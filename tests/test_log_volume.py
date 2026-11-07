"""#146: log lines worth reading are not buried (live, 6 Nov: 8,298 of
10,149 lines a day were httpx's own, 686 the health check)."""

import asyncio
import json
import logging
import logging.config
from pathlib import Path

import httpx

import server.auth_logging as auth_logging
from server.home import HomeDiscovery
from server.store import Store


def access(path, status):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", status),
        None,
    )


def test_a_successful_health_check_is_left_out_of_the_access_log():
    keep = auth_logging.HealthCheckFilter().filter
    assert not keep(access("/api/health", 200))
    assert keep(access("/api/health", 503))
    assert keep(access("/api/v1/library", 200))
    assert keep(access("/api/healthy", 200))


def test_the_logging_config_quiets_httpx_and_filters_the_health_check():
    config = json.loads(
        (Path(__file__).parent.parent / "server" / "logging.json").read_text()
    )
    assert config["loggers"]["httpx"]["level"] == "WARNING"
    assert config["loggers"]["httpcore"]["level"] == "WARNING"
    assert config["handlers"]["access"]["filters"][0] == "health"
    logging.config.dictConfig({**config, "disable_existing_loggers": False})
    try:
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    finally:
        logging.getLogger("httpx").setLevel(logging.NOTSET)
        logging.getLogger("httpcore").setLevel(logging.NOTSET)


def test_a_chart_timeout_is_one_line_without_a_traceback(tmp_path, caplog):
    class Catalogue:
        pass

    home = HomeDiscovery(Store(tmp_path), Catalogue())

    async def timeout(client, region):
        raise httpx.ReadTimeout("slow")

    home.match_chart = timeout
    with caplog.at_level(logging.WARNING, logger="pig_music.home"):
        asyncio.run(home.run_chart(None, "au"))
    (record,) = [r for r in caplog.records if "chart failed" in r.getMessage()]
    assert record.getMessage() == "Building the au chart failed (ReadTimeout)"
    assert not record.exc_info


def test_the_warmer_does_not_refetch_a_home_feed_within_the_day(tmp_path, monkeypatch):
    """#145: Home feeds were kept an hour and renewed by the warmer 20
    minutes early, so every seed was fetched every hour (195 calls a day)."""
    import server.home as home_module

    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"payload": {"jspf": {}}})

    home = HomeDiscovery(Store(tmp_path), None)
    now = [1_000_000.0]
    monkeypatch.setattr(home_module.time, "time", lambda: now[0])

    async def run():
        token = home_module.warming.set(True)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                await home.feed(c, "explore/lb-radio", prompt="tag:rock")
                for hours in (1, 2, 6, 12, 23):
                    now[0] = 1_000_000.0 + hours * 3600
                    await home.feed(c, "explore/lb-radio", prompt="tag:rock")
        finally:
            home_module.warming.reset(token)

    asyncio.run(run())
    assert len(calls) == 1
