# Meridian Assistant: real-browser end-to-end suite

Drives the REAL extension (unpacked, e2e build) in a real Chromium (or Google Chrome) against the REAL Meridian backend
(temp data dir, in-memory secrets, fake loopback writing model) and local fixture pages. Nothing touches the internet,
the Keychain, the installed Meridian.app or any real data.

```
.venv/bin/python extension/tests/e2e/test_extension_e2e.py                    # everything (about 2-3 minutes)
.venv/bin/python extension/tests/e2e/test_extension_e2e.py -k tab_walk,pairing # substrings, comma separated
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --repeat 5          # flakiness check
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --recipe chrome     # branded Chrome through CDP Extensions.loadUnpacked
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --headed            # watch it
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --list              # what exists, what is blocked, by what
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --strict            # release gate: BLOCKED or KNOWN ISSUE = failure
.venv/bin/python extension/tests/e2e/test_extension_e2e.py --junit out.xml     # junit summary
.venv/bin/python extension/tests/e2e/test_extension_e2e.py -k fixtures_scan --update-golden   # after editing a fixture
```

Prerequisites: `npm --prefix extension ci` (esbuild; the suite builds the `e2e`, `e2e-closed` and `chrome` variants into its
own temp directory, never into `extension/dist`), Playwright browsers for the repo venv.

## Layout

