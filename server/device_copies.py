"""AAC device-copy cache. Originals and personal authorization remain authoritative."""

import asyncio
from collections import OrderedDict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from .media import track_file


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(512 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_stamp(stat):
    """Content/identity metadata; reading may legitimately change access time."""
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


# Copies converted at once (WATCH1: one at a time, 9-26 s each, delivered
# 1.2 songs a minute). Each FFmpeg AAC encode is one thread and little memory.
WORKERS = 2


@lru_cache(maxsize=1)
def encoder_version():
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=10, check=True
        )
        if not result.stdout.splitlines():
            raise ValueError("AAC encoder unavailable")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("AAC encoder unavailable") from exc
    return hashlib.sha256(
        result.stdout.splitlines()[0] + b"aac-256k-44100-stereo-artwork-v2"
    ).hexdigest()


class DeviceCopies:
    def __init__(self, store):
        self.store = store
        self.root = store.directory / "device-copies"
        self.root.mkdir(exist_ok=True)
        self.quota = max(
            32 * 1024 * 1024,
            int(os.environ.get("PIG_DEVICE_COPY_CACHE_BYTES", 5 * 1024**3)),
        )
        self.worker = None
        self.stopping = False
        self.leases = {}
        self.hashes = OrderedDict()
        self.lock = threading.RLock()
        with store.connect() as conn:
            for key, raw in conn.execute("SELECT id,payload FROM device_copies"):
                job = json.loads(raw)
                if job["state"] == "preparing":
                    job["state"] = "queued"
                    conn.execute(
                        "UPDATE device_copies SET payload=? WHERE id=?",
                        (json.dumps(job), key),
                    )
        for path in self.root.glob("*.partial.m4a"):
            path.unlink(missing_ok=True)

    def source(self, track_id, quality):
        path = track_file(self.store, track_id)
        stat = path.stat()
        if not 0 < stat.st_size <= 2 * 1024**3:
            raise ValueError("This source is outside the supported device-copy size.")
        signature = (str(path), *source_stamp(stat))
        with self.lock:
            digest = self.hashes.get(signature)
        if digest is None:
            digest = checksum(path)
            if source_stamp(path.stat()) != source_stamp(stat):
                raise ValueError("The source changed. Request it again after scanning.")
            with self.lock:
                self.hashes[signature] = digest
                self.hashes.move_to_end(signature)
                while len(self.hashes) > 256:
                    self.hashes.popitem(last=False)
        version = encoder_version() if quality == "aac" else "original-v1"
        key = hashlib.sha256((digest + quality + version).encode()).hexdigest()
        return path, digest, stat, key

    def read(self, key):
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM device_copies WHERE id=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, job):
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO device_copies VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                (job["id"], json.dumps(job)),
            )

    def manifest(self, track_id, quality, create=False):
        path, sha, stat, key = self.source(track_id, quality)
        if quality == "original":
            return {
                "state": "ready",
                "quality": quality,
                "sha256": sha,
                "bytes": stat.st_size,
                "extension": path.suffix.lstrip(".").lower(),
                "source_sha256": sha,
                "key": key,
            }
        with self.lock:
            job = self.read(key)
            output = self.root / (key + ".m4a")
            if (
                job
                and job["state"] == "ready"
                and (not output.is_file() or output.stat().st_size != job["bytes"])
            ):
                job = None
            if create and (job is None or job["state"] == "failed"):
                job = {
                    "id": key,
                    "state": "queued",
                    "source": str(path),
                    "source_sha256": sha,
                    "touched": time.time(),
                }
                self.save(job)
            if job and job["state"] == "ready":
                job["touched"] = time.time()
                self.save(job)
            return {
                "key": key,
                "quality": quality,
                "state": job["state"] if job else "not_requested",
                "sha256": job.get("sha256") if job else None,
                "bytes": job.get("bytes", 0) if job else 0,
                "extension": "m4a",
                "source_sha256": sha,
            }

    def kick(self):
        if self.stopping or self.worker and not self.worker.done():
            return
        self.worker = asyncio.create_task(self.run())

    def claim(self):
        """The next queued copy, marked as preparing so no other worker takes
        it."""
        with self.lock, self.store.connect() as conn:
            for (raw,) in conn.execute("SELECT payload FROM device_copies"):
                job = json.loads(raw)
                if job["state"] == "queued":
                    job["state"] = "preparing"
                    conn.execute(
                        "UPDATE device_copies SET payload=? WHERE id=?",
                        (json.dumps(job), job["id"]),
                    )
                    return job
        return None

    async def command(self, *arguments):
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *arguments,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(1200):
                if await process.wait() != 0:
                    raise ValueError("AAC conversion failed")
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise

    @staticmethod
    def probe(source):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
            capture_output=True,
            timeout=20,
            check=True,
        )
        return json.loads(result.stdout).get("streams", [])

    async def run(self):
        await asyncio.gather(*(self.convert() for _ in range(WORKERS)))

    async def convert(self):
        while not self.stopping:
            # Publishing a download (validating its audio, importing it) has
            # priority; a download still transferring does not hold copies up.
            active = await asyncio.to_thread(self.store.queue)
            if any(
                item.get("status") in {"validating", "importing"}
                for item in active.get("items", [])
            ):
                await asyncio.sleep(2)
                continue
            job = await asyncio.to_thread(self.claim)
            if not job:
                return
            key = job["id"]
            temporary = self.root / (key + ".partial.m4a")
            output = self.root / (key + ".m4a")
            try:
                source = Path(job["source"])
                streams = await asyncio.to_thread(self.probe, source)
                audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
                compatible = (
                    source.suffix.lower() in {".m4a", ".mp4"}
                    and audio.get("codec_name") == "aac"
                    and audio.get("profile") == "LC"
                    and int(audio.get("sample_rate") or 0) == 44100
                    and int(audio.get("channels") or 0) == 2
                    and 0 < int(audio.get("bit_rate") or 0) <= 256000
                )
                artwork = next(
                    (
                        s
                        for s in streams
                        if s.get("disposition", {}).get("attached_pic")
                        and s.get("codec_name") in {"mjpeg", "png"}
                    ),
                    None,
                )
                arguments = [
                    "-v",
                    "error",
                    "-nostdin",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-map_metadata",
                    "0",
                ]
                if artwork is not None:
                    arguments += [
                        "-map",
                        "0:" + str(artwork["index"]),
                        "-c:v",
                        "copy",
                        "-disposition:v:0",
                        "attached_pic",
                    ]
                else:
                    arguments += ["-vn"]
                arguments += (
                    ["-c:a", "copy"]
                    if compatible
                    else ["-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2"]
                )
                arguments += ["-movflags", "+faststart", str(temporary)]
                await self.command(*arguments)
                job["reused_audio"] = compatible
                job["embedded_artwork"] = artwork is not None
                await self.command(
                    "-v", "error", "-nostdin", "-i", str(temporary), "-f", "null", "-"
                )
                if await asyncio.to_thread(checksum, source) != job["source_sha256"]:
                    raise ValueError("Source changed")
                size = temporary.stat().st_size
                sha = await asyncio.to_thread(checksum, temporary)
                await asyncio.to_thread(self.prune, size)
                temporary.replace(output)
                job.update(state="ready", bytes=size, sha256=sha, touched=time.time())
                await asyncio.to_thread(self.save, job)
            except asyncio.CancelledError:
                job["state"] = "queued"
                await asyncio.to_thread(self.save, job)
                raise
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                job["state"] = "failed"
                await asyncio.to_thread(self.save, job)
            finally:
                temporary.unlink(missing_ok=True)

    def prune(self, incoming):
        with self.lock, self.store.connect() as conn:
            jobs = sorted(
                (
                    json.loads(raw)
                    for (raw,) in conn.execute("SELECT payload FROM device_copies")
                ),
                key=lambda j: j.get("touched", 0),
            )
            used = sum(j.get("bytes", 0) for j in jobs if j["state"] == "ready")
            for job in jobs:
                if used + incoming <= self.quota:
                    break
                if job["state"] == "ready" and not self.leases.get(job["id"]):
                    (self.root / (job["id"] + ".m4a")).unlink(missing_ok=True)
                    conn.execute("DELETE FROM device_copies WHERE id=?", (job["id"],))
                    used -= job["bytes"]
            if used + incoming > self.quota:
                raise ValueError("Device-copy cache is full")

    def acquire(self, track_id, quality):
        with self.lock:
            value = self.manifest(track_id, quality)
            if value["state"] != "ready":
                raise LookupError("Copy is not ready")
            path = (
                track_file(self.store, track_id)
                if quality == "original"
                else self.root / (value["key"] + ".m4a")
            )
            self.leases[value["key"]] = self.leases.get(value["key"], 0) + 1
            return path, value

    def release(self, key):
        with self.lock:
            self.leases[key] = max(0, self.leases.get(key, 0) - 1)
            if not self.leases[key]:
                del self.leases[key]

    async def close(self):
        self.stopping = True
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
