# Extracted Music Grabber components

`freemp3cloud.py`, `zvu4no.py`, `jiosaavn.py` and `audio_probe.py` originate from Music Grabber, GitLab `g33kphr33k/musicgrabber`, commit `7fd57573a47ee9d1de0b8331964f294abc68799a` (installed 4.3.0 source comparison recorded in discovery).

The upstream Unlicense is retained in `MUSICGRABBER-LICENSE`. Source parser imports have been redirected to a small local compatibility module. Search errors are raised instead of suppressed so the acquisition worker can report source failures. Original download routines are retained for reference but are not used: PigMusic owns bounded transfers, URL restrictions, cancellation, verification, tagging and publication. No upstream database or user/session code is imported.

`compat.py` is PigMusic code. It provides duration formatting, source timeouts and a neutral search scorer; candidate identity/version matching and file-quality gates are handled by the PigMusic worker.

The JioSaavn search parser also retains upstream album and ISRC fields to check release context before remote playback.

The audio probe reads PigMusic source tags as well as legacy Music Grabber provenance. Native bitrate fallback and stream-copy remux orchestration are application-owned under `server/audio.py`.

## Extractor runtime

PigMusic uses the official PyPI Python distributions `yt-dlp==2026.8.19` (Unlicense) and `yt-dlp-ejs==0.8.0` (Unlicense AND MIT AND ISC), with their pinned default dependencies. Their distribution metadata retains upstream licensing and notices. Upstream: https://github.com/yt-dlp/yt-dlp and https://github.com/yt-dlp/ejs. Only the SoundCloud adapter uses yt-dlp; the image carries no JavaScript runtime for it (YouTube was removed as a source, D-60). Extractor orchestration, network handler, identity rules and source health are PigMusic code. No Music Grabber YouTube/Monochrome adapter code was copied into the new adapters.
