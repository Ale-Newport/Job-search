"""Render the toolbar/store icons of the extension from the Meridian symbol (frontend/public/brand).

    .venv/bin/python extension/scripts/render_icons.py

Writes extension/icons/icon-{16,32,48,128}.png: the symbol in mint and sand on a rounded forest plate, so the icon
reads on light and dark toolbars alike. Uses the Chromium bundled with Playwright; no network, no extra packages.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SYMBOL = ROOT / "frontend" / "public" / "brand" / "meridian-symbol-light.svg"
OUT = ROOT / "extension" / "icons"
FOREST = "#173F36"
SIZES = {16: (1.20, 26), 32: (1.14, 28), 48: (1.10, 28), 128: (1.06, 28)}  # size -> (symbol scale, plate radius)


def symbol_paths() -> str:
    text = SYMBOL.read_text(encoding="utf-8")
    inner = re.search(r"</title>(.*)</svg>", text, re.S)
    if not inner:
        raise SystemExit(f"unexpected symbol file: {SYMBOL}")
    return inner.group(1).strip()


def icon_svg(size: int, scale: float, radius: int, paths: str) -> str:
    # The symbol occupies x 19..109, y 16..110 of a 128 box: centre it on the plate.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 128 128">'
        f'<rect width="128" height="128" rx="{radius}" fill="{FOREST}"/>'
        f'<g transform="translate(64 64) scale({scale}) translate(-64 -63)">{paths}</g></svg>'
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = symbol_paths()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chromium", headless=True)
        try:
            for size, (scale, radius) in SIZES.items():
                page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
                page.set_content(
                    f'<!doctype html><body style="margin:0;background:transparent">{icon_svg(size, scale, radius, paths)}</body>'
                )
                target = OUT / f"icon-{size}.png"
                page.screenshot(path=str(target), omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
                page.close()
                print(f"wrote {target.relative_to(ROOT)}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
