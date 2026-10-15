# Real-widget compatibility suite

Does the assistant's fill engine (`src/content/fill.ts`, with the adapters in `src/content/widgets/`) and scanner work with the widget
libraries that career sites are really built from, not only with hand-written fixtures? Every page under `fixtures/` is a REAL library
(react-select, Headless UI, MUI, Downshift, intl-tel-input, react-phone-number-input, react-datepicker, react-dropzone, Quill, Select2,
react-hook-form, Formik, Vue 3) or a hand-rolled replica of an ATS widget, driven with the real keyboard.

```
cd extension
npm run typecheck && node build.mjs --variant=e2e
../.venv/bin/python tests/widgets/run.py                     # the matrix: ~2 min, Chromium, content script in an ISOLATED world
../.venv/bin/python tests/widgets/run.py --world main        # same, content script in the page world (what tests/content does)
../.venv/bin/python tests/widgets/run.py --browser webkit    # Playwright WebKit (page world: it has no isolated worlds)
../.venv/bin/python tests/widgets/real_run.py                # real worker + real backend + real UI on six of the pages (< 1 min idle, minutes on a loaded machine)
node --import ./tests/unit/register.mjs --test tests/widgets/unit/*.test.mjs
node tests/widgets/bundle-report.mjs                         # what the adapters cost in the production content script
```

`run.py` builds its own e2e bundle into `dist/widgets-e2e` (other workstreams rebuild `dist/e2e` at any time).

## How a verdict is made

`tests/content/harness.py` is reused unchanged: fixture servers, the mock background (`Bg`: canned suggestions looked up by label),
`Rig`. `widget_rig.py` adds the widget fixture folder and an **isolated-world mode**: the real `content.js` and a mock extension runtime
are injected through CDP `Page.addScriptToEvaluateOnNewDocument` with a `worldName`, so the extension sees the page exactly like a real
content script (its own prototypes and globals; framework value trackers, custom-element members and page monkey patches invisible to it).
The page world only keeps the Playwright binding and tiny proxies (`main_shim.js`).

A scenario focuses a field, presses the real Tab key, waits for the assistant (and its own 280 ms re-check) and then asserts the
widget's OWN state: React/Vue state, the model the page keeps, hidden inputs and `FormData`, the displayed selection, where focus went, and
that no error toast was shown. Per widget: `PASS`, `FAIL`, `XFAIL` (a known gap owned by another workstream, with its hand-off) or
`XPASS` (an XFAIL that started passing: remove the mark). Exit code 0 only without `FAIL`/`XPASS`.

## Rebuilding the fixtures

`src/*.jsx` are bundled by `build-fixtures.mjs` (esbuild from `extension/node_modules`, shared chunks in `fixtures/chunks/`);
`src/*.html|css|js` are copied unchanged. `fixtures/` is committed, `node_modules` never is:

```
cd extension/tests/widgets && npm ci && node build-fixtures.mjs
```

## What is and is not verified

* The libraries are the real packages at pinned versions (`package.json`, `package-lock.json`).
* `workday_widgets`, `smartrecruiters_widgets`, `workable_widgets`, `icims_widgets`, `teamtailor_widgets`, `lever_widgets` and
  `angular_cva` are replicas written from memory of the vendors' public markup patterns (and of what `@angular/forms` does to native
  controls). They are NOT verified against live tenants: no employer site is contacted by any test.
* The react-select page is the real, unmodified layout (its empty search input is 4px wide: the scanner must treat it as a field, not
  as a honeypot). There is no layout accommodation any more.
* `scanner_patterns.html` and `job_header.html` are hand-written markup of widget FAMILIES (Radix Select, Headless UI Listbox, Workday
  buttons, drop areas, Yes/No button pairs, table layouts ...) and of their decoys; they stand for the patterns, not for a vendor's
  exact DOM. `tests/widgets/scanner_scenarios.py` states what the scanner must make of each block.
* Safari itself is not run; WebKit is Playwright's build, in the page world.

## Popups the page opens next to a field (calendars, lists)

