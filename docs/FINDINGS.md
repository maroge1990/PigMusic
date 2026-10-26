# Open review findings

Medium and low findings left after the second Codex review round of a batch (see "Independent review" in [AGENTS.md](../AGENTS.md)). The owner prioritises them; move one to the blueprint roadmap when it is scheduled, and delete it here when it is fixed.

| Found | Batch | Severity | Finding | Where |
| --- | --- | --- | --- | --- |
| 26 Oct 2026 | PLAY4 (de06009, 20afe9b), Codex round 2 | Medium | A stand-in is not looked for in an edition retired within the last 6 h with no lease, though `Mixes.entry(playback=True)` still plays such an edition. A rotated song whose only compatible copy is in an edition retired minutes ago therefore stays unavailable and is passed over. Fix: also select `retired_at > now - 6 h` in the candidate query (the final `playback()` check stays), with a regression for a candidate retired a minute ago. Reproduced by Codex in memory; not on a device | `server/mixes.py` `Mixes.stand_in` candidate query |
