# Browser decision engines: verified integration notes

Research date: 29 September 2026. This application implements its own restricted browser executor. It does not vendor either reference project's browser loop. Upstream performance figures are authors' measurements, not measurements of JobAgent. The optional runtime setup pins `laya-browser-agent` to commit `9060f073e7836d6f10a6b0596f362e362f32107e`.

## Licences and installation

* [`browser-use/jev-ultrafast`](https://github.com/browser-use/jev-ultrafast) is [MIT licensed](https://github.com/browser-use/jev-ultrafast/blob/main/LICENSE). The harness licence does not grant access to the hosted Jev service or its closed model weights.
* [`ChenneyZhuang/laya-browser-agent`](https://github.com/ChenneyZhuang/laya-browser-agent) is [Apache-2.0 licensed](https://github.com/ChenneyZhuang/laya-browser-agent/blob/main/LICENSE). Its [package metadata](https://github.com/ChenneyZhuang/laya-browser-agent/blob/main/pyproject.toml) currently declares version 0.3.2 and Python >=3.10. Optional dependencies are `laya-mlx>=0.1.0`, `laya>=0.3.3`, Playwright and websocket-client. It is marked Alpha.
* The [installation instructions](https://github.com/ChenneyZhuang/laya-browser-agent#install) currently require installation from its repository, rather than an assumed PyPI package. Apple Silicon uses the MLX runtime; Intel Macs use PyTorch. Browser-tuned weights are downloaded on first use and cached. Model availability, licence and memory consumption must be checked when selecting a different checkpoint.

Recommended optional setup, in a separate virtual environment:

```sh
git clone https://github.com/ChenneyZhuang/laya-browser-agent
cd laya-browser-agent
python3.12 -m venv .venv
.venv/bin/pip install -e '.[mlx]'
.venv/bin/localdecide doctor
.venv/bin/localdecide serve --host 127.0.0.1 --port 8791
```

Use `.[torch]` on an Intel Mac. Pin a reviewed upstream commit for production installations; the example does not claim a moving branch is reproducible. No upstream skill installer is required.

## Actual HTTP contract

Both integrations use `POST /v1/systemone`, with a JSON body containing `model`, `state`, and `questions`. Each question is `type: "choice"`, with `criteria` mapping offered keys to descriptions, and `instructions` explaining the question. One request contains an operation head and separate target heads for supported operations. The result contains `answers`; each selected head must contain `choice`, `confidence`, and `probabilities`.

The [Jev client implementation](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/model.py) calls `https://api.typesafe.ai/v1/systemone` with a bearer token and defaults to `jev-latest`. It separately validates the operation and the selected target, including finite probability values, complete key coverage, total probability and argmax consistency. Text writing is a separate generative-model call; the decision model does not supply form values.

The [localdecide HTTP server](https://github.com/ChenneyZhuang/laya-browser-agent/blob/main/localdecide/serve.py) serves the same shape at `http://127.0.0.1:8791/v1/systemone`. It also offers `/healthz`, `/v1/models`, `/v1/decide`, and `/v1/table`. Its request `model` is an echoed label, not a checkpoint selector. The actual backend comes from `LOCALDECIDE_BACKEND`. Its permissive CORS implementation must not be copied into JobAgent. This service should remain on loopback and be contacted by the authenticated Python orchestrator, not exposed publicly.

The [backend implementation](https://github.com/ChenneyZhuang/laya-browser-agent/blob/main/localdecide/backends/base.py) defaults to `ichenney/laya-browser-v32b`, subfolder `v32b`. `LayaMLXBackend` and `LayaTorchBackend` call `load(...)` then `predict(state, questions)`. Model loading is lazy. A health response alone therefore does not establish successful inference. The [`doctor` command](https://github.com/ChenneyZhuang/laya-browser-agent/blob/main/localdecide/cli.py) performs a prediction smoke test.

## Architecture decisions and limitations

JobAgent owns the observed element table, node references, document identity and semantic fingerprint. The model selects only a permitted operation and an observed index. Application data comes from verified candidate facts. The executor independently checks freshness, visibility, enabled state and occlusion immediately before an operation. Submission requires its own policy gate and independent confirmation evidence.

The [Jev snapshot implementation](https://github.com/browser-use/jev-ultrafast/blob/main/jev_ultrafast/snapshot.js) demonstrates atomic structured observations, stable node identity and form-state freshness checks. Its [documented limitations](https://github.com/browser-use/jev-ultrafast#evidence-and-limits) include frames, shadow roots, uploads, popup tabs and arbitrary widgets; its accessible-name reader is not the complete W3C algorithm. These gaps are reasons to build explicit Playwright handling and human takeover rather than assuming that either upstream loop handles every ATS.

Laya's [design discussion](https://github.com/ChenneyZhuang/laya-browser-agent#how-it-works) recommends narrowing option sets and records overconfidence and loop failures. A confidence score is advisory; it never overrides JobAgent's deterministic safety rules. Scope targets to the current application form and apply a step budget. Local decision failure pauses or falls back to deterministic/human handling. It must never silently switch to paid hosted inference.

Jev is optional and sends the selected structured page content to TypeSafe. It requires explicit configuration and a secret in Keychain. Laya is preferred for local inference, but deterministic discovery, matching, profile management, review and form preparation remain available without model downloads or paid credentials.

## Measurements on this Mac

An isolated runtime was actually installed under the ignored `.local-models/laya` development folder: Apple M2 Max, 64 GB unified memory; Python 3.12.11; `laya-browser-agent` 0.3.2 at the pinned revision; `laya-mlx` 0.2.0; MLX 0.32.3. `localdecide doctor` loaded the downloaded browser-v32b checkpoint and completed real inference in 1,250 ms including model loading.

The actual localhost HTTP benchmark selected the expected TYPE_TEXT / target 1 in 3/3 repeated synthetic email-form cases. Measured latencies were 110.87, 61.06 and 59.44 ms (median 61.06 ms); the service RSS after inference was 1,493.17 MiB. This tiny check establishes that this installation and wire contract work; it is not a general ATS reliability score. Raw results are in [local-laya-benchmark.json](local-laya-benchmark.json).

The initial probe omitted the actual email while asking to enter a verified email. Laya consistently requested human input instead of selecting the field. That result is preserved in [local-laya-benchmark-initial.json](local-laya-benchmark-initial.json). The corrected fixture supplies the synthetic address `alex@example.test`; no real candidate data was used or browser action executed.

The runtime manager starts only a configured local process, reports its status and RSS, and stops only the child it owns. Installation is explicit and isolated; a separate Python 3.12/3.13 is needed for optional installation even though the packaged desktop backend does not require a system Python. No model or runtime virtual environment is included in the desktop bundle.

# Browser assistant (Chrome and Safari extension): dependencies

Reviewed 15 October 2026 against `extension/package.json`, `extension/package-lock.json`, `requirements.lock` and the repository.

## What ships

The extension in `extension/dist/{chrome,safari}` has **no runtime npm dependencies**: `extension/package.json` lists only `devDependencies`, and the bundles (`background.js`, `content.js`, `hint.js`, `options.js`) contain this repository's own code plus `backend/jobagent/automation/observation.js` bundled verbatim. No third-party script, font, image or remote code is loaded by the extension, and the only `fetch` calls in the extension's source go to the loopback bridge (`http://127.0.0.1:<port>/v1/...`, in `extension/src/shared/bridge-client.ts`). Its host permissions are the loopback address and the curated ATS hosts of `extension/ats-hosts.json` (for the small hint script and injection), plus an optional all-sites permission the user grants. Safari adds only the Swift sources in `extension/safari` (a sandboxed principal class that answers a native ping), compiled with `swiftc` and signed with `codesign`; there is no Xcode project and no Swift package.

The backend side uses libraries Meridian already depends on (`fastapi`, `uvicorn` for the second listener, `httpx` for the research fetcher with proxies and cookies disabled, `beautifulsoup4` for page parsing); the bridge adds none.

## Build and test tools (not shipped)

| Package | Pinned by the lock file | Use |
|---|---|---|
| `esbuild` | 0.28.2 (`^0.28.2`) | Bundles the IIFE scripts for each variant (`extension/build.mjs`) |
| `typescript` | 5.9.3 (`~5.9.2`) | `tsc --noEmit` type check (`make extension-typecheck`) |
| `@types/chrome` | 0.3.5 (`^0.3.5`) | Types for the `chrome.*` APIs |
| `playwright` (Python, `requirements.lock`) | 1.63.0 | Content, UI, widget and end-to-end suites; bundled Chromium and WebKit, branded Chrome through CDP |
| Node.js | needed for `make extension` | Runs esbuild and the unit tests (`node --test`) |
| Xcode (Swift toolchain, `codesign`, `pluginkit`) | only for `make extension-safari` and the Safari part of `make build` | Builds, signs and registers the Safari app extension; without it the build ships without Safari and says so |

`make extension-deps` installs from the lock file (`npm ci`, offline first) only when `node_modules` differs.

## Vendored and committed test code (never shipped)

- `extension/tests/fixtures/vendor/`: React 18.3.1 and ReactDOM 18.3.1 production UMD builds, copied unmodified from the npm packages (MIT, `LICENSE-react.txt`). They render a controlled-input fixture so the end-to-end suite proves insertions reach real React state.
- `extension/tests/widgets/fixtures/`: demo pages bundled from real libraries at the versions in `extension/tests/widgets/package.json` and its lock file (react-select, Headless UI, MUI, Downshift, intl-tel-input, react-phone-number-input, react-datepicker, react-dropzone, Quill, Select2, react-hook-form, Formik, Vue, jQuery and others). They are committed as minified bundles without licence banners, so the notices and the full lock-file list with licences live in `extension/tests/widgets/THIRD_PARTY.md`, generated by `extension/scripts/third-party.mjs`; a unit test fails when the file no longer matches the lock file. `build.mjs` copies nothing from `tests/`, and the sidecar embeds only `extension/dist/chrome`.
- The replicas of Workday, SmartRecruiters, Workable, iCIMS, Teamtailor and Lever widgets, and of Angular form controls, are hand-written from public markup patterns, not vendor code.

## Constraints

- The Chrome extension id is pinned by the public key in `extension/manifest-key.txt`; the private key stays in the git-ignored `.local-data/extension/`.
- Chrome 120 or newer and Safari 18 or newer are the manifests' minimums (`minimum_chrome_version`, `strict_min_version`). The Safari extension cannot be notarised with the Apple Development identity used here.
- Update `extension/package-lock.json` and `extension/tests/widgets/package-lock.json` with their `package.json`; regenerate `THIRD_PARTY.md` after the second.
