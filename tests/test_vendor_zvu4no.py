"""zvu4no's search page as served on 5 Nov 2026: links moved to
//hybrid.<host>/mp3-download/ and the old end-of-list markers went, so the
parser read no results at all (Mark's remote plays: "zvu4no no match")."""

import httpx

from server.vendor import zvu4no

BLOCK = """<div class="f-table"> <div class="f-row"> <div class="f-cell f-max"><div class="c-img"><img src="//img.zvu4it.org/img/{n}.jpg" alt="{artist}"></div></div> <div class="f-cell"> <div class="f-row"> <div class="f-cell"> <div class="artist-name"><a href="/tracks/{artist}">{artist}</a></div> <div class="track-name">{title}</div> </div> </div> <div class="f-row"> <div class="f-cell"> <div class="play-container play-pause"> <div class="play-text">listen</div> </div> <div class="download-container"> <a class="mp3" href="//hybrid.zvu4it.org/mp3-download/{n}/abc/{artist}%20-%20{title}.mp3" title="download" target="_blank"> <div class="download-text">download</div> </a> </div> </div> </div> </div> <div class="f-cell f-max-add"> <div class="time-container"> <div class="time-text">{time}</div> </div> </div> </div> </div>"""

PAGE = (
    "<html><body><main>"
    + BLOCK.format(n=1, artist="Coldplay", title="Yellow", time="04:29")
    + BLOCK.format(n=2, artist="Coldplay", title="Yellow (Live)", time="05:02")
    + "</main><footer>end</footer></body></html>"
)


def test_the_current_page_layout_gives_every_result(monkeypatch):
    def get(url, **kwargs):
        return httpx.Response(200, text=PAGE, request=httpx.Request("GET", url))

    monkeypatch.setattr(zvu4no.httpx, "get", get)
    results = zvu4no.search_zvu4no("Coldplay Yellow", 10)
    assert [(r["channel"], r["title"]) for r in results] == [
        ("Coldplay", "Yellow"),
        ("Coldplay", "Yellow (Live)"),
    ]
    first = results[0]
    assert first["source_url"].startswith("https://hybrid.zvu4it.org/mp3-download/")
    assert zvu4no.is_zvu4no_url(first["source_url"])
    assert first["duration"] == "4:29"


def test_links_off_the_provider_host_are_ignored(monkeypatch):
    page = PAGE.replace("//hybrid.zvu4it.org/", "//evil.example.com/")

    def get(url, **kwargs):
        return httpx.Response(200, text=page, request=httpx.Request("GET", url))

    monkeypatch.setattr(zvu4no.httpx, "get", get)
    assert zvu4no.search_zvu4no("Coldplay Yellow", 10) == []
