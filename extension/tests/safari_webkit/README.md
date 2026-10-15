# Safari-like extension harness (WebKit's own web-extension runtime)

`run.py` + `harness.swift` load the built `safari` variant of the extension into **WKWebExtensionController** (the engine behind Safari's web
extensions), open a fixture page, click the extension's toolbar action, press **Connect**, approve the pairing through the app API and print what the panel and
the chip show, field by field (`WK_STEPS=steps.json` focuses fields and presses Tab/Enter). It runs against a REAL backend on a temporary
data directory (never the installed app: its own bridge port is 47690, the app's is 47615).

```
.venv/bin/python extension/tests/safari_webkit/run.py                      # pairing and the panel on a Greenhouse-style form
WK_STEPS=my_steps.json .venv/bin/python extension/tests/safari_webkit/run.py local_assist.html 120
MERIDIAN_LOCAL_ENGINES=1 ...                                              # with the REAL Laya and Ollama of this Mac (start them first)
.venv/bin/python extension/tests/safari_webkit/run.py --options            # does the options page render?
```

Why it exists: Safari cannot be driven from a script, and Chromium cannot show Safari's rules. This harness reproduced, on the author's
machine and before any user reported them, the two bugs that made the first packaged Safari extension useless:

* a POST with `referrerPolicy: "no-referrer"` goes out with `Origin: null` (Safari's `no-cors` extension fetches), which the bridge refuses;
* IndexedDB opens inside the worker but its transactions never answer, so the sealed credential vault hung the pairing after approval.

Limits (be honest about them): it is not Safari. The origin scheme is `webkit-extension://`, so the run uses a copy of the backend that also accepts
that scheme; Safari's per-site permission prompts, the toolbar and the Develop menu do not exist here; an options page needs the context's own web
view configuration (a plain web view shows it blank, which is what first looked like a Safari bug). A pass here means the worker, content script and
bridge protocol work in WebKit; the first run in real Safari is still the last check.
