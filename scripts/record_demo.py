"""Record the README demo (docs/media/demo.gif) and screenshots from the real app on an isolated, synthetic stack.

  .venv/bin/python scripts/record_demo.py            # writes docs/media/demo.gif and docs/media/*.png
  .venv/bin/python scripts/record_demo.py --keep     # also keeps the raw .webm recording next to them

What runs: the real backend on a temporary data directory and the real interface through Vite (frontend/tests/live_stack.py),
the synthetic candidate of the extension test suite, and a handful of fictional vacancies on .invalid addresses created
through the public API. Nothing touches your Meridian data folder, Keychain, email or any employer website, and nothing is
left running. Needs Google Chrome (or Playwright Chromium) and ffmpeg (brew install ffmpeg).
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "frontend" / "tests"))

import live_stack  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

OUT = REPO / "docs" / "media"
VIEWPORT = {"width": 1280, "height": 800}
HEADERS = {"Authorization": f"Bearer {live_stack.DEV_TOKEN}"}

# Fictional employers and roles. Every URL is on the reserved .invalid domain, so nothing can be opened by mistake.
JOBS = [
    ("Graduate Software Engineer", "Northwind Robotics", "London, United Kingdom", "hybrid", ["Python", "TypeScript", "React", "SQL"]),
    ("Junior Backend Engineer (Python)", "Lumen Health", "London, United Kingdom", "hybrid", ["Python", "FastAPI", "PostgreSQL"]),
    ("Software Engineer, New Grad", "Tidal Payments", "Dublin, Ireland", "onsite", ["Go", "Python", "Kubernetes"]),
    ("Graduate Data Engineer", "Brightline Energy", "Manchester, United Kingdom", "hybrid", ["Python", "SQL", "Airflow"]),
    ("Frontend Engineer (Graduate)", "Atlas Learning", "Remote, United Kingdom", "remote", ["TypeScript", "React", "CSS"]),
    ("Machine Learning Engineer, Early Career", "Orbital Analytics", "Madrid, Spain", "hybrid", ["Python", "PyTorch", "SQL"]),
    ("Associate Platform Engineer", "Harbor Logistics", "Amsterdam, Netherlands", "hybrid", ["Python", "Terraform", "AWS"]),
    ("Graduate Full-Stack Developer", "Kestrel Studio", "Bristol, United Kingdom", "onsite", ["TypeScript", "Node.js", "React"]),
]


def description(title: str, company: str, skills: list[str]) -> str:
    return (
        f"{company} is hiring a {title}. This is a fictional vacancy used to record the Meridian demo. "
        f"You will build and ship production features with {', '.join(skills)}, review code with your team and learn "
        "from experienced engineers. Requirements: a degree in Computer Science or a related subject, or equivalent "
        f"experience; familiarity with {skills[0]} and {skills[1]}. Graduate and entry-level candidates are welcome."
    )


async def seed_jobs(request, base: str) -> list[dict]:
    created = []
    posted = datetime.now(timezone.utc) - timedelta(days=1)
    for index, (title, company, location, arrangement, skills) in enumerate(JOBS):
        slug = company.lower().replace(" ", "-")
        response = await request.post(base + "/api/jobs", headers=HEADERS, data={
            "title": title, "company": company, "location": location, "work_arrangement": arrangement,
            "url": f"https://careers.{slug}.invalid/jobs/{index + 1}", "description": description(title, company, skills),
            "skills": skills, "experience_level": "entry", "contract_types": ["full_time"], "source": "manual",
            "posted_at": (posted - timedelta(hours=index * 5)).isoformat(),
        })
        assert response.ok, await response.text()
        created.append(await response.json())
    # Two of them were applied to "externally" a few days ago: manual tracking records only, no website involved.
    for job, days in ((created[1], 6), (created[3], 3)):
        applied = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        response = await request.post(base + f"/api/jobs/{job['id']}/record-application", headers=HEADERS,
                                      data={"applied_at": applied, "notes": "Applied on the company site."})
        assert response.ok, await response.text()
    return created


async def pause(page, seconds: float) -> None:
    await page.wait_for_timeout(int(seconds * 1000))


async def glide(page, pixels: int, steps: int = 12) -> None:
    """Scroll the page content smoothly with the mouse wheel, as a person would."""
    await page.mouse.move(VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.6)
    for _ in range(steps):
        await page.mouse.wheel(0, pixels / steps)
        await page.wait_for_timeout(45)


async def nav(page, name: str) -> None:
    await page.get_by_role("navigation").get_by_role("button", name=name, exact=True).click()
    await page.wait_for_load_state("networkidle")


async def record(video_dir: Path, base: str) -> Path:
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001 - Chrome is optional, Playwright's Chromium works too
            browser = await p.chromium.launch(headless=True)
        setup = await browser.new_context(viewport=VIEWPORT)
        await seed_jobs(setup.request, base)
        await setup.close()
        context = await browser.new_context(viewport=VIEWPORT, record_video_dir=str(video_dir), record_video_size=VIEWPORT,
                                            color_scheme="light", reduced_motion="no-preference")
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(base)
        await page.wait_for_load_state("networkidle")
        await pause(page, 2.0)
        await page.screenshot(path=str(OUT / "overview.png"))
        await glide(page, 560)
        await pause(page, 1.8)
        for name, hold, shot in SCENES:
            await nav(page, name)
            await pause(page, hold)
            if shot:
                await page.screenshot(path=str(OUT / f"{shot}.png"))
            await glide(page, 420)
            await pause(page, 1.2)
        await nav(page, SCENES[0][0])
        await pause(page, 1.0)
        video = await page.video.path()
        await context.close()
        await browser.close()
        if errors:
            raise AssertionError("page errors while recording:\n" + "\n".join(errors))
        return Path(video)


# (navigation button, seconds on screen, screenshot name or None)
SCENES: list[tuple[str, float, str | None]] = []


def to_gif(video: Path, target: Path, width: int = 880, fps: int = 8) -> None:
    palette = video.with_suffix(".png")
    scale = f"fps={fps},scale={width}:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-vf", f"{scale},palettegen=max_colors=96:stats_mode=diff",
                    str(palette)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video), "-i", str(palette), "-lavfi",
                    f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", "-loop", "0",
                    str(target)], check=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="keep the raw .webm recording in docs/media")
    parser.add_argument("--scenes", default="Opportunities,Tracker,Profile,Analytics",
                        help="comma-separated navigation buttons to visit after the overview")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required: brew install ffmpeg")
    SCENES.extend((name.strip(), 1.8, name.strip().lower()) for name in args.scenes.split(",") if name.strip())
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="meridian-demo-"))
    try:
        async with live_stack.LiveStack(profile=True, seed=True, bridge=False) as stack:
            video = await record(work, stack.base_url)
        to_gif(video, OUT / "demo.gif")
        if args.keep:
            shutil.copy(video, OUT / "demo.webm")
        print(f"wrote {OUT / 'demo.gif'} ({(OUT / 'demo.gif').stat().st_size // 1024} KiB)")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
