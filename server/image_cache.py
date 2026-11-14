"""Local content-addressed presentation images; no provider calls."""

import io
import re
import threading

from PIL import Image, ImageFilter

from .images import write_atomically

SIZES = {128, 256, 512, 1024, 1600}
LOCK = threading.RLock()


def public_image(store, target):
    if (
        not target
        or not target.is_file()
        or target.is_symlink()
        or target.parent.resolve() != store.cover_dir.resolve()
    ):
        return None
    if re.fullmatch(r"[0-9a-f]{64}", target.stem):
        return store.covers.path(target.stem)
    key = "image-public:" + target.name
    sha = store.read_document(key).get("sha")
    if not store.covers.path(sha):
        try:
            sha = store.covers.save(target.read_bytes())
        except (OSError, ValueError, Image.DecompressionBombError):
            # Display caches must never prevent the music projection from loading.
            return None
        store.write_document(key, {"sha": sha})
    return store.covers.path(sha)


def variant(target, width, soft=False):
    if width not in SIZES:
        raise ValueError("Unsupported image size")
    directory = target.parent / "variants"
    directory.mkdir(exist_ok=True)
    output = directory / f"{target.stem}-v1-{width}{'-soft' if soft else ''}.webp"
    with LOCK:
        if output.is_file() and not output.is_symlink():
            return output
        with Image.open(target) as image:
            image = image.convert("RGB")
            image.thumbnail((width, width))
            if soft:
                image = image.filter(ImageFilter.GaussianBlur(max(2, width / 32)))
            buffer = io.BytesIO()
            image.save(buffer, "WEBP", quality=80)
        write_atomically(buffer.getvalue(), output)
    return output
