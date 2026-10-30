# PigMusic: agent guide

Private household music manager: FastAPI + embedded slskd in one Unraid container, React/Vite web client, Swift iPhone/iPad/Watch apps. Open work is in [GitHub Issues](https://github.com/maroge1990/PigMusic/issues), ordered by [ROADMAP.md](ROADMAP.md). `docs/archive/` is frozen history, not instructions.

## Map of the docs

| File | Holds |
| --- | --- |
| [ROADMAP.md](ROADMAP.md) | Current state (overwritten each session), Now / Next / Later by issue |
| [docs/PRODUCT.md](docs/PRODUCT.md) | What PigMusic does and the rules it keeps; known limits; what is set aside |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) + [docs/architecture/](docs/architecture/) | How it works; what to know before touching mixes, playback, library identity, Watch sync |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Mark's decisions, numbered and dated; appended, never rewritten |
| [docs/DESIGN.md](docs/DESIGN.md) | Layout conventions and brand |
| [docs/TESTING.md](docs/TESTING.md) | Test tiers T1–T5, who runs what, release and device checklists, diagnostics tools |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploy, release, rollback, internet access, security model, troubleshooting |
| [api/openapi.v1.json](api/openapi.v1.json) | The API contract (Swift copy must match) |
| [.env.example](.env.example), `.private/` | Every environment variable; real household values live only in the gitignored `.private/` |

## Setup (macOS)

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt -r requirements-dev.txt
(cd client && npm ci)
```

Node 24 and FFmpeg are also required.

## Run

```sh
(cd client && npm run build)
PIG_MUSIC_DATA=.localdata/dev PIG_MUSIC_PRELOAD=false .venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8765
```

The preview config `pig-music-dev` attaches to port 8765. A fresh install shows the first-run admin form; the installation code is printed to the server console. Use generated test credentials only. iPhone: open `swift/PigMusic.xcodeproj`, scheme **PigMusic** (embeds **PigMusicWatch**); Debug launch argument `--demo` gives a synthetic offline library.

## Test, cheapest first

Tiers and who runs what: [docs/TESTING.md](docs/TESTING.md). Every functional change has a test that **fails on the old code**; show it.

1. **Every change (~4 min):** `.venv/bin/python -m pytest tests -q -p no:cacheprovider` and `(cd client && npm test)`. Web UI changes: `(cd client && npm run build && npm run test:e2e)` (Playwright against a mocked API; first run `npx playwright install chromium`).
2. **Before commit (~1 min more):**
   ```sh
   .venv/bin/python -m black --check server tests scripts --workers 1
   (cd client && npm run format:check && npm run build)
   .venv/bin/python -m scripts.export_openapi --check
   .venv/bin/python scripts/sync_native_brand.py --check && .venv/bin/python scripts/generate_brand_tokens.py --check
   swift test --package-path swift/PigKit
   ```
   For the two Swift contract tests, run `.venv/bin/python -m scripts.serve_contract_fixture` in another terminal and add `PIG_CONTRACT_SERVER=http://127.0.0.1:8781`. When Swift changes, also build: `xcodebuild -quiet -project swift/PigMusic.xcodeproj -scheme PigMusic -destination 'generic/platform=iOS Simulator' -skipPackagePluginValidation CODE_SIGNING_ALLOWED=NO build` (and `-scheme PigMusicWatch` with `generic/platform=watchOS Simulator`).
3. **CI on push:** `.github/workflows/container.yml` runs dependency audits, the backend suite in the exact Linux image, 1 GB artwork/delivery/sync scale gates and web checks; `native.yml` runs the brand, contract, PigKit and unsigned app builds.

Tests mark provider rate limits off by default; `@pytest.mark.real_rate_limits` (see `tests/test_rate_limits.py`) keeps them.

## Work in batches

