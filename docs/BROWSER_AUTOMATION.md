# Browser automation contract and verified limits

The browser uses a dedicated local Playwright profile. On macOS it selects installed Google Chrome by default, otherwise Chromium from the user's Playwright browser cache. Explicit channel choices are respected. It opens visibly for real applications. It does not attach to an unrelated personal browser profile, modify fingerprints, bypass CAPTCHA or save passwords in SQLite. Cookies remain in the browser profile. Only files selected as application documents can be uploaded.

## Decision boundary

The fixed DOM observer numbers controls and retains original node references. It reads labels, roles, current state, enabled operations, dropdown options, required fields, frames and open shadow roots. A model decision contains only an operation, observed index and confidence. The executor checks the original document, semantic snapshot, current visibility, enabled state and hit-test coverage before interaction. Text and files are supplied separately from approved candidate facts and selected document versions.

Laya/Jev is deliberately limited to choosing among already-observed safe application navigation buttons when the deterministic adapter cannot select one unambiguously. It never writes missing facts, chooses sensitive answers, submits, accepts legal statements or executes code. Model failure leads to a human task. Cloud inference requires opt-in configuration, a Keychain secret and the orchestrator's budget callback.

## Form behavior

Deterministic filling handles labelled text fields, native selects, checkboxes, radio groups, uploads, native date fields and accessible custom dropdowns with exact verified-option matches. Multi-step forms can continue through explicit Next/Continue controls. A custom dropdown must display the selected value after its option is clicked; otherwise verification is handed to the user. Closed shadow roots, proprietary widgets, unreadable frames, hidden required uploads and ambiguous choices require takeover.

Only verified or locked facts are considered. Conflicting facts, unknown questions and unavailable options stop at human input. Sensitive categories require an explicit saved policy, including legal/privacy agreement checkboxes. Country-specific work authorization is never inferred from nationality, education or location. A generic verified answer is used only for an exact supported field meaning; unfamiliar phrasing requires a saved response.

## Review and submission

MANUAL does not fill or submit. REVIEW and AUTO both prepare the form and return a snapshot for the orchestrator's approval/policy gate. Filling never clicks final Submit. Submission accepts a one-use review snapshot bound to the active application. A changed field, page, node or document invalidates it. After the click, a separate observation must find an application confirmation. Missing evidence creates an uncertain-outcome task and never retries Submit automatically. A confirmation already visible before this run is not claimed as a new successful application.

Global pause is checked before every action. Human takeover clears snapshots; resume requires fresh inspection. The progress callback persists each executed step while the run is active, including the observation used for it. The orchestrator owns durable jobs, applications, events and recovery state.

## Adapter coverage

The registry includes Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Teamtailor, iCIMS, Taleo, LinkedIn, Indeed and generic company pages. Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Teamtailor have known field-name aliases. All permitted ATS adapters share guarded semantic HTML form handling. This is not a claim that every proprietary variant of every ATS has been validated. LinkedIn and Indeed are configured for prepared-data/manual-browser handoff.

The deterministic paths were tested with local Greenhouse-style, Lever-style, Workday-style multi-step/iframe and generic validation fixtures. Additional browser tests cover occlusion, stale nodes and edited approvals, CAPTCHA, forbidden submit/upload operations, open shadow roots, custom dropdown verification, missing documents, hidden required uploads, confidence gates, Laya failures and bounded navigation. No real employment application was used for automated tests.

## Local model lifecycle

`LayaRuntime` provides asynchronous `status`, `install`, `start`, `stop` and `close`. Installation runs only on explicit request, isolates its virtual environment and Hugging Face cache, pins the reviewed upstream commit and runs `localdecide doctor`. The desktop app works without a local-model Python runtime. Installation requires Python 3.12/3.13 and network access for packages/model weights. On Apple Silicon it uses MLX; other supported machines use PyTorch.

Start preserves any existing listener on loopback port 8791 and does not spawn duplicates. Stop terminates only its own child. The model and virtual environment are not included in the desktop bundle. The development installation and a real inference benchmark are recorded in [DEPENDENCIES.md](DEPENDENCIES.md); the installed Apple Silicon package versions are captured in `scripts/laya-runtime-macos-arm64.lock`. The independent production installation, managed start, successful real inference and clean stop are recorded in [installed-laya-runtime.json](installed-laya-runtime.json).