| file | role |
|---|---|
| `test_extension_e2e.py` | CLI, runner, summary, junit, leftover-process reaper |
| `framework.py` | scenario registry (`@scenario(needs=..., xfail=...)`), `Env`, `check`, `finding`, results |
| `harness.py` | build variants, launch recipes, service-worker discovery, real toolbar activation, direct pairing, failure artifacts, capability probes |
| `backend.py` / `backend_launcher.py` | the real backend in a subprocess, app-API client, seeding through the API, pairing approval, shims for `open`/`osascript`/`pbcopy` |
| `seed.py` | the synthetic candidate (typed knowledge layer + flat facts + CV) |
| `fake_llm.py` | OpenAI-compatible loopback model satisfying the real draft + independent-review pipeline |
| `servers.py` | fixture server on two origins (127.0.0.1 and localhost; every server has a listen backlog of 256: TEST-01), fake employer site, bridge squatter. `FixtureServers.generated[name] = html` serves a page a scenario made |
| `pages.py` | `form_page(controls)`: an application page made on the fly from a list of labelled controls (the plain-question gate and the metamorphic sample) |
| `ui.py`, `flows.py` | selectors/locators of the visual layer (`Chip`, and `ClosedChip` for the closed root of the production build), field-by-field `walk`, value readers |
| `expectations.py` | acceptance tables: what each fixture field must do |
| `scenarios_*.py` | the scenarios: `harness` (self-checks), `connection`, `forms`, `assist` (drafts, research, save, track), `wave2` (slow bridge, hidden tabs, disconnect, Spanish, production-like build, click-through ...), `journeys` (keyboard-only applications), `wave5` (chip chords and rewrite chords, chip placement beside/avoid, first-use card, end-of-form review, duplicate banner and what became of the application, Save to tracker and 'already recorded', Fill verified confirmation/cancel/undo-all, Alt+Shift+M from a cross-origin frame, always-on switch notes, the extension's own widget scan), `engine` (the plain-question gate and a metamorphic sample through the real page), `g5` (what running everything on a loaded machine found: the Ashby fixture's late results must not re-open a list after a pick; an Enter under a page popover writes nothing and says so), `sec6` (the final security review of the content layer through the real worker: covers that take no click hide a field and the CV input from Tab, a script's `execCommand` text is never stored as a correction, typed tags and creatable values never reach the bridge) |
| `keyjourney.py` | the keyboard-only recorder (`Keys`: Tab / Shift+Tab / Enter / Esc only), the trusted-mouse watch, the whole-form snapshot and its comparison with `fixtures/journeys/*.expected.json` |
| `../fixtures/` | realistic application forms (see below) and `scan_golden.json` |

## Keyboard-only journeys

`scenarios_journeys.py` completes a Greenhouse-style, an Ashby-style and a Workday-style (cross-origin frame) application with
`Keys` only: after the toolbar activation nothing but Tab, Shift+Tab, Enter and Esc is pressed (`Keys.press` refuses anything
else) and `watch_pointer` / `pointer_events` prove that no trusted mouse event reached any frame. Each journey ends by comparing EVERY control of
EVERY frame with `extension/tests/fixtures/journeys/<name>.expected.json` (an AI draft is matched with `{"$contains": ...}`),
so what must stay empty (consent, demographics, unknown answers, a cover letter, the CAPTCHA response) is asserted as much as
what must be filled. The expected files were written from the seed data, never from what the product produced.

## The production smoke journey

`production_build_paired_with_closed_shadow` is made only of what ships: the `chrome` variant, the very build that is packaged (closed
shadow root, no e2e hooks, the fixed bridge ports; built into the suite's own temp directory), paired through its own options page against a test backend (the code on the page
is the code the app asks for), started by the registered `chrome.action.onClicked` listener (what a toolbar click runs), and then a
whole Greenhouse application is walked by `scenarios_journeys.greenhouse_journey` with Tab, Shift+Tab, Enter and Esc alone (the same
function the e2e-build journey runs, with a `ClosedChip` instead of a `Chip`). It ends with the same forbidden-effect checks as every
journey: the whole form equals `greenhouse.expected.json`, exactly the planned controls were written, nothing submitted or recorded,
no consent or demographic answer, no trusted mouse event, no `Authorization` header or chip text anywhere in the page.

## The visual layer after waves 3 and 4: what the suite reads

* **The chip has two shapes.** The full chip has `chip-question` (the PAGE's own label, its first line), `chip-value`, `chip-trust`, `chip-why`
  and the key hints; when the form leaves it no room it folds to a one-line pill (`data-pill`, `chip-pill-text`, a `chip-trust` word only for
  the screen reader). `Chip.value` reads both; `Chip.shown_value()` reads the answer without the operation word ("Select"). A quiet chip
  (`data-quiet`) has `chip-label` and `chip-reason` instead. A skipped upload (state SKIP) is a quiet chip too: `Chip.expect_skip(code=...)`
  asserts the extension's own words for the reason (`ui.dictionary("en")` reads `content/i18n.ts`, so no product sentence is copied into the suite).
* **The chip's own question line is not a leak.** The closed-root check (`ClosedChip.read()` returns `question` and `own`) compares only the
  assistant's own text with the page: the question line repeats the page's label on purpose.
* **Chip actions have chords that type nothing**: Control+Option+n on a Mac, Alt+Shift+n elsewhere (`ui.is_mac`, `ui.chip_chord`). The suite asks
  the page's own browser which platform it is, and reads the chord from the button's `aria-keyshortcuts`.
* **"Fill verified" asks first** (`panel-fill-preview`, `fill-confirm`, `fill-cancel`); `Alt+Shift+Z` takes the whole batch back
  (`panel-batch-undo`). After a MOUSE click on the panel's button the keyboard is still on the page: Esc closes the question only with the keyboard in it.
* **Overlap guards need a layout that forces an overlap.** The chip keeps off the neighbours whenever it can, so a click-through proof uses
  `dense_form.html` (rows as tall as the chip's one-line form) in a small window, and the vacuity guard is on that form.
* **Plain-question gate.** `scenarios_engine.py` serves forms made with `pages.form_page`: the plain wordings are answered and the wordings the
  engine's gate suite holds as NOT plain stay 'needs you'. A fixture label with a note in brackets is NOT a plain question ("Email address (editor
  with its own model)" became "Email address" for that reason), nor is "Email for job alerts" (a sign-up, not the person's email).
* **Metamorphic sample.** `scenarios_engine.metamorphic_sample_of_twenty_labels_through_the_real_page` starts the real backend seeded with one of
  the held-out suite's synthetic candidates (`MERIDIAN_E2E_PERSON=uk_citizen`, read by `backend_launcher.py`), renders 20 wrapped labels (one per
  kind of wrapper, fixed order) plus their plain bases as a real form, reads each chip and applies the suite's own `check.evaluate`. A new
  violation names the property (S1 ... K1: see `metamorphic/check.py`), the wrapper, the label and what the chip said.

## Patience on a loaded machine (`scale.py`)

The suite's waits were chosen on a quiet machine. Beside other projects' browsers (load average 70-190 measured) a backend needs more than
the former 60 s to import the application, the first suggestions of a page more than 30 s, and scenarios failed that had nothing wrong.
`scale.py` holds ONE factor (`MERIDIAN_E2E_SLOWDOWN`, default 3; `1` is the original speed of a quiet CI machine) that multiplies every EVENT
wait: `flows.poll_until`, the default of every `expect(...)`, `ui.DEFAULT_TIMEOUT` (the chip budget), the explicit millisecond timeouts of the
positive waits (`millis(...)`) and the per-scenario time limit; the backend's ready wait is `backend.READY_TIMEOUT` (180 s). An event wait
returns the moment its condition holds, so a pass is never slower, and no assertion is weakened.

Two checks that depended on the speed of the machine, not on the product, now read the product's own record instead of racing it:
* `draft_generation_dwell_regenerate` runs the content script's clock 50 times slower (`Rig.slow_clock`) while the draft appears and Tab goes
  down, so "on screen for 500 ms" is tested the same way on any machine (before, a product with NO dwell passed it on a loaded machine: the
  harness itself needed 500 ms between the draft and the key, and the check turned "inconclusive").
* `slow_bridge_shows_the_connecting_state` asks a sampler running INSIDE the page (`ui.record_phases`, every 20 ms) which phases the panel went
  through, instead of polling from outside for a state that lasts seconds.

## Fixtures that model late widgets must not race themselves

`ashby_apply.html` has comboboxes whose results arrive 120 ms after the last typed character. Results that arrive AFTER an option was picked
belong to a search nobody waits for: the fixture cancels the pending search on a pick (`ashby_fixture_list_stays_closed_after_a_pick_when_late_results_arrive`
proves it in one task, with no timing luck). Before that, a loaded machine re-opened the location list over the next question (the sponsorship
buttons) and both Ashby scenarios failed for the fixture's own reason: the assistant refuses to write under a page popover (SEC-01), as it must.
`?overlay=N` puts a popover over the sponsorship buttons for N ms once a location is chosen
(`enter_while_a_popover_covers_the_buttons_writes_nothing_under_it_and_says_so`: nothing is written under it, nothing is submitted, the person gets an error toast).

## Things the harness cannot do for real, and what it does instead

* **Hidden tabs.** Headless Chromium reports every tab as visible. `Rig.set_visibility(page, False)` makes the content script
  see `document.visibilityState == 'hidden'` (the property is overridden in the extension's isolated world, where the content
  script reads it, followed by a real `visibilitychange` event). A `--headed` run really hides background tabs too.
* **A slow bridge.** A delaying proxy cannot stand in front of the bridge: the bridge's hello names the port it listens on and
  the worker takes any other port for a stranger (the `delaying_proxy_in_front_of_the_bridge_is_refused` scenario proves that
  no credential goes through). A busy Meridian is simulated by `RealBackend.freeze()` / `thaw()` (SIGSTOP / SIGCONT of the
  whole backend: the sockets keep accepting, nothing answers).
* **The production build** (`production_build_paired_with_closed_shadow`, the smoke journey) uses the fixed bridge ports
  47615-47624, so it takes `harness.FIXED_PORTS_LOCK` and skips itself when anything already listens there (a real Meridian).
  Its shadow root is CLOSED, so locators cannot see the chip: `ui.ClosedChip` reads it through the DevTools protocol
  (`DOM.describeNode` with `pierce` lists a closed root, `DOM.resolveNode` + `Runtime.callFunctionOn` read it where it stands, which
  is how DevTools shows one). Nothing in the product is test-only for that, and the page itself is still asserted to see nothing.
* **Always-on over plain http.** The switch asks for the secure origin of the site (and the worker's auto-start asks for BOTH origins of an
  http site), which no loopback fixture holds: the e2e build only has `http://127.0.0.1/*` and `http://localhost/*`. The suite therefore proves
  the permission note (`panel-always-on-permission`: nothing stored, a small options window opened for the site) and, for a site Meridian's own list names, the
  switch reading ON plus the rule note (`panel-always-on-rule`); Meridian's API only accepts dotted hostnames, so the loopback site is put on the
  worker's copy of the list directly (`hold_meridians_site_list`). That a rule starts the assistant by itself is covered by `tests/unit/activation.test.mjs`.
* **Application status moved in Meridian.** The app API records an application as sent (`POST /api/jobs/{id}/record-application`) but has no call for
  "rejected" (an email moves it): `RealBackend.db_write` updates the TEMP database, nothing else.
* **A click into a cross-process frame** goes through `flows.click_in_frame`: right after focus crossed the frame boundary Chromium
  can still route mouse events by the previous frame's hit-test data (the click then lands on the host's `<iframe>` element; 4 of 16
  runs without the guard, 0 of 24 with it), so the helper first waits two animation frames inside the frame.

