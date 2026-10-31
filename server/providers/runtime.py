"""Shared source orchestration used by acquisition and owner diagnostics."""

import time

from .contracts import Context, ProviderError
from .extractor import Extractor
from .health import Health
from .monochrome import Monochrome


class Runtime:
    def __init__(self, store):
        self.store = store
        self.health = Health()
        # YouTube was removed as a source (D-60); its saved session goes too.
        (store.directory / "youtube-cookies.txt").unlink(missing_ok=True)
        self.adapters = {"soundcloud": Extractor("soundcloud")}
        # Invalid operator config should leave unrelated sources usable.
        try:
            self.adapters["monochrome"] = Monochrome()
        except ProviderError:
            pass

    def configured(self, source):
        if source == "soundcloud":
            import importlib.util

            return importlib.util.find_spec("yt_dlp") is not None
        if source == "monochrome":
            return source in self.adapters
        if source == "soulseek":
            return (self.store.directory / "slskd-connection.json").is_file()
        return True

    def search(self, source, target, context):
        if not self.configured(source):
            raise ProviderError("not_configured")
        with self.health.operation(source):
            try:
                results = self.adapters[source].search(target, context)
            except ProviderError as exc:
                self.health.outcome(source, error=exc)
                raise
            self.health.outcome(source)
            return results

    def transfer(self, source, candidate, path, context):
        with self.health.operation(source):
            try:
                return self.adapters[source].transfer(candidate, path, context)
            except ProviderError as exc:
                self.health.outcome(source, error=exc, transfer=True)
                raise

    def statuses(self):
        from . import REGISTRY

        settings = self.store.download_settings()
        return [
            {
                "id": source,
                "name": spec.name,
                "profiles": sorted(spec.profiles),
                "configured": self.configured(source),
                "enabled": source in settings["sources"],
                **self.health.snapshot(source),
            }
            for source, spec in REGISTRY.items()
        ]

    def check(self, source):
        from . import REGISTRY

        if source not in REGISTRY:
            raise KeyError(source)
        if not self.configured(source):
            raise ProviderError("not_configured")
        with self.health.operation(source):
            try:
                if source in self.adapters:
                    self.adapters[source].check(
                        Context(
                            "native_standard",
                            deadline=time.monotonic() + 30,
                            max_bytes=2_000_000,
                        )
                    )
                elif source == "soulseek":
                    from ..soulseek import Soulseek

                    if not Soulseek(self.store).health().get("connected"):
                        raise ProviderError()
                else:
                    # The retained parsers share guarded transport and a cancellable process.
                    from .legacy import search

                    search(
                        source,
                        "music",
                        Context("native_standard", deadline=time.monotonic() + 30),
                        1,
                    )
                self.health.outcome(source)
            except ProviderError as exc:
                self.health.outcome(source, error=exc)
                raise
            except Exception as exc:
                error = ProviderError()
                self.health.outcome(source, error=error)
                raise error from exc
        return next(value for value in self.statuses() if value["id"] == source)
