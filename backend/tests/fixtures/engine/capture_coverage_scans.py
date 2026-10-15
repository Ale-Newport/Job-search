"""Regenerate ``coverage_scans.json``: the controls the content script REALLY sends for every fixture page of the repository.

    cd extension && node build.mjs --variant=e2e --out=<private dir> && cd .. &&
    .venv/bin/python backend/tests/fixtures/engine/capture_coverage_scans.py <private dir> [output.json] [page.html,page2.html]

Runs the content-script rig (``extension/tests/content``: real Chromium, the bundled ``observation.js`` + ``scan.ts``, a mock extension
background) over every html fixture of ``backend/tests/browser/fixtures``, ``extension/tests/fixtures``, ``extension/tests/content/fixtures``,
``extension/tests/widgets/fixtures`` and ``backend/tests/fixtures/extension``, records every ``suggest`` request the page sends, keeps the
controls (defaults dropped: ``ElementIn`` restores them; options capped at 40; ports and document ids normalised) and writes the file
``tests/test_engine_coverage.py`` reads. Nothing is sent anywhere but 127.0.0.1 fixture servers. Re-run it when ``scan.ts`` or
``observation.js`` changes the shape of an element, or when a fixture page is added.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "extension" / "tests" / "content"))
import harness as H  # noqa: E402

DIRS = [("ext", REPO / "extension" / "tests" / "fixtures"), ("content", REPO / "extension" / "tests" / "content" / "fixtures"),
        ("widgets", REPO / "extension" / "tests" / "widgets" / "fixtures"), ("backend", REPO / "backend" / "tests" / "browser" / "fixtures"),
        ("ext_backend", REPO / "backend" / "tests" / "fixtures" / "extension")]
EXTRA = {"spa_wizard.html": ["?step=2"], "ashby_apply.html": ["?long=1"], "workday_apply.html": ["?bare=1", "?tail=field"]}
SKIP = {"recaptcha_anchor.html", "recaptcha_badge.html"}
FRAMING = {"workday_apply.html", "workday.html", "tall_iframe.html"}  # pages whose frames must be waited for
DEFAULTS = {"role": "", "tag": "", "type": "", "explicit_type": None, "label": "", "context": "", "description": "", "placeholder": "",
            "section_id": "", "section_label": "", "form_id": "", "group": "", "field_path": "", "name": "", "id": "", "autocomplete": "",
            "required": False, "enabled": True, "readonly": False, "multiple": False, "checked": False, "expanded": False,
            "has_value": False, "value": "", "selected_text": "", "selected_values": [], "max_length": None, "min_length": None,
            "pattern": "", "min": None, "max": None, "step": None, "accept": "", "operations": [], "options": [],
            "phone_country_code": None, "phone_national_only": False, "auxiliary_control": False, "visible_label": "", "position": None}


def pages(only: set[str] | None) -> list[tuple[str, str, str]]:
    found, seen = [], set()
    for tag, folder in DIRS:
        for path in sorted(folder.glob("*.html")):
            if path.name.startswith("_") or path.name in SKIP or path.name in seen or (only and path.name not in only):
                continue
            seen.add(path.name)
            found.append((tag, path.name, ""))
            found.extend((tag, path.name, query) for query in EXTRA.get(path.name, []))
    return found


async def capture(build_dir: Path, only: set[str] | None) -> dict:
    H.CONTENT_JS = build_dir / "content.js"
    H.FIXTURE_DIRS[:] = [folder for _, folder in DIRS]
    servers = H.Servers()
    rig = H.Rig("chromium", servers)
    await rig.start()
    out: dict = {}
    try:
        for tag, name, query in pages(only):
            try:
                framed = name in FRAMING or name.startswith(("frames_top", "hidden_frames"))
                page, bg = await rig.open(name + query, H.Bg(), activate=True, ready=False, wait_frames=1 if framed else 0)
                await asyncio.sleep(4.0 if framed else 1.8)
                out[name + query] = {"dir": tag, "title": await page.title(), "requests": bg.suggest_requests()}
                print(f"{name + query:48} requests={len(out[name + query]['requests'])}", flush=True)
            except Exception as exc:  # noqa: BLE001 - a page that does not load is reported, never fatal
                print(f"{name + query:48} FAILED {exc!r}", flush=True)
            finally:
                await rig.reset()
    finally:
        await rig.stop()
        servers.close()
    return out


def prune(data: dict) -> dict:
    result = {}
    for name in sorted(data):
        seen, requests = set(), []
        for request in data[name]["requests"]:
            elements = []
            for element in request["elements"]:
                key = (request["frame_url"], element["node_id"], element.get("label"))
                if key in seen:
                    continue
                seen.add(key)
                kept = {"node_id": element["node_id"]}
                for field, default in DEFAULTS.items():
                    value = element.get(field, default)
                    if field == "options":
                        value = [{k: v for k, v in option.items() if not (k == "disabled" and not v)} for option in value[:40]]
                    if value != default:
                        kept[field] = value
                elements.append(kept)
            if elements:
                frame_url = re.sub(r"http://(?:127\.0\.0\.1|localhost):\d+", "http://127.0.0.1:1", request["frame_url"])
                requests.append({"frame_key": request.get("frame_key", "top"), "frame_url": frame_url, "elements": elements})
        if requests:
            result[name] = {"dir": data[name]["dir"], "title": data[name]["title"], "requests": requests}
    return result


if __name__ == "__main__":
    only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    captured = prune(asyncio.run(capture(Path(sys.argv[1]).resolve(), only)))
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "coverage_scans.json"
    about = json.loads((HERE / "coverage_scans.json").read_text())["about"] if (HERE / "coverage_scans.json").exists() else ""
    target.write_text(json.dumps({"about": about, "pages": captured}, separators=(",", ":"), sort_keys=True))
    print(len(captured), "pages", sum(len(r["elements"]) for p in captured.values() for r in p["requests"]), "elements")
