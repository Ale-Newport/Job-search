#!/usr/bin/env python
"""Preview rig and assertions for the Meridian Assistant visual layer (chip, panel, toasts, prompts, i18n).

Builds extension/tests/ui/rig.ts (which mounts the real createUI()) into a scratch directory, serves it on a loopback
OS-assigned port and drives it with Playwright (bundled Chromium, channel="chromium"). Every scripted state is rendered
in English and Spanish on light and dark pages, at desktop, 360 px and 300 px (iframe-sized) widths; screenshots are
cropped around the UI and assembled into contact sheets for visual review.

Besides the scene matrix (desktop, 360 px, a 300 px frame and 200%/400% zoom) it runs the behaviour tests: real keyboard
and mouse, click-through chips, persistent field markers, the panel's look through the worker messages, announcements,
forced colours, print, RTL pages, hostile ancestors, shadow-DOM dialogs and CSS isolation. `--browser webkit` repeats
everything in Playwright's WebKit, the closest stand-in for Safari available here.

Usage (from anywhere):
    .venv/bin/python extension/tests/ui/run.py [--shots DIR] [--quick] [--only SUBSTRING] [--browser webkit] [--skip-build]
Exit status is non-zero when any assertion fails.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from playwright.async_api import Page, async_playwright  # noqa: E402

from behaviours import run_behaviours  # noqa: E402
from harness import RESPONSIVE, WIDE, ZOOM, Report, leaked, open_page, overlap  # noqa: E402

EXT = HERE.parents[1]
UI_SRC = EXT / "src" / "content" / "ui"
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:"


# ---- local server ---------------------------------------------------------------------------------------------------
class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        if path.startswith("/csp/"):
            path = path[4:]
        return super().translate_path(path)

    def end_headers(self) -> None:
        if self.path.startswith("/csp/"):
            self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args: object) -> None:  # silence
        return


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


# ---- static checks on the sources -----------------------------------------------------------------------------------
def static_checks(report: Report) -> None:
    files = sorted(UI_SRC.glob("*.ts")) + [EXT / "src" / "content" / "i18n.ts"]
    banned = re.compile(
        r"\binnerHTML\b|\bouterHTML\b|insertAdjacentHTML|document\.write|\beval\(|new Function\(|dangerouslySet|createContextualFragment|DOMParser"
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if banned.search(line) and not line.lstrip().startswith("//"):
                report.fail(f"static: {path.name}:{number} uses HTML-parsing API: {line.strip()[:80]}")
        for match in re.finditer(r'addEventListener\(\s*"click"', text):
            window = text[match.start() : match.start() + 220]
            report.check(
                "isTrusted" in window,
                f"static: {path.name} click listener without isTrusted check near offset {match.start()}",
            )
        report.check(
            "localStorage" not in text and "sessionStorage" not in text,
            f"static: {path.name} touches page-origin storage",
        )
        report.check(
            "fetch(" not in text and "XMLHttpRequest" not in text, f"static: {path.name} performs network access"
        )
        report.check(
            "chrome.storage" not in text and "browser.storage" not in text,
            f"static: {path.name} reads extension storage (closed to content scripts; the panel's look goes through the worker)",
        )
    # every key in the English dictionary is used somewhere (dynamic families are allow-listed)
    i18n = (EXT / "src" / "content" / "i18n.ts").read_text(encoding="utf-8")
    keys = re.findall(r'^\s*"([a-z_]+(?:\.[A-Za-z_]+)+)":', i18n, flags=re.M)
    sources = "\n".join(p.read_text(encoding="utf-8") for p in sorted(UI_SRC.glob("*.ts")))
    dynamic = (
        "why.",
        "reason.",
        "gen.",
        "toast.",
        "conn.",
        "research.status.",
        "research.quality.",
        "research.done.",
        "progress.status.",
        "chip.stale.",
        "chip.skip.",
    )
    unused = [k for k in dict.fromkeys(keys) if f'"{k}"' not in sources and not k.startswith(dynamic)]
    report.check(not unused, f"static: unused i18n keys: {unused}")


# ---- scene runner ---------------------------------------------------------------------------------------------------
async def run_scene(page: Page, scene: dict, cfg: dict, report: Report, shots: Path | None) -> None:
    tag = f"{scene['id']} [{cfg['name']}]"
    # a scene is its own page: what an earlier scene made the fake worker remember (the dock side of panel-dock-left) must not leak into it
    await page.evaluate("localStorage.removeItem('__fake_worker_ui')")
    info = await page.evaluate("([id, lang]) => rig.run(id, { lang })", [scene["id"], cfg["lang"]])
    for testid in scene["clicks"]:
        await page.locator(f'[data-testid="{testid}"]').first.click()
    if scene.get("typing"):  # real typing: the chip shrinks to a pill only for trusted input events
        await page.focus(f"#{scene['typing']['id']}")
        await page.keyboard.type(scene["typing"]["text"])
    await page.wait_for_timeout(170)
    audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", [scene["id"], cfg["lang"]])
    report.scenes += 1
    report.min_contrast = min(report.min_contrast, audit["minContrast"])
    for key in ("contrast", "overflow", "focusable", "names", "testids", "roles", "links", "clickthrough", "expect"):
        for item in audit[key]:
            report.fail(f"{tag}: {key}: {item}")
    if await page.evaluate("rig.pwned()") is not None:
        report.fail(f"{tag}: page script executed through UI text (window.__pwned)")
    viewport = audit["viewport"]
    chip = audit["chip"]
    anchor = info["anchor"]
    if chip:
        inside = (
            chip["x"] >= -0.5
            and chip["y"] >= -0.5
            and chip["x"] + chip["w"] <= viewport["w"] + 0.5
            and chip["y"] + chip["h"] <= viewport["h"] + 0.5
        )
        report.check(inside, f"{tag}: chip outside the viewport {chip} in {viewport}")
        # a tiny viewport (400% zoom, a short iframe) may force the chip over a field that fills it; nowhere else
        tiny = viewport["h"] <= 300
        expected_overlay = (scene.get("expect") or {}).get("placement") == "overlay" or (
            tiny and audit["chipPlacement"] == "overlay"
        )
        if (
            scene["id"] != "chip-follow-scroll"
            and not expected_overlay
            and scene["id"].startswith(("chip-", "matrix-"))
        ):
            report.check(overlap(chip, anchor) < 1, f"{tag}: chip covers the focused field {chip} vs {anchor}")
    for mark in audit["marks"]:
        if mark["placement"] == "inset":
            continue  # no room anywhere around the field: the badge may sit inside it
        # the field's text zone: the box inset by 8 px. A marker may overhang the corner but never covers it.
        zone = {
            "x": anchor["x"] + 8,
            "y": anchor["y"] + 8,
            "w": max(0, anchor["w"] - 16),
            "h": max(0, anchor["h"] - 16),
        }
        if scene["id"].startswith("mark-") and scene["id"] not in ("mark-tones", "mark-with-chip"):
            report.check(overlap(mark, zone) < 1, f"{tag}: the marker covers the field's text area {mark} vs {anchor}")
    touches = await page.evaluate("window.__bgStats.storageTouches")
    report.check(
        touches == 0, f"{tag}: the UI touched chrome.storage {touches} time(s); content scripts cannot read it"
    )
    vocab = await page.evaluate("(lang) => rig.vocabulary(lang)", cfg["lang"])
    data = await page.evaluate("rig.dataStrings()")
    bad = leaked(audit["texts"], data, vocab)
    report.check(not bad, f"{tag}: words outside the {cfg['lang']} dictionary (hard-coded strings?): {bad}")
    errors = getattr(page, "rig_errors", [])
    if errors:
        report.fail(f"{tag}: browser console: {errors[:3]}")
        errors.clear()
    if shots is not None and not scene["noShot"] and audit["bounds"]:
        box = dict(audit["bounds"])
        if scene["id"].startswith(("chip-", "matrix-", "flash-", "mark-")):
            left, top = min(box["x"], anchor["x"]), min(box["y"], anchor["y"])
            right, bottom = (
                max(box["x"] + box["w"], anchor["x"] + anchor["w"]),
                max(box["y"] + box["h"], anchor["y"] + anchor["h"]),
            )
            box = {"x": left, "y": top, "w": right - left, "h": bottom - top}
        pad = 14
        if scene["fullShot"]:
            box = {"x": 0, "y": 0, "w": viewport["w"], "h": viewport["h"]}
            pad = 0
        x, y = max(0, box["x"] - pad), max(0, box["y"] - pad)
        w, h = min(viewport["w"] - x, box["w"] + 2 * pad), min(viewport["h"] - y, box["h"] + 2 * pad)
        target = shots / cfg["name"]
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{scene['id']}.png"
        motion = "allow" if scene["id"].startswith("flash") else "disabled"  # the flash fades out by itself
        await page.screenshot(
            path=str(path), clip={"x": x, "y": y, "width": max(w, 40), "height": max(h, 30)}, animations=motion
        )
        report.shots.append((cfg["name"], scene["id"], path))


async def run_scenes(browser, base: str, report: Report, shots: Path | None, only: str | None, quick: bool) -> None:
    probe_ctx, probe = await open_page(browser, base, WIDE[0])
    scenes = await probe.evaluate("rig.scenes()")
    await probe_ctx.close()
    if only:
        scenes = [s for s in scenes if only in s["id"]]
    configs = WIDE[:1] if quick else WIDE + RESPONSIVE + ZOOM
    for cfg in configs:
        ctx, page = await open_page(browser, base, cfg)
        for scene in scenes:
            if scene["group"] == "matrix" and cfg["name"] != "wide-en-light" and cfg["name"] != "wide-es-dark":
                continue
            if cfg in RESPONSIVE and not scene["responsive"]:
                continue
            if cfg in ZOOM and not scene["zoom"]:
                continue
            await run_scene(page, scene, cfg, report, shots)
        await ctx.close()


# ---- contact sheets -------------------------------------------------------------------------------------------------
def contact_sheets(report: Report, out: Path) -> list[Path]:
    from PIL import Image, ImageDraw

    groups: dict[tuple[str, str], list[tuple[str, Path]]] = {}
    for cfg, scene_id, path in report.shots:
        prefix = scene_id.split("-")[0] if scene_id.startswith("matrix") else "-".join(scene_id.split("-")[:2])
        groups.setdefault((cfg, prefix), []).append((scene_id, path))
    sheets: list[Path] = []
    for (cfg, prefix), items in sorted(groups.items()):
        tile_w, label_h, gap, cols = 460, 18, 10, 3
        chunk = 9
        for part, start in enumerate(range(0, len(items), chunk)):
            batch = items[start : start + chunk]
            tiles = []
            for scene_id, path in batch:
                with Image.open(path) as img:
                    scale = min(1.0, tile_w / img.width)
                    tile = img.convert("RGB").resize(
                        (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS
                    )
                tiles.append((scene_id, tile))
            col_heights = [0] * cols
            placed = []
            for scene_id, tile in tiles:
                col = col_heights.index(min(col_heights))
                placed.append((col, col_heights[col], scene_id, tile))
                col_heights[col] += tile.height + label_h + gap
            sheet = Image.new("RGB", (cols * (tile_w + gap) + gap, max(col_heights) + gap), (122, 126, 133))
            draw = ImageDraw.Draw(sheet)
            for col, y, scene_id, tile in placed:
                x = gap + col * (tile_w + gap)
                draw.text((x, y + gap), scene_id, fill=(255, 255, 255))
                sheet.paste(tile, (x, y + gap + label_h))
            path = out / f"sheet-{cfg}-{prefix}-{part + 1}.png"
            sheet.save(path)
            sheets.append(path)
    return sheets


# ---- entry point ----------------------------------------------------------------------------------------------------
async def main_async(args: argparse.Namespace, workdir: Path, shots: Path | None) -> Report:
    report = Report()
    static_checks(report)
    typecheck = run([str(EXT / "node_modules" / ".bin" / "tsc"), "--noEmit", "-p", str(HERE / "tsconfig.json")], EXT)
    report.check(
        typecheck.returncode == 0,
        f"typecheck of the rig and the UI sources failed:\n{typecheck.stdout[-1500:]}{typecheck.stderr[-500:]}",
    )
    if not args.skip_build:
        built = run(["node", str(HERE / "build-rig.mjs"), f"--out={workdir}"], EXT)
        report.check(built.returncode == 0, f"rig build failed: {built.stdout[-800:]}{built.stderr[-800:]}")
        if built.returncode != 0:
            return report
    server, base = serve(workdir)
    try:
        async with async_playwright() as pw:
            if args.browser == "webkit":
                browser = await pw.webkit.launch(headless=True)
            else:
                browser = await pw.chromium.launch(channel="chromium", headless=True)
            try:
                if not args.behaviour:
                    await run_scenes(browser, base, report, shots, args.only, args.quick)
                if not args.only:
                    await run_behaviours(browser, base, report, shots, args.behaviour, engine=args.browser)
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--shots", help="directory for screenshots and contact sheets (default: discarded)")
    parser.add_argument("--quick", action="store_true", help="only the desktop English/light configuration")
    parser.add_argument("--only", help="only scenes whose id contains this text (skips the behaviour tests)")
    parser.add_argument(
        "--behaviour", help="only behaviour tests whose name contains this text (skips the scene matrix)"
    )
    parser.add_argument(
        "--browser",
        choices=("chromium", "webkit"),
        default="chromium",
        help="engine to run in; webkit is the closest stand-in for Safari that Playwright offers",
    )
    parser.add_argument("--skip-build", action="store_true", help="reuse an existing build in --workdir")
    parser.add_argument("--workdir", help="scratch build directory (default: a temporary directory)")
    args = parser.parse_args()
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="meridian-ui-rig-"))
    workdir.mkdir(parents=True, exist_ok=True)
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    try:
        report = asyncio.run(main_async(args, workdir, shots))
        if shots and report.shots:
            sheets = contact_sheets(report, shots)
            print(f"contact sheets: {len(sheets)} in {shots}")
    finally:
        if not args.workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    print(
        f"scenes rendered: {report.scenes}; assertions: {report.checks}; failures: {len(report.failures)}; "
        f"lowest text contrast: {report.min_contrast:.2f}:1"
    )
    for failure in report.failures[:80]:
        print(f"FAIL {failure}")
    if len(report.failures) > 80:
        print(f"... and {len(report.failures) - 80} more")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
