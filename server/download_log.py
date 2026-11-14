"""A download's story in plain words (#197), from the steps its job kept.

Built from the job's saved timings (server.downloader.Timings): what was
searched, the copies found, each copy tried and why it was refused, the
checks, any conversion and where the file went. Never names a Soulseek
peer or shows a claim token; the same lines serve the owner and members.
"""

from .providers import REGISTRY
from .quality import LABELS


def _source(value):
    spec = REGISTRY.get(value or "")
    return spec.name if spec else (value or "a source")


def _profile(value):
    return LABELS.get(value or "", value or "")


def _copies(found):
    parts = []
    for copy in found or []:
        size = copy.get("mb")
        parts.append(
            " ".join(
                str(p)
                for p in (copy.get("format"), f"{size:.0f} MB" if size else None)
                if p
            )
        )
    return ", ".join(p for p in parts if p)


def _line(step):
    name, outcome = step.get("step"), step.get("outcome", "ok")
    ok = outcome == "ok"
    source, profile = _source(step.get("source")), _profile(step.get("profile"))
    if name == "resolve":
        text = "Matched the song to its album edition"
    elif name == "search":
        count = step.get("candidates")
        if ok:
            text = f"Searched {source} for {profile}: " + (
                f"{count} matching cop{'y' if count == 1 else 'ies'}"
                if count
                else "no matching copy"
            )
            copies = _copies(step.get("found"))
            if copies:
                text += f" (first: {copies})"
        else:
            text = f"Searched {source} for {profile}: {outcome}"
    elif name == "transfer":
        size = step.get("bytes")
        text = f"Downloaded a copy from {source}" + (
            f" ({size / 1_000_000:.1f} MB)" if size else ""
        )
        if not ok:
            text = f"The download from {source} did not finish: {outcome}"
    elif name == "validate":
        what = "the lossless original" if profile == LABELS["lossless"] else "the file"
        text = f"Checked {what}: " + ("passed" if ok else f"refused, {outcome}")
    elif name == "convert":
        text = f"Converted the lossless original to {profile}" + (
            "" if ok else f": {outcome}"
        )
    elif name == "tag":
        text = "Wrote the song's details and cover into the file"
    elif name == "decode":
        text = "Played the whole file through: " + ("no errors" if ok else outcome)
    elif name in {"hash", "commit"}:
        return None
    else:
        text = f"{name}: {outcome}"
    return {"text": text, "ok": ok, "seconds": step.get("seconds")}


def story(payload, status):
    """The lines of one job's log, oldest first, ending with its outcome."""
    timings = payload.get("timings") or {}
    lines = [line for step in timings.get("steps") or [] if (line := _line(step))]
    if timings.get("queued_seconds") is not None:
        lines.insert(
            0,
            {
                "text": "Waited in the queue",
                "ok": True,
                "seconds": timings["queued_seconds"],
            },
        )
    if status == "failed":
        lines.append({"text": payload.get("error") or "Failed", "ok": False})
    elif status in {"completed", "downloaded"}:
        where = " / ".join(
            str(payload[k]) for k in ("album_artist", "album") if payload.get(k)
        )
        if where:
            lines.append({"text": f"Filed it in the library under {where}", "ok": True})
        lines.append({"text": "Done", "ok": True})
    elif status == "cancelled":
        lines.append({"text": "Cancelled", "ok": False})
    return lines