## Mutation testing (proving a scenario catches its bug)

`MERIDIAN_E2E_EXTENSION_DIR=<copy of extension/>` and `MERIDIAN_E2E_BACKEND_DIR=<dir holding a copy of jobagent/>` point the suite
at scratch copies of the product, so a fix can be reverted THERE and the scenario that guards it must fail. (The suite never
needs them otherwise.)

## Safety rules the harness enforces

* every data dir, profile and build lives under one temp root that is removed at exit (`MERIDIAN_E2E_TMP` redirects it);
* the backend runs with `MERIDIAN_TEST=1`, `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`, an in-memory secret store, no
  scheduler and `HOME` pointing into the temp root. Under `MERIDIAN_TEST` the product itself skips the native approval dialog
  and only records its macOS helper commands (`open`, `pbcopy`; `extension/api.py`); as a second line of defence shim
  executables for `open`, `osascript`, `pbcopy` and `say` come first on `PATH` and only log (the `osascript` one then blocks, like an unanswered dialog). They catch calls by bare name;
  a call by absolute path (`/usr/bin/open`) relies on the product's own `MERIDIAN_TEST` guard;
* the bridge is found through the e2e-only storage key `meridian.e2e.ports` (never the fixed 47615-47624 range), so a real
  Meridian running on this Mac is never contacted; the production-build scenario refuses to run when something listens there;
