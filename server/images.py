"""Re-encoding and atomic writes shared by every artwork store (#170).

Re-encoding keeps only pixels (no metadata or embedded payloads) and turns
the picture upright first; the pixel limit refuses decompression bombs
before decoding.
"""

import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps


def reencode(content, box, *, mode="RGB", format="JPEG", limit=20_000_000, **options):
    """`content` decoded, upright, fitted inside `box` and encoded again."""
    with Image.open(io.BytesIO(content)) as picture:
        if picture.width * picture.height > limit:
            raise ValueError("Artwork too large")
        picture = ImageOps.exif_transpose(picture).convert(mode)
        picture.thumbnail(box)
        output = io.BytesIO()
        picture.save(output, format, **options)
    return output.getvalue()


def write_atomically(data, target, durable=False):
    """Replace `target` with `data` in one step; a failure leaves it as it was."""
    target = Path(target)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + target.stem + "-", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_image(content, target, box, **options):
    write_atomically(reencode(content, box, **options), target)
