"""Regenerate ``d1_scans.json``: the SuggestRequests the content script REALLY sends (scan.ts + the bundled observer).

    cd extension && node build.mjs --variant=e2e --out=<private dir> && cd .. &&
    .venv/bin/python backend/tests/fixtures/extension/capture_scans.py <private dir> [output.json]

Runs the content-script rig (extension/tests/content: real Chromium, mock extension background) over the rig's fixtures,
the repository's ATS fixtures and ``conditional.html`` next to this file, records every ``suggest`` the page sends, and
writes ``d1_scans.json`` here (or the given output path). Nothing is sent anywhere but 127.0.0.1 fixture servers. Re-run it when
``extension/src/content/scan.ts`` or ``backend/jobagent/automation/observation.js`` changes the shape of an element.
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

PAGES = ["choices.html", "combobox.html", "greenhouse.html", "lever.html", "ashby_trainline.html", "inline_questions.html"]


async def capture(build_dir: Path) -> dict:
    H.CONTENT_JS = build_dir / "content.js"
    H.FIXTURE_DIRS.insert(0, HERE)
    servers = H.Servers()
    rig = H.Rig("chromium", servers)
    await rig.start()
    pages: dict[str, list] = {}
    try:
        for name in PAGES:
            _, bg = await rig.open(name, H.Bg(), activate=True, ready=False)
            await asyncio.sleep(2.0)
            pages[name] = bg.suggest_requests()
        page, bg = await rig.open("conditional.html", H.Bg(), activate=True, ready=False)
        await asyncio.sleep(1.5)
        await page.select_option("#sponsor", "yes")  # reveals "If yes, ..." (a refresh listing the follow-up first)
        await asyncio.sleep(1.5)
        await page.check("input[name=auth][value=no]")  # a radio group parent (ONE element in the scanner's convention)
        await asyncio.sleep(1.5)
        await page.select_option("#sponsor", "no")
        await asyncio.sleep(1.5)
        pages["conditional.html"] = bg.suggest_requests()
    finally:
        await rig.stop()
        servers.close()
    return pages


def normalise(request: dict, number: int) -> dict:
    request = json.loads(json.dumps(request))
    request["frame_url"] = re.sub(r"http://127\.0\.0\.1:\d+", "http://127.0.0.1:1", request["frame_url"])
    request["document_id"] = f"doc-{number}"
    return request


if __name__ == "__main__":
    pages = asyncio.run(capture(Path(sys.argv[1]).resolve()))
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "d1_scans.json"
    previous = HERE / "d1_scans.json"
    about = json.loads(previous.read_text())["about"] if previous.exists() else ""
    out = {"about": about, "pages": {k: [normalise(r, i) for r in v] for i, (k, v) in enumerate(pages.items())}}
    target.write_text(json.dumps(out, indent=1))
    print({k: len(v) for k, v in out["pages"].items()})
