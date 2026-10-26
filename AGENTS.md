# PigMusic: agent guide

Private household music manager: FastAPI + embedded slskd in one Unraid container, React/Vite web client, Swift iPhone/iPad/Watch apps. Product contract, decisions, architecture and the roadmap live in [blueprint.md](blueprint.md). Deploy/rollback: [docs/operations.md](docs/operations.md). Manual/browser/device verification: [docs/testing.md](docs/testing.md).

## Setup (macOS)

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt -r requirements-dev.txt
(cd client && npm ci)
```

Node 24 and FFmpeg are also required. Environment variables: [.env.example](.env.example).

## Run

```sh
(cd client && npm run build)
PIG_MUSIC_DATA=.localdata/dev PIG_MUSIC_PRELOAD=false .venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8765
```

The preview config `pig-music-dev` attaches to port 8765. A fresh install shows the first-run admin form; the installation code is printed to the server console. Use generated test credentials only. iPhone: open `swift/PigMusic.xcodeproj`, scheme **PigMusic** (embeds **PigMusicWatch**); Debug launch argument `--demo` gives a synthetic offline library.

## Test, cheapest first

1. **Every change (~1 min):** `.venv/bin/python -m pytest tests -q -p no:cacheprovider` and `(cd client && npm test)`. Web UI changes: `(cd client && npm run build && npm run test:e2e)` (Playwright against a mocked API; first run `npx playwright install chromium`).
2. **Before commit/push (~1 min more):**
   ```sh
   .venv/bin/python -m black --check server tests scripts --workers 1
   (cd client && npm run format:check && npm run build)
   .venv/bin/python -m scripts.export_openapi --check
   .venv/bin/python scripts/sync_native_brand.py --check && .venv/bin/python scripts/generate_brand_tokens.py --check
   swift test --package-path swift/PigKit
   ```
   For the two Swift contract tests, run `.venv/bin/python -m scripts.serve_contract_fixture` in another terminal and add `PIG_CONTRACT_SERVER=http://127.0.0.1:8781`. When Swift changes, also build: `xcodebuild -quiet -project swift/PigMusic.xcodeproj -scheme PigMusic -destination 'generic/platform=iOS Simulator' -skipPackagePluginValidation CODE_SIGNING_ALLOWED=NO build` (and `-scheme PigMusicWatch` with `generic/platform=watchOS Simulator`).
3. **CI on push:** `.github/workflows/container.yml` runs dependency audits, the backend suite in the exact Linux image, 1 GB artwork/delivery/sync scale gates and web checks; `native.yml` runs the brand, contract, PigKit and unsigned app builds.

## Independent review

Once your checks pass, get an adversarial review by a different agent before pushing any change that either:
- touches concurrency, session or object lifetimes, playback, sync, file moves/deletion (Tidy, Trash, organisation, export), auth, signing or security; or
- changes more than about 3 non-test source files or 150 non-test lines.

A plain review only reads the diff; ask for an adversarial one. If you can run it yourself (Claude Code with the Codex plugin: its `codex-companion.mjs adversarial-review --wait --base <ref> <focus>`), do so, return the output verbatim and don't apply findings unasked. Otherwise end your report with a ready-to-paste `/codex:adversarial-review --base <ref> <focus>` command. Either way:
- `--base` is the commit before your work, so only your change is reviewed.
- The focus names the risky parts, the failure being fixed, what your tests prove and don't, and asks for reproductions (`swift test`, small `swiftc` or pytest programs against the real sources), not only reading. It repeats the fixture rules: disposable data, `PIG_MUSIC_DATA` and `PIG_MUSIC_LIBRARY` set explicitly, no push or deploy.
- A reviewer in a read-only sandbox can't run tests; say what it couldn't verify. Before acting on a finding, check it against the code.

Stopping rule, so review doesn't loop: at most two Codex rounds per batch of work. After the second round only high findings block the handoff; record medium and low ones in [docs/FINDINGS.md](docs/FINDINGS.md) for the owner to prioritise, and say so in the report.

Tests mark provider rate limits off by default; `@pytest.mark.real_rate_limits` (see `tests/test_rate_limits.py`) keeps them.

## Ship

- Commit straight to `main`. **Ask before pushing:** a push to `main` publishes `ghcr.io/maroge1990/pig-music:main` and `sha-<rev>`.
- Live releases are separate and owner-authorised: verify with `scripts/deploy_unraid.py --image <digest>`, release with `scripts/deploy_release.py` (see operations).
- Native build number: change `CURRENT_PROJECT_VERSION` in both `swift/PigMusic.xcodeproj/project.pbxproj` and `scripts/generate_native_project.py`. Current version: 0.2.0 (10).
- After a delivery, update the blueprint's status and roadmap. Commit messages carry the details; don't add new dated delivery docs.

## Conventions

- Owner sees the member experience plus an Admin section; admin-only options always go at the bottom of shared views. Native apps have no admin tools.
- Layout: integrated phone player/tab dock, settings below the music on detail pages, compact headers, Home shows artists and albums (no song recommendations).
- Never transcode to reach a quality; favourites and imports never trigger downloads; a song matches any edition of itself unless the request is `explicit_edition` (Other versions); live/remix/demo recordings stay distinct. Full contract: blueprint.
- Match surrounding code style: Black (Python), Prettier (web), existing Swift formatting.

## Gotchas

- `PIG_MUSIC_LIBRARY` defaults to `/library`, which macOS resolves to the system `/Library`. Always set it locally; `tests/conftest.py` points tests at an empty path.
- Never point tests or tools at live appdata or the real NAS. Secrets stay in appdata files, never in Git, docs or `.env`.
- `scripts/generate_native_project.py` has no check mode: it rewrites the project and schemes. The repo keeps Xcode's re-saved files, so run it only when adding app Swift files, and review the diff.
- Xcode builds: use the default DerivedData (no `-derivedDataPath`), check `df -h /` first (stop under ~15 GB), and keep `ONLY_ACTIVE_ARCH=YES` for Debug. Only the iOS 27 simulator runtime is installed; generic destinations work without a watchOS runtime.
- `scripts/deploy_unraid.py` bundles `README.md` and `blueprint.md`; keep them at the root. The brand JSON files in `docs/` are read by the Dockerfile, scripts and tests; don't move them.
- Both OpenAPI copies (server and Swift) must match; regenerate with `.venv/bin/python -m scripts.export_openapi`.