Batch by area (D-50): group the open issues that touch the same code and risk (say, the idle-CPU loops #63/#74/#79, or the Watch delivery issues) and carry them as one batch. The fixed costs are paid once per batch: two Codex rounds, one push, one CI build. Live releases bundle a few batches.
- Inside a batch each fix stays its own commit (one logical change, `Refs #n`) with its own test that fails on the old code. Run the targeted tests per fix and the full gates once before the batch's first Codex round and again before the push.
- Keep a batch to one platform where possible: a Swift change adds Mac runs, and a mixed diff gives Codex more to misread.
- Don't batch unrelated areas to save time, and keep a fix whose live effect must be confirmed (for example the event-loop gates in #16) in a release where it can be told apart.
- Name the batch, its issues and its first commit in the Codex focus; `--base` is the commit before the batch.

## Independent review

Once your checks pass, get an adversarial review by a different agent before pushing any change that either:
- touches concurrency, session or object lifetimes, playback, sync, file moves/deletion (Tidy, Trash, organisation, export), auth, signing or security; or
- changes more than about 3 non-test source files or 150 non-test lines.

A plain review only reads the diff; ask for an adversarial one. If you can run it yourself (Claude Code with the Codex plugin: its `codex-companion.mjs adversarial-review --wait --base <ref> <focus>`), do so, show the output verbatim and don't apply findings unasked. Otherwise end your report with a ready-to-paste `/codex:adversarial-review --base <ref> <focus>` command. Either way:
- `--base` is the commit before your work, so only your change is reviewed.
- The focus names the risky parts, the failure being fixed, what your tests prove and don't, and asks for reproductions (`swift test`, small `swiftc` or pytest programs against the real sources), not only reading. It repeats the fixture rules: disposable data, `PIG_MUSIC_DATA` and `PIG_MUSIC_LIBRARY` set explicitly, no push or deploy.
- A reviewer in a read-only sandbox can't run tests; say what it couldn't verify. Before acting on a finding, check it against the code.

Stopping rule, so review doesn't loop: at most two Codex rounds per batch of work. After the second round only high findings block the handoff; medium and low ones become issues labelled `finding` for Mark to prioritise; say so in the report. Security findings go to a draft security advisory instead (the repo is public): no exploit detail in public text.

## Session checklist

**Start:** `git status`; `git log origin/main..main`; read ROADMAP.md's current state; `gh issue list --label P1` before picking work. Tools that can't read issues (Codex, offline agents) get the issue text pasted into their prompt.

**End** (in every handover): docs touched; ROADMAP.md current state overwritten (yes/no); no new status or handover files (yes/no); issues updated (with a comment for item-level handover); commits waiting (`git log origin/main..main`); and for every pushed-but-open issue, ask Mark whether it has been checked. The story of a change goes in its commit message, not the docs.

## Ship

- Commit straight to `main`, one logical change per commit, writing `Refs #n` (never `Fixes`/`Closes`: an issue is closed only after Mark confirms the fix was checked). A push to `main` publishes `ghcr.io/maroge1990/pig-music:main` and `sha-<rev>`: push a batch once it has passed its two Codex rounds with no high finding left (D-50); otherwise ask Mark first.
- Live releases: Claude may release when it gives a genuine testing benefit, under D-52 (CI, two Codex rounds, Unraid verify, at most once a day, nobody playing, tell Mark after); otherwise ask Mark. Verify with `scripts/deploy_unraid.py --image <digest>`, release with `scripts/deploy_release.py` (see [operations](docs/OPERATIONS.md)). Both read household values from `.private/household.env`.
- Native build number: change `CURRENT_PROJECT_VERSION` in both `swift/PigMusic.xcodeproj/project.pbxproj` and `scripts/generate_native_project.py`. Current version: 0.2.0 (15).
- After a delivery, overwrite ROADMAP.md's current state and comment on the issue. Record Mark's decisions in docs/DECISIONS.md. Don't add dated delivery docs.

## Conventions

- Layout and brand rules: [docs/DESIGN.md](docs/DESIGN.md). Admin-only options always go at the bottom of shared views; native apps have no admin tools.
- Product rules (never transcode to reach a quality; favourites and imports never trigger downloads; a song matches any edition unless `explicit_edition`; live/remix/demo stay distinct): [docs/PRODUCT.md](docs/PRODUCT.md).
- Match surrounding code style: Black (Python), Prettier (web), existing Swift formatting.
- Docs describe the present and are overwritten; one home per fact.

## Gotchas

- `PIG_MUSIC_LIBRARY` defaults to `/library`, which macOS resolves to the system `/Library`. Always set it locally; `tests/conftest.py` points tests at an empty path.
- Never point tests or tools at live appdata or the real NAS. Secrets stay in appdata files, never in Git, docs or `.env`.
- The repo is public: household addresses, host names, the public domain and edge-protection rules live only in `.private/` (`tests/test_private_details.py` fails if they reappear in a tracked file when `.private/household.env` is present).
- `scripts/generate_native_project.py` has no check mode: it rewrites the project and schemes. The repo keeps Xcode's re-saved files, so run it only when adding app Swift files, and review the diff.
- Xcode builds: use the default DerivedData (no `-derivedDataPath`), check `df -h /` first (stop under ~15 GB), and keep `ONLY_ACTIVE_ARCH=YES` for Debug. The 8 GB development Mac cannot run the iOS 27 simulator; generic destinations build without a runtime.
- `scripts/deploy_unraid.py` bundles `README.md` and `docs/`; the brand JSON files in `docs/` are read by the Dockerfile, scripts and tests; don't move them.
- Both OpenAPI copies (server and Swift) must match; regenerate with `.venv/bin/python -m scripts.export_openapi`.
