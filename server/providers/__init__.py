"""Provider capabilities shared by settings and acquisition.

Only implemented sources are registered. Adding an entry never enables a source
in saved settings, and declared capability never replaces file validation.
"""

from dataclasses import dataclass
from typing import Callable

from ..vendor.freemp3cloud import search_freemp3cloud
from ..vendor.jiosaavn import search_jiosaavn
from ..vendor.zvu4no import search_zvu4no


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    profiles: frozenset[str]
    extensions: frozenset[str]
    default_extension: str | None = None
    download_hosts: tuple[str, ...] = ()
    search: Callable | None = None
    requires_connection: bool = False
    adapter: bool = False

    def extension(self, candidate):
        value = candidate.get("extension") or self.default_extension
        if value not in self.extensions:
            raise ValueError("Source returned an unsupported audio container")
        return value


REGISTRY = {
    "soundcloud": ProviderSpec(
        "soundcloud",
        "SoundCloud",
        frozenset({"native_standard", "mp3_128", "mp3_320", "aac_256", "lossless"}),
        frozenset({".mp3", ".m4a", ".opus", ".ogg", ".flac"}),
        adapter=True,
    ),
    "monochrome": ProviderSpec(
        "monochrome",
        "Monochrome",
        frozenset({"lossless", "native_standard"}),
        frozenset({".flac"}),
        ".flac",
        adapter=True,
    ),
    "soulseek": ProviderSpec(
        "soulseek",
        "Soulseek",
        frozenset({"mp3_128", "mp3_320", "aac_256", "lossless", "native_standard"}),
        frozenset({".mp3", ".m4a", ".flac", ".opus", ".ogg"}),
        requires_connection=True,
    ),
    "freemp3cloud": ProviderSpec(
        "freemp3cloud",
        "FreeMp3Cloud",
        frozenset({"mp3_128", "mp3_320", "native_standard"}),
        frozenset({".mp3"}),
        ".mp3",
        ("meln.top", "freemp3cloud.com"),
        search_freemp3cloud,
    ),
    "zvu4no": ProviderSpec(
        "zvu4no",
        "zvu4no",
        frozenset({"mp3_128", "mp3_320", "native_standard"}),
        frozenset({".mp3"}),
        ".mp3",
        ("zvu4it.org",),
        search_zvu4no,
    ),
    "jiosaavn": ProviderSpec(
        "jiosaavn",
        "JioSaavn",
        frozenset({"aac_256", "native_standard"}),
        frozenset({".m4a"}),
        ".m4a",
        ("saavncdn.com",),
        search_jiosaavn,
    ),
}


def supports(source, profile):
    spec = REGISTRY.get(source)
    return spec is not None and profile in spec.profiles


SEARCH_PROVIDERS = {
    source: spec.search for source, spec in REGISTRY.items() if spec.search is not None
}