A date picker opens its calendar when its input takes the focus and closes it on the Tab key the widget itself reads. The assistant used to
swallow that Tab (it inserts and walks to the next field by itself), so the calendar stayed open over the NEXT field, which was then
written into under it. `src/content/popups.ts` recognises a popup the page opened next to the field (a dialog, listbox, grid or menu by
ARIA, rendered, out of the form's flow, within 300 px, not around the field and not holding another question): the answer is still
inserted, but the Tab is left to the page and the browser (`react_datepicker_gets_its_own_tab_so_the_calendar_closes...`), and a field
such a popup covers is never written (`tests/content`: `a_calendar_that_stays_open_over_the_next_field...`). MUI Select / Autocomplete
mark their own open list with `aria-expanded` on the control, which the older combobox rules already honour (`mui_form_walked_with_tab_only`).

## Covers that never take a click (SEC-01)

A layer with `pointer-events: none` (a div, an `::after`, an `<img>`, an opacity .999 layer, text, a popover in the top layer) is invisible to
a pointer hit test and used to hide a field from its user while the assistant filled it. `visibility.ts` therefore repeats every hit test with
`pointer-events: auto !important` forced (a constructed stylesheet in an `@layer`, adopted for one synchronous pass: `paintPass`). The page
`fixtures/visibility_paint.html` has the hostile blocks (`hp_pe_*`) and the things that lie over a field with `pointer-events: none` and hide
nothing (`ok_pe_*`: a floating label, an icon, a MUI-style outline, a 30% veil); `visibility_covers_that_take_no_click_...` and
`visibility_helpers_answer_for_covers_that_take_no_click_...` run it through the scanner and through `humanVisibility` directly.
IntersectionObserver v2 (`trackVisibility`) was measured as a further gate and is not used: Chromium reports `isVisible: false` for every target as
soon as any element paints later, even a transparent `position: fixed; inset: 0` layer (the assistant's own UI has several).

## Known gaps (XFAIL, owned elsewhere)

Each is a scenario whose widget cannot work yet because of the keyboard layer or the vendor markup, not the scanner or the fill engine;
the scenario flips to `XPASS` (a failure of the expectation) when the owner fixes it, so the mark gets removed. The hand-off text is in
the scenario itself.

| Widget | Why | Owner |
|---|---|---|
| default react-select markup after a choice | the input becomes invisible, the rescan drops the field, `nextStop()` answers "native": focus does not advance | keyboard.ts, controller.ts |
| a fill that failed once | `state.ts` `evaluate()` refuses every later Tab while `failed` is set, even for a fresh suggestion | state.ts |
| Lever location autocomplete | plain text input + hidden `selectedLocation` set only by clicking a result | observation.js (markup unverified) |

## What the scanner recognises (and what it deliberately does not)

`observation.js` is shared with the Playwright filler, which calls it without arguments. The extension calls it with `{widgets: true}`
for the patterns the filler's own upload and dropdown paths do not drive yet (a hidden file input inside a plain drop area, a
`<button aria-haspopup="listbox">` dropdown, two `<button aria-pressed>` reading Yes/No): see `scanner_scenarios.py`
`observer_default_output_is_what_the_playwright_filler_always_got`. Naming fixes that are strictly better for both callers (a custom
radio is named by its own text, the question of a radio group in a table layout is the cell before it) are on by default.

## What the scanner lets through (visibility, sensitive fields, text limits)

`visibility_scenarios.py` (pages `visibility_*.html`, `sensitive_fields.html`, `hostile_text.html`, `editor_labels.html`,
`sections_nearest.html`, `jsonld_flood.html`) pins what `src/content/scan.ts` makes of hostile and look-alike markup, with the real content
script:

* **An assist field is something a person can see and click** (`src/content/visibility.ts`): ancestor opacity and `filter: opacity()`,
  every clipping ancestor (following containing blocks), the reachable part of the page, ghost text, and a hit test with `elementsFromPoint`
  that must reach the control, its label or its declared proxy before anything that paints. What a cookie banner or a sticky bar covers
  is not hidden for good; what is off screen can only be judged by its styles until a scroll brings it on screen (then it is hit-tested
  and a changed verdict rescans); a control the person has already been seen to see is not dropped because the popup of the field above
  covers it. `controlIsHumanVisible(el)` / `frameIsHumanVisible(iframe)` are the exports the frame and fill code use; the scenarios also
  call them directly through a test-only bundle (`build-helpers.mjs`, built by `run.py` next to the content bundle).
* **Never assist fields** (`src/content/sensitive.ts`): one-time codes, card, bank and ID numbers, security answers, passwords.
* **Text that leaves the page** is one line per field, capped per field and per scan, and never carries what was typed in an editor.
* `tests/widgets/unit/sensitive.test.mjs` is the node unit test of the pure helpers (classifier, colour alpha, filter opacity).

Known limits: a cover that only paints through `box-shadow`, a pseudo-element, a mask or a blend mode is not seen by `elementsFromPoint`;
a covered control below the fold is accepted until it is scrolled to (the fresh check `Scanner.stillVisible(field)` is for the moment
right before a value is written: Tab, Enter, Replace, a click on Insert and "Fill verified" all end in `Controller.insertField`, which asks it).
That check is strict (a layer of the page over the control refuses the write) with ONE tolerance: a control that was seen before and is now
covered by something inside the OWN block of another question of the scan (`tabstops.ts ownBlock`: the calendar or the list of suggestions
of the field the person just left, which stays open because the Tab that would have closed it went to the assistant) is still written.
A decoy placed inside a neighbour's block would pass as well; the chip (question and value) and the confirmation row of "Fill verified" are
what the person sees then.

"Fill verified" does not fill at once any more: `press_fill_verified(page)` (tests/content/harness.py, re-exported by `widget_rig`) presses it
and confirms the row the panel lists (SEC-01). The rig does not call a page "ready" before one field was scanned (TEST-01), and its fixture
servers listen with a backlog of 256 (the default of 5 reset connections under load and left pages empty).
