"""
MusicGrabber - FreeMp3Cloud Source

MP3 search portal (g2.freemp3cloud.com) backed by the meln.top CDN. The site
is server-rendered ASP.NET: a search is a form POST that needs a session cookie
plus an antiforgery token harvested from the landing page first. The HTML it
returns lists tracks with artist, title, duration, a direct cdnm.meln.top MP3
download link, and  -  crucially  -  an "HQ" label on the good ones.

Quality is the whole story here: HQ-tagged results are 320 kbps (some 256) and
even ship embedded cover art; the un-tagged ones are a sad 128 kbps. We score
HQ at the top lossy tier and demote the rest so they only ever win when
nothing better exists.

The download URL carries its own session_key + hash, so it works later from the
download thread with no cookie needed; only the search step needs the session.
"""

import hashlib
import html
import re

import httpx

from .compat import TIMEOUT_FREEMP3CLOUD_SEARCH
from .compat import score_search_result_with_breakdown, parse_duration

_BASE_URL = "https://g2.freemp3cloud.com"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Referer": _BASE_URL + "/",
}

# Each result lives in a <div class="play-item"> ... </div>. We split on the
# opening tag and parse the fields out of each chunk individually.
_RE_TOKEN  = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_RE_ARTIST = re.compile(r'class="s-artist">(.*?)</div>', re.DOTALL)
_RE_TITLE  = re.compile(r'class="s-title">(.*?)</div>', re.DOTALL)
_RE_TIME   = re.compile(r'class="s-time">(.*?)</div>', re.DOTALL)
_RE_HREF   = re.compile(r'class="downl">\s*<a href="(https://[^"]+\.mp3[^"]*)"', re.DOTALL)
_RE_HQ     = re.compile(r'class="s-hq"')
_RE_TAGS   = re.compile(r"<[^>]+>")


def _clean_text(value: str) -> str:
    value = _RE_TAGS.sub("", value or "")
    return html.unescape(value).strip()


def _duration_to_secs(dur: str) -> int:
    try:
        parts = dur.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, AttributeError):
        pass
    return 0


def search_freemp3cloud(query: str, limit: int) -> list[dict]:
    """Search FreeMp3Cloud and return normalised result dicts.

    On any error returns [] rather than raising  -  a flaky source should
    degrade gracefully, not sink the whole multi-source search.
    """
    try:
        with httpx.Client(
            headers=_HEADERS,
            timeout=TIMEOUT_FREEMP3CLOUD_SEARCH,
            follow_redirects=True,
        ) as client:
            # Step 1: landing page hands us the session cookie + antiforgery token.
            # g2 currently 301s to a2 (load-balanced subdomain, changes without
            # warning), and a POST to the pre-redirect URL gets silently downgraded
            # to a GET by the redirect follow, so post to wherever we actually
            # landed rather than back to _BASE_URL.
            landing = client.get(_BASE_URL + "/")
            landing.raise_for_status()
            token_m = _RE_TOKEN.search(landing.text)
            if not token_m:
                print("freemp3cloud: no antiforgery token on landing page")
                return []

            # Step 2: post the search; cookie travels on the client automatically.
            resp = client.post(
                str(landing.url),
                data={
                    "searchSong": query,
                    "__RequestVerificationToken": token_m.group(1),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()

        results = []
        seen_urls = set()
        for block in resp.text.split('<div class="play-item">')[1:]:
            artist_m = _RE_ARTIST.search(block)
            title_m  = _RE_TITLE.search(block)
            href_m   = _RE_HREF.search(block)
            if not (title_m and href_m):
                continue

            href = html.unescape(href_m.group(1))
            if href in seen_urls:
                continue
            seen_urls.add(href)

            artist = _clean_text(artist_m.group(1)) if artist_m else ""
            title  = _clean_text(title_m.group(1))
            time_m = _RE_TIME.search(block)
            dur    = _clean_text(time_m.group(1)) if time_m else ""
            is_hq  = bool(_RE_HQ.search(block))

            duration_secs = _duration_to_secs(dur)
            combined_title = f"{artist} - {title}" if artist else title
            relevance_score, score_breakdown = score_search_result_with_breakdown(
                combined_title, artist, query,
                duration_seconds=duration_secs or None,
                view_count=None,
            )
            # HQ results are genuine 320 kbps, so they get the same top lossy
            # bonus as other 320kbps sources. The un-tagged ones are 128 kbps;
            # give them a token +5 so they only surface when nothing better turned up.
            if is_hq:
                relevance_score += 30
                score_breakdown.append("source_quality=+30 (HQ)")
                quality_label = "320kbps"
            else:
                relevance_score += 5
                score_breakdown.append("source_quality=+5 (non-HQ)")
                quality_label = "128kbps"

            video_id = "fmc_" + hashlib.md5(href.encode()).hexdigest()[:12]

            results.append({
                "video_id": video_id,
                "title": title,
                "channel": artist,
                "duration": parse_duration(duration_secs) if duration_secs else dur,
                "thumbnail": "",
                "is_playlist": False,
                "video_count": None,
                "source": "freemp3cloud",
                "source_url": href,
                "quality": quality_label,
                "relevance_score": relevance_score,
                "score_breakdown": score_breakdown,
                "slskd_username": None,
                "slskd_filename": None,
                "slskd_size": None,
            })

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        return results

    except Exception as e:
        raise RuntimeError("FreeMp3Cloud search unavailable") from e
