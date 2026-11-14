"""The shared image save helper (#170)."""

import io

import pytest
from PIL import Image

from server.images import reencode, save_image, write_atomically


def picture(size=(400, 200), orientation=None, colour="red"):
    image = Image.new("RGB", size, colour)
    stream = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(stream, "JPEG", exif=exif)
    else:
        image.save(stream, "PNG")
    return stream.getvalue()


def test_a_rotated_photo_is_saved_upright_and_fitted(tmp_path):
    target = tmp_path / "cover.jpg"
    # Orientation 6: stored 400x200, shown 200x400.
    save_image(picture(orientation=6), target, (100, 100), quality=84)
    with Image.open(target) as saved:
        assert saved.size == (50, 100)
        assert saved.format == "JPEG"
        assert 0x0112 not in saved.getexif()


def test_too_many_pixels_are_refused_before_decoding(tmp_path):
    with pytest.raises(ValueError, match="too large"):
        reencode(picture((300, 300)), (100, 100), limit=80_000)


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(tmp_path):
    target = tmp_path / "cover.jpg"
    target.write_bytes(b"old")
    with pytest.raises(OSError):
        save_image(b"not an image", target, (100, 100))
    write_atomically(b"new", target, durable=True)
    assert target.read_bytes() == b"new"
    assert [p.name for p in tmp_path.iterdir()] == ["cover.jpg"]


def test_transparency_survives_as_png(tmp_path):
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    data = reencode(stream.getvalue(), (800, 310), mode="RGBA", format="PNG")
    with Image.open(io.BytesIO(data)) as saved:
        assert saved.mode == "RGBA" and saved.getpixel((0, 0))[3] == 0