* after the run every process whose command line points into the temp root is killed and reported.

## Fixtures (`extension/tests/fixtures`)

Greenhouse, Lever and Ashby style forms, a Workday-style multi-step form inside a cross-origin iframe, a React 18 controlled
form (vendored UMD), masked phone, contenteditable editors (plain and model-owning), choice controls, a conditional follow-up
question, consent + demographics, file uploads, a prefilled form, a single-page wizard, submit + confirmation (same document and
next document), a shadow-DOM form, a password sign-up page, a CAPTCHA gate, a decoy page without a form and a fake employer
website. Every page includes `_guard.js`, which records synthetic events (the extension's writes) and every submit/next click
(`window.__events`, `window.__submits`): the suite asserts the extension never clicks Submit and never writes to passwords,
consent boxes, protected answers or the CAPTCHA.

## Contract with the product (what the suite relies on)

* `data-testid` / `data-*` attributes of the visual layer listed at the top of `ui.py` (open shadow root in e2e builds);
* `chrome.storage.local["meridian.e2e.ports"]` candidate-port override and `e2e.html` (`window.send`) in e2e builds;
* `MERIDIAN_EXTENSION_RESEARCH_LOOPBACK=1` (under `MERIDIAN_TEST`) lets the research fetcher reach the loopback employer site;
* `GET /api/extension` (`pending_pairing`, `clients`, `state`), `POST /api/extension/pairings/{id}/approve|deny`,
  `DELETE /api/extension/clients/{id}`, `PATCH /api/extension/settings`.

## KNOWN ISSUES and xfail

A row of `expectations.py` with `known_issue=...` and a scenario with `xfail=...` describe behaviour the product does not meet
yet. They never hide: the run prints `KNOWN ISSUE ...` lines and a summary, `--strict` fails on them, and a known issue that has
been fixed announces itself ("appears FIXED", or XPASS for a scenario) so the marker can be removed. Right now there is one: a skipped
TRANSCRIPT upload says "upload a cover letter yourself" (`controller.ts` `COVER_LETTER` lists `transcript`); `file_attach_cv` asserts that the
upload IS left alone with a quiet chip and records the wrong sentence with `finding()`. Every earlier gap has been fixed and its scenario is a
plain assertion (the mutation proof of each is: revert the fix in a scratch copy through `MERIDIAN_E2E_EXTENSION_DIR` or
`MERIDIAN_E2E_BACKEND_DIR` and the scenario fails).

The golden scan (`fixtures/scan_golden.json`) holds every fixture in BOTH modes of `observation.js`: `plain` (no options: how the Playwright
filler calls it) and `widgets` (`{widgets: true}`: how the extension calls it). `scan_frames` takes `widgets=` and defaults to the extension's
call; `widget_patterns.html` holds the three controls only the widget mode sees, and
`the_extension_scans_with_the_widget_patterns_the_golden_file_holds` checks that the extension really asks for them.

## Fixture variants

`workday_apply.html` takes `?bare=1` (nothing of the host page after the partner's frame), `?tail=field` (the frame's button is
disabled, so its last field is its last tab stop and Tab hands focus to the host page) and `?prefill=1` (the host's email field is
already filled by the page). The query string is handed on to the frame through the `{{QUERY}}` token of the fixture server.
