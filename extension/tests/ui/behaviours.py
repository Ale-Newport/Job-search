"""Behavioural assertions of the UI preview rig: real keyboard and mouse, isolation, accessibility, i18n, timing."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from harness import Report, leaked, open_page

BASE = {"name": "beh", "width": 1100, "height": 700, "lang": "en", "theme": "light"}
DARK = {**BASE, "name": "beh-dark", "theme": "dark"}


@dataclass
class Env:
    browser: object
    base: str
    report: Report
    shots: Path | None
    engine: str = "chromium"

    @contextlib.asynccontextmanager
    async def session(self, cfg: dict | None = None, **kwargs):
        context, page = await open_page(self.browser, self.base, cfg or BASE, **kwargs)
        try:
            yield page
        finally:
            await context.close()


def tab_keys(env: "Env") -> tuple[str, str]:
    """Safari's Tab skips buttons and links unless the user changed a preference; Option+Tab walks every control."""
    return ("Alt+Tab", "Alt+Shift+Tab") if env.engine == "webkit" else ("Tab", "Shift+Tab")


async def show(page, scene: str, lang: str = "en") -> dict:
    info = await page.evaluate("([id, lang]) => rig.run(id, { lang })", [scene, lang])
    await page.wait_for_timeout(170)
    return info


async def actions(page) -> list[dict]:
    return await page.evaluate("rig.actions")


async def click(page, testid: str) -> None:
    await page.locator(f'[data-testid="{testid}"]').first.click()


async def is_visible(page, testid: str) -> bool:
    loc = page.locator(f'[data-testid="{testid}"]')
    return await loc.count() > 0 and await loc.first.is_visible()


# ---- i18n and performance ---------------------------------------------------------------------------------------------
async def test_i18n(env: Env) -> None:
    async with env.session() as page:
        for failure in await page.evaluate("rig.i18nSelfTest()"):
            env.report.fail(f"i18n: {failure}")
        env.report.check(True, "i18n")


async def test_perf(env: Env) -> None:
    async with env.session() as page:
        result = await page.evaluate("rig.perf()")
        env.report.check(result["avgMs"] < 8, f"perf: showChip averages {result['avgMs']:.2f} ms (limit 8)")
        env.report.check(result["maxMs"] < 60, f"perf: slowest showChip took {result['maxMs']:.1f} ms (limit 60)")
        print(f"perf: showChip avg {result['avgMs']:.2f} ms, max {result['maxMs']:.1f} ms")


# ---- keyboard ----------------------------------------------------------------------------------------------------------
async def test_chip_never_in_tab_order(env: Env) -> None:
    async with env.session() as page:
        for scene in ("chip-verified-email", "chip-draft-expanded", "chip-needs-draft-button", "chip-undoable"):
            await show(page, scene)
            for clicked in await page.evaluate("rig.scenes()"):
                if clicked["id"] == scene:
                    for testid in clicked["clicks"]:
                        await click(page, testid)
            await page.focus("#f-first")
            for _ in range(14):
                await page.keyboard.press(tab_keys(env)[0])
                info = await page.evaluate("rig.focusedInfo()")
                env.report.check(
                    not info["inHost"],
                    f"keyboard: Tab landed inside the UI while only a chip was showing ({scene}: {info})",
                )
        env.report.check(True, "chip tab order")


async def tab_through_panel(env: Env, page, scene: str) -> None:
    await show(page, scene)
    tabbables = await page.evaluate("rig.tabbables()")
    expected = [item.split(":")[0] for item in tabbables]
    await page.focus("#fx-text")
    seen: list[str] = []
    entered = False
    for _ in range(len(expected) + 8):
        await page.keyboard.press(tab_keys(env)[0])
        info = await page.evaluate("rig.focusedInfo()")
        if info["inHost"]:
            entered = True
            seen.append(str(info["idx"]))
            ring = info["outline"] or ""
            env.report.check(
                not ring.startswith("none") and "0px" not in ring,
                f"keyboard: no visible focus ring on {info['testid']} in {scene}: {ring}",
            )
        elif entered:
            break
    env.report.check(seen == expected, f"keyboard: {scene}: Tab order {seen} != DOM order {expected}")
    for _ in range(len(expected) + 2):
        await page.keyboard.press(tab_keys(env)[1])
        info = await page.evaluate("rig.focusedInfo()")
        if not info["inHost"]:
            break
    back = await page.evaluate("document.activeElement && document.activeElement.id")
    env.report.check(back == "fx-text", f"keyboard: {scene}: Shift+Tab did not return to the page (landed on {back!r})")


async def test_panel_keyboard(env: Env) -> None:
    async with env.session() as page:
        for scene in (
            "panel-ready-20",
            "ask-both",
            "panel-research-needs-company",
            "panel-conn-pairing",
            "panel-collapsed",
            "panel-orphaned",
            "panel-all-notes",
        ):
            await tab_through_panel(env, page, scene)
        info = await show(page, "panel-more-open")
        del info
        await tab_through_panel(env, page, "panel-more-open")


async def test_focus_panel_shortcut(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        env.report.check(await page.evaluate("rig.ui.focusPanel()"), "focusPanel returned false with a panel mounted")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["inHost"], "focusPanel did not move focus into the UI")
        await show(page, "ask-save")
        await page.evaluate("rig.ui.focusPanel()")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["testid"] == "save-application",
            f"focusPanel should reach the pending prompt first, got {info['testid']}",
        )
        await show(page, "chip-verified-email")
        env.report.check(
            not await page.evaluate("rig.ui.focusPanel()"), "focusPanel should be false when there is nothing to focus"
        )


async def test_focus_not_stolen(env: Env) -> None:
    async with env.session() as page:
        await page.focus("#fx-top")
        await page.evaluate("rig.run('chip-verified-email', { lang: 'en' })")
        await page.wait_for_timeout(170)
        await click(page, "chip-dismiss")
        active = await page.evaluate("document.activeElement && document.activeElement.id")
        env.report.check(
            active == "fx-top", f"focus: clicking a chip button stole focus from the page field (now {active!r})"
        )
        env.report.check(
            {"type": "dismiss", "key": "f0:12"} in await actions(page),
            "focus: dismiss action not emitted by a real click",
        )
        # the panel's buttons and switches do not take focus either: the next Tab must continue from the field
        await show(page, "panel-ready-20")
        await page.focus("#fx-top")
        for testid in ("panel-fill-verified", "panel-more", "panel-keys", "panel-collapse"):
            await click(page, testid)
            active = await page.evaluate("document.activeElement && document.activeElement.id")
            env.report.check(
                active == "fx-top", f"focus: clicking {testid} stole focus from the page field ({active!r})"
            )
        # ... but a text input of the panel does take it, as typing needs
        await show(page, "panel-research-done-full")
        await page.focus("#fx-top")
        await click(page, "research-not-this")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["testid"] == "research-input-company", f"focus: the company input did not take focus ({info})"
        )


async def test_chip_click_through(env: Env) -> None:
    async with env.session() as page:
        await page.evaluate(
            "window.__pageClicks = []; document.getElementById('fx-text').addEventListener('click', () => window.__pageClicks.push(1))"
        )
        await show(page, "chip-verified-email")
        point = await page.evaluate("rig.freePoint()")
        env.report.check(point is not None, "click-through: no non-control pixel found in the chip")
        if point is None:
            return
        under = await page.evaluate(
            "([x, y]) => document.elementFromPoint(x, y) && document.elementFromPoint(x, y).id",
            [point["x"], point["y"]],
        )
        env.report.check(
            under == "fx-text", f"click-through: expected the page's textarea under the chip pixel, found {under!r}"
        )
        await page.mouse.click(point["x"], point["y"])
        env.report.check(
            await page.evaluate("document.activeElement && document.activeElement.id") == "fx-text",
            "click-through: a click on the chip body did not reach the field underneath",
        )
        env.report.check(
            await page.evaluate("window.__pageClicks.length") == 1, "click-through: the page did not receive the click"
        )
        env.report.check(await actions(page) == [], f"click-through: the chip body emitted {await actions(page)}")
        # its real controls still work
        await click(page, "chip-accept")
        env.report.check(
            await actions(page) == [{"type": "accept", "key": "f0:12"}], "click-through: Insert stopped working"
        )
        # the expanded draft is a scroller the reader asked for: it keeps the pointer
        await show(page, "chip-draft-expanded")
        await click(page, "chip-more")
        pointer = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=chip-value]')).pointerEvents"
        )
        env.report.check(
            pointer == "auto", f"click-through: the expanded draft cannot scroll with the wheel ({pointer})"
        )
        await show(page, "chip-draft-long")
        pointer = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=chip-value]')).pointerEvents"
        )
        env.report.check(pointer == "none", f"click-through: the collapsed draft preview takes the pointer ({pointer})")


async def test_chip_avoids_the_next_field(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-avoid-irrelevant")
        pair = await page.evaluate("rig.anchorOf('fx-pair-a')")
        below = (await page.evaluate("rig.audit('chip-avoid-irrelevant', 'en')"))["chip"]
        env.report.check(below["y"] > pair["y"], "avoid: without a neighbour in the way the chip stays below the field")
        # the same field and key, now with the next field known: the chip moves out of its way without being rebuilt
        await page.evaluate("""() => {
          const a = rig.anchorOf('fx-pair-a');
          rig.ui.showChip(rig.make.chipView(a, { avoid: [rig.anchorOf('fx-pair-b')] }));
        }""")
        moved = (await page.evaluate("rig.audit('chip-avoid-next', 'en')"))["chip"]
        next_field = await page.evaluate("rig.anchorOf('fx-pair-b')")
        env.report.check(
            moved["y"] + moved["h"] <= pair["y"] or moved["y"] >= next_field["y"] + next_field["h"],
            f"avoid: the chip still covers the next field {moved} vs {next_field}",
        )
        env.report.check(moved["y"] < pair["y"], "avoid: the chip did not go above the field")
        # scrolling moves the field and its neighbour together: the chip keeps clear of the neighbour at its NEW position
        await page.evaluate(
            "() => { const a = rig.anchorOf('fx-pair-a'); rig.ui.updateChipAnchor({ ...a, y: a.y + 120 }); }"
        )
        scrolled = (await page.evaluate("rig.audit('chip-avoid-next', 'en')"))["chip"]
        shifted = {**next_field, "y": next_field["y"] + 120}
        env.report.check(
            scrolled["y"] + scrolled["h"] <= shifted["y"] or scrolled["y"] >= shifted["y"] + shifted["h"],
            f"avoid: after scrolling the chip covers the next field {scrolled} vs {shifted}",
        )
        # and it does not flip-flop: with the neighbour gone again it may return below
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-pair-a')))")
        again = (await page.evaluate("rig.audit('chip-avoid-next', 'en')"))["chip"]
        env.report.check(again is not None, "avoid: the chip vanished when the hint was dropped")


async def test_typing_in_panel_input(env: Env) -> None:
    async with env.session() as page:
        await page.evaluate("""window.__leak = [];
          for (const type of ['keydown', 'keyup', 'keypress', 'input', 'beforeinput', 'compositionupdate'])
            document.addEventListener(type, (ev) => {
              if (ev.target && ev.target.hasAttribute && ev.target.hasAttribute('data-meridian-assistant')) window.__leak.push(String(ev.key || ev.data || ''));
            });""")
        await show(page, "panel-research-done-full")
        await click(page, "research-not-this")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["testid"] == "research-input-company",
            f"research form: input not focused after opening ({info['testid']})",
        )
        await page.keyboard.type("Globex Corp")
        await page.keyboard.press("Enter")
        env.report.check(
            await actions(page) == [{"type": "research-company", "company": "Globex Corp"}],
            f"research form: company action {await actions(page)}",
        )
        env.report.check(
            not await is_visible(page, "research-form-company"), "research form: form still open after submit"
        )
        leaked_chars = [k for k in await page.evaluate("window.__leak") if len(k) == 1]
        env.report.check(
            not leaked_chars,
            f"privacy: characters typed into the panel input reached page bubble-phase listeners: {leaked_chars}",
        )

        await page.evaluate("rig.resetActions()")
        await click(page, "research-add-site")
        await page.keyboard.type("https://www.Acme-Robotics.example/careers?x=1")
        await page.keyboard.press("Enter")
        env.report.check(
            await actions(page) == [{"type": "research-site", "domain": "www.acme-robotics.example"}],
            f"research form: site action {await actions(page)}",
        )

        await page.evaluate("rig.resetActions()")
        await click(page, "research-add-site")
        await page.keyboard.type("not a website")
        await page.keyboard.press("Enter")
        env.report.check(await actions(page) == [], "research form: an invalid site address emitted an action")
        env.report.check(
            await is_visible(page, "research-form-error"), "research form: no inline error for an invalid address"
        )
        await page.keyboard.type("x")
        env.report.check(
            not await is_visible(page, "research-form-error"), "research form: error not cleared while editing"
        )
        await page.keyboard.press("Escape")
        env.report.check(
            not await is_visible(page, "research-form-site"), "research form: Escape did not close the form"
        )
        env.report.check(await actions(page) == [], "research form: Escape emitted an action")


# ---- trust and actions -----------------------------------------------------------------------------------------------
async def test_untrusted_clicks(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate("""() => {
          const root = rig.ui.root;
          const button = root.querySelector('[data-testid="chip-accept"]');
          button.click();
          button.dispatchEvent(new MouseEvent('click', { bubbles: true, composed: true }));
          root.querySelector('[data-testid="chip-dismiss"]').click();
        }""")
        env.report.check(await actions(page) == [], f"trust: synthetic clicks produced actions {await actions(page)}")
        await click(page, "chip-accept")
        env.report.check(
            await actions(page) == [{"type": "accept", "key": "f0:12"}],
            f"trust: real click action {await actions(page)}",
        )


ACTION_CASES = [
    ("chip-verified-email", ["chip-accept"], {"type": "accept", "key": "f0:12"}),
    ("chip-verified-email", ["chip-dismiss"], {"type": "dismiss", "key": "f0:12"}),
    ("chip-protected-explicit", ["chip-accept"], {"type": "accept", "key": "f0:12"}),
    ("chip-conflict", ["chip-replace"], {"type": "replace", "key": "f0:12"}),
    ("chip-undoable", ["chip-undo"], {"type": "undo", "key": "f0:12"}),
    ("chip-needs-draft-button", ["chip-draft"], {"type": "draft", "key": "f0:12"}),
    ("chip-draft-long", ["chip-regen-again"], {"type": "regenerate", "key": "f0:30", "instruction": "again"}),
    ("chip-draft-long", ["chip-regen-shorter"], {"type": "regenerate", "key": "f0:30", "instruction": "shorter"}),
    ("chip-draft-long", ["chip-regen-longer"], {"type": "regenerate", "key": "f0:30", "instruction": "longer"}),
    ("chip-draft-long", ["chip-regen-different"], {"type": "regenerate", "key": "f0:30", "instruction": "different"}),
    ("panel-ready-20", ["panel-fill-verified"], {"type": "fill-verified"}),
    ("panel-ready-20", ["panel-keys"], {"type": "toggle-keys"}),
    ("panel-ready-20", ["panel-collapse"], {"type": "toggle-collapse"}),
    ("panel-ready-20", ["panel-settings"], {"type": "open-options", "anchor": "behaviour"}),
    ("panel-ready-20", ["panel-help"], {"type": "keys-help"}),  # Help is the in-panel cheat sheet since wave 3 (UX-12)
    ("panel-ready-20", ["progress-item"], {"type": "focus-item", "key": "f0:0"}),
    ("panel-ready-20", ["research-toggle", "research-refresh"], {"type": "research-refresh"}),
    ("panel-collapsed", ["panel-pill"], {"type": "toggle-collapse"}),
    ("panel-more-open", ["panel-deactivate"], {"type": "deactivate"}),
    ("panel-more-open", ["panel-always-on"], {"type": "always-on-site"}),
    ("panel-more-open", ["panel-hide-hint"], {"type": "hide-hint-site"}),
    ("panel-conn-no_bridge", ["calm-open-meridian"], {"type": "open-meridian"}),
    ("panel-conn-no_bridge", ["calm-retry"], {"type": "retry-connection"}),
    ("panel-conn-unpaired", ["calm-connect"], {"type": "connect"}),
    ("panel-conn-pairing", ["calm-cancel"], {"type": "cancel-pairing"}),
    ("panel-conn-pairing-expired", ["calm-new-code"], {"type": "connect"}),
    ("panel-conn-revoked", ["calm-connect"], {"type": "connect"}),
    ("panel-conn-proof_failed", ["calm-retry"], {"type": "retry-connection"}),
    ("panel-conn-disabled", ["calm-open-meridian"], {"type": "open-meridian"}),
    ("panel-conn-error", ["calm-retry"], {"type": "retry-connection"}),
    ("panel-profile-empty", ["calm-open-meridian"], {"type": "open-meridian"}),
    ("panel-update-ready", ["calm-update-open"], {"type": "open-options", "anchor": "connection"}),
    ("panel-cross-origin", ["note-allow-all"], {"type": "open-options", "anchor": "permissions"}),
    ("panel-drafts-unavailable", ["note-open-meridian"], {"type": "open-meridian"}),
    ("ask-save", ["save-application"], {"type": "save-answer", "key": "f0:7", "remember": "application"}),
    ("ask-save", ["save-reuse"], {"type": "save-answer", "key": "f0:7", "remember": "reuse"}),
    ("ask-save", ["save-no"], {"type": "save-never", "key": "f0:7"}),
    ("ask-save", ["save-more", "save-always"], {"type": "save-setting", "value": "always"}),
    ("ask-save", ["save-more", "save-never"], {"type": "save-setting", "value": "never"}),
    ("ask-track", ["track-record"], {"type": "track-record"}),
    ("ask-track", ["track-dismiss"], {"type": "track-dismiss"}),
    # wave 3
    ("chip-stale-research", ["chip-rewrite"], {"type": "rewrite", "key": "f0:30"}),
    ("chip-stale-company-rejected", ["chip-rewrite"], {"type": "rewrite", "key": "f0:30"}),
    ("chip-skip-file-unapproved", ["chip-open-meridian"], {"type": "open-meridian"}),
    ("chip-pill-typing", ["chip-replace"], {"type": "replace", "key": "f0:12"}),
    ("panel-more-track", ["panel-track-save"], {"type": "track-save"}),
    ("panel-more-track", ["panel-track-applied"], {"type": "track-applied"}),
    ("panel-duplicate-applied", ["dup-continue"], {"type": "duplicate-continue"}),
    ("panel-duplicate-applied", ["dup-close"], {"type": "deactivate"}),
    ("panel-first-use", ["first-keys"], {"type": "keys-help"}),
    ("panel-review", ["review-item"], {"type": "focus-item", "key": "f0:11"}),
    ("panel-review", ["review-dismiss"], {"type": "review-dismiss"}),
    ("panel-research-needs-site", ["research-cancel-site"], None),
]


async def test_action_mapping(env: Env) -> None:
    async with env.session() as page:
        scenes = {s["id"]: s for s in await page.evaluate("rig.scenes()")}
        for scene, clicks, expected in ACTION_CASES:
            await show(page, scene)
            for testid in scenes[scene]["clicks"] + clicks:
                await click(page, testid)
            got = await actions(page)
            env.report.check(got == ([] if expected is None else [expected]), f"actions: {scene} {clicks} emitted {got}, expected [{expected}]")
        await show(page, "ask-save")
        await click(page, "save-application")
        env.report.check(not await is_visible(page, "save-ask"), "save prompt stayed after the user answered")
        await show(page, "ask-track")
        await click(page, "track-dismiss")
        env.report.check(not await is_visible(page, "track-ask"), "track prompt stayed after the user answered")


async def test_closing_panel_keeps_focus_target(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        await page.focus("#fx-top")
        env.report.check(await page.evaluate("rig.ui.focusPanel()"), "focus: focusPanel found nothing to focus")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["testid"] == "panel-collapse", f"focus: the panel's first control is {info['testid']}")
        await page.keyboard.press("Enter")
        env.report.check({"type": "toggle-collapse"} in await actions(page), "keyboard: Enter on minimise did not emit")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ collapsed: true }))")
        await page.wait_for_timeout(60)
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["testid"] == "panel-pill",
            f"focus: collapsing the panel did not move focus to the pill ({info['testid']})",
        )
        await page.keyboard.press("Enter")
        env.report.check(
            await actions(page) == [{"type": "toggle-collapse"}, {"type": "toggle-collapse"}],
            "keyboard: Enter on the focused pill did not expand the panel",
        )


async def test_focus_kept_when_a_control_disappears(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-conn-unpaired")
        await page.evaluate("rig.ui.focusPanel()")
        await page.keyboard.press(tab_keys(env)[0])
        await page.keyboard.press(tab_keys(env)[0])
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["inHost"], f"focus: could not reach the panel's Connect button by keyboard ({info})")
        await page.evaluate(
            "rig.ui.setPanel(rig.make.panelView({ connection: { phase: 'pairing', pairing: { pairingId: 'p', code: '482913', expiresAt: Date.now() + 90000 } } }))"
        )
        await page.wait_for_timeout(60)
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["inHost"],
            "focus: Connect disappeared and the keyboard fell out of the panel (next Tab would start at the page top)",
        )


# ---- timing: dwell, undo, countdown, toasts, announcements --------------------------------------------------------------
DRAFT_VIEW = """(dwell) => {
  const anchor = rig.anchorOf('fx-text');
  return rig.make.chipView(anchor, { key: 'd1', state: 'GENERATED_DRAFT', tone: 'draft', value: 'A short validated draft.', dwellRemainingMs: dwell,
    canRegenerate: true, keys: ['tab', 'shift-tab', 'regenerate', 'esc'], whyCode: 'generated', whySummary: 'Drafted', fieldLabel: 'Why Acme?' });
}"""


async def test_dwell(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-skip")
        await page.evaluate(f"(dwell) => rig.ui.showChip(({DRAFT_VIEW})(dwell))", 450)
        await page.wait_for_timeout(40)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelectorAll('.hint.dim').length") == 1,
            "dwell: the Tab hint is not dimmed while the draft is still new",
        )
        env.report.check(await is_visible(page, "chip-dwell"), "dwell: no countdown indicator")
        await page.wait_for_timeout(200)
        await page.evaluate(
            f"(dwell) => rig.ui.showChip(({DRAFT_VIEW})(dwell))", 450
        )  # same view again must not restart the dwell
        await page.wait_for_timeout(420)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelectorAll('.hint.dim').length") == 0,
            "dwell: Tab hint still dimmed after the dwell elapsed (or the dwell restarted)",
        )
        env.report.check(
            not await is_visible(page, "chip-dwell"), "dwell: indicator still visible after the dwell elapsed"
        )
        await page.evaluate(f"(dwell) => rig.ui.showChip(({DRAFT_VIEW})(dwell))", 450)
        await page.wait_for_timeout(60)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelectorAll('.hint.dim').length") == 0,
            "dwell: a stale re-render dimmed the hint again",
        )
        await page.evaluate(f"(dwell) => rig.ui.showChip(({DRAFT_VIEW})(dwell))", 0)
        await page.evaluate(f"(dwell) => rig.ui.showChip(({DRAFT_VIEW})(dwell))", 450)
        await page.wait_for_timeout(40)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelectorAll('.hint.dim').length") == 1,
            "dwell: a new dwell after a reset was not shown",
        )


async def test_undo_expiry(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-undoable")
        env.report.check(await is_visible(page, "chip-undo"), "undo: button missing right after insertion")
        await page.wait_for_timeout(8400)
        env.report.check(not await is_visible(page, "chip-undo"), "undo: button still offered after 8 s")
        env.report.check(not await is_visible(page, "chip-inserted"), "undo: 'Inserted' pill still shown after 8 s")
        env.report.check(await is_visible(page, "chip-accept"), "undo: the normal Insert button did not come back")


async def test_pairing_countdown(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-conn-pairing")
        first = await page.locator('[data-testid="pairing-countdown"]').inner_text()
        await page.wait_for_timeout(1300)
        second = await page.locator('[data-testid="pairing-countdown"]').inner_text()
        env.report.check(first != second, f"pairing: countdown did not tick ({first!r} -> {second!r})")
        label = await page.locator('[data-testid="pairing-code"]').get_attribute("aria-label")
        env.report.check(label == "Pairing code 4 8 2 9 1 3", f"pairing: spoken code label {label!r}")
        await show(page, "panel-conn-pairing-expiring")
        await page.wait_for_timeout(4600)
        env.report.check(await is_visible(page, "calm-pairing-expired"), "pairing: expiry was not shown")
        env.report.check(not await is_visible(page, "pairing-code"), "pairing: the expired code is still displayed")


async def test_toasts_and_live_regions(env: Env) -> None:
    async with env.session() as page:
        await show(page, "toast-all")
        await page.evaluate("""() => {
          for (let i = 0; i < 5; i++) rig.ui.toast({ kind: 'filled', tone: 'ok', params: { label: 'Field ' + i } });
          rig.ui.toast({ kind: 'filled', tone: 'ok', params: { label: 'Field 4' } });
          rig.ui.toast({ kind: 'error', tone: 'warn' });
        }""")
        polite = await page.locator('[data-testid="toasts"] [data-testid="toast"]').count()
        env.report.check(polite <= 3, f"toasts: {polite} stacked in the polite region (max 3)")
        texts = await page.locator('[data-testid="toasts"] [data-testid="toast"]').all_inner_texts()
        env.report.check(len(set(texts)) == len(texts), f"toasts: duplicates stacked {texts}")
        env.report.check(
            await page.locator('[data-testid="toasts-alert"] [data-testid="toast"]').count() == 1,
            "toasts: the error toast is not in the alert region",
        )
        roles = await page.evaluate(
            "[...rig.ui.root.querySelectorAll('.toast-region')].map((n) => n.getAttribute('role') + ':' + (n.getAttribute('aria-live') || ''))"
        )
        env.report.check(roles == ["status:polite", "alert:"], f"toasts: live region roles {roles}")
        await page.wait_for_timeout(7000)
        env.report.check(
            await page.locator('[data-testid="toast"]').count() == 0, "toasts: not dismissed automatically"
        )
        await show(page, "chip-verified-email")
        await page.wait_for_timeout(450)
        text = await page.locator('[data-testid="chip-announce"]').inner_text()
        env.report.check(
            "Verified" in text and "alex.newport@example.com" in text and "Tab" in text,
            f"a11y: chip announcement {text!r}",
        )
        env.report.check(
            await page.locator('[data-testid="chip-announce"]').get_attribute("aria-live") == "polite",
            "a11y: chip announcement region is not polite",
        )
        await show(page, "chip-needs-missing-fact", "es")
        await page.wait_for_timeout(450)
        text = await page.locator('[data-testid="chip-announce"]').inner_text()
        env.report.check("No hay sugerencia" in text, f"a11y: Spanish announcement {text!r}")


async def test_stable_nodes(env: Env) -> None:
    async with env.session() as page:
        result = await page.evaluate("""() => {
          rig.run('chip-verified-email', { lang: 'en' });
          const first = rig.ui.root.querySelector('.chip');
          const view = rig.make.chipView(rig.anchorOf('fx-top'));
          rig.ui.showChip(view);
          const same = rig.ui.root.querySelector('.chip') === first;
          rig.ui.updateChipAnchor({ ...view.anchor, y: view.anchor.y + 10 });
          const moved = rig.ui.root.querySelector('.chip') === first;
          const top = parseFloat(first.style.top);
          rig.ui.showChip({ ...view, value: 'changed' });
          const changed = rig.ui.root.querySelector('.chip') !== first;
          return { same, moved, top, changed };
        }""")
        env.report.check(result["same"], "re-render: showChip with an identical view rebuilt the chip")
        env.report.check(result["moved"], "re-render: updateChipAnchor rebuilt the chip instead of only moving it")
        env.report.check(result["changed"], "re-render: a changed value did not update the chip")


async def test_lifecycle(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate(
            "document.documentElement.insertAdjacentHTML('beforeend', '<div data-meridian-assistant id=stale></div>')"
        )
        await page.evaluate("rig.run('chip-verified-email', { lang: 'en' })")
        count = await page.evaluate("document.querySelectorAll('[data-meridian-assistant]').length")
        env.report.check(
            count == 1 and await page.evaluate("!document.getElementById('stale')"),
            f"lifecycle: stale host from an orphaned script was not replaced ({count})",
        )
        await page.evaluate("rig.ui.unmount()")
        env.report.check(
            not (await page.evaluate("rig.hostInfo()"))["present"], "lifecycle: unmount left the host in the page"
        )
        await page.evaluate("rig.ui.mount({ panel: false })")
        await page.wait_for_timeout(60)
        env.report.check(
            await page.locator('[data-testid="chip"]:visible').count() == 0,
            "lifecycle: a stale chip came back after remounting",
        )
        await page.evaluate("rig.ui.root.host.remove()")
        await page.evaluate("document.documentElement.append(document.createElement('div'))")
        await page.wait_for_timeout(100)
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["present"],
            "lifecycle: UI was not re-attached after the page removed its host",
        )


async def test_language_switch(
    env: Env,
) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate("rig.ui.setLanguage('es')")
        text = await page.locator('[data-testid="chip-trust"]').inner_text()
        env.report.check(text == "Verificado", f"language: live switch to es gave {text!r}")
        audit = await page.evaluate("rig.audit('chip-verified-email', 'es')")
        vocab = await page.evaluate("rig.vocabulary('es')")
        bad = leaked(audit["texts"], await page.evaluate("rig.dataStrings()"), vocab)
        env.report.check(not bad, f"language: English left over after switching to es: {bad}")
        await page.evaluate("rig.ui.setLanguage('auto')")
        text = await page.locator('[data-testid="chip-trust"]').inner_text()
        env.report.check(text == "Verified", f"language: auto with an en-GB browser gave {text!r}")
        await show(page, "panel-ready-6")
        await page.evaluate("rig.ui.setLanguage('es')")
        title = await page.locator('[data-testid="panel"]').get_attribute("aria-label")
        env.report.check(title == "Asistente de Meridian", f"language: panel region label {title!r}")
        lang = await page.evaluate("rig.ui.root.querySelector('.ui').getAttribute('lang')")
        env.report.check(lang == "es", f"language: root lang attribute {lang!r}")
    async with env.session({**BASE, "lang": "es"}) as page:
        await show(page, "chip-verified-email")
        await page.evaluate("rig.ui.setLanguage('auto')")
        text = await page.locator('[data-testid="chip-trust"]').inner_text()
        env.report.check(text == "Verificado", f"language: auto with an es-ES browser gave {text!r}")


async def test_platform_labels(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-draft-long")
        text = await page.locator('[data-testid="hint-regenerate"]').inner_text()
        env.report.check("⌥R" in text, f"platform: macOS hint {text!r}")
    init = "Object.defineProperty(navigator, 'userAgentData', { get: () => ({ platform: 'Windows' }) }); Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });"
    async with env.session(init=init) as page:
        await show(page, "chip-draft-long")
        text = await page.locator('[data-testid="hint-regenerate"]').inner_text()
        env.report.check("Alt+R" in text, f"platform: Windows hint {text!r}")


# ---- isolation ----------------------------------------------------------------------------------------------------------
async def test_reduced_motion(env: Env) -> None:
    probe = """() => [...rig.ui.root.querySelectorAll('*')].filter((el) => {
      const cs = getComputedStyle(el);
      return cs.animationName !== 'none' || !/^0s(, 0s)*$/.test(cs.transitionDuration);
    }).map((el) => el.className || el.tagName)"""
    async with env.session() as page:
        await page.emulate_media(reduced_motion="no-preference")
        await show(page, "chip-generating-generating")
        env.report.check(
            len(await page.evaluate(probe)) > 0,
            "motion control: the pulse animation is expected without the reduced-motion preference",
        )
    async with env.session() as page:
        await page.emulate_media(reduced_motion="reduce")
        for scene in (
            "chip-draft-dwell",
            "chip-generating-generating",
            "panel-conn-pairing",
            "panel-conn-ambiguous",
            "toast-all",
            "flash-verified",
            "chip-verified-email",
            "panel-research-running",
            "chip-fold-pill",
            "panel-review-long",
            "panel-fill-preview",
            "chip-group-row",
        ):
            await page.evaluate("([id, lang]) => rig.run(id, { lang })", [scene, "en"])
            offenders = await page.evaluate(probe)
            env.report.check(not offenders, f"reduced motion: {scene} still animates {offenders[:4]}")
            await page.wait_for_timeout(60)


async def test_closed_root(env: Env) -> None:
    async with env.session() as measure:  # the same scene in the open build tells where the Insert button is
        await show(measure, "chip-verified-email")
        button = await measure.evaluate("rig.controlCenter('chip-accept')")
    async with env.session(bundle="closed") as page:
        info = await page.evaluate("rig.run('chip-verified-email', { lang: 'en' })")
        await page.wait_for_timeout(200)
        host = await page.evaluate("rig.hostInfo()")
        env.report.check(
            host["present"] and not host["shadowRootExposed"], f"closed: the page can reach the shadow root {host}"
        )
        env.report.check(not host["rootExposed"], "closed: the test hook leaked into a production-flag build")
        env.report.check(info["anchor"]["x"] >= 0, "closed: scene did not run")
        env.report.check(
            await page.evaluate("([x, y]) => rig.hitTest(x, y)", [button["x"], button["y"]]) == "host",
            "closed: the chip's Insert button is not on top at its position",
        )
        env.report.check(
            await page.locator('[data-testid="chip"]').count() == 0, "closed: Playwright could see inside a closed root"
        )
        if env.shots:
            await page.screenshot(
                path=str(env.shots / "closed-root-chip.png"),
                clip={"x": 0, "y": 40, "width": 520, "height": 220},
                animations="disabled",
            )
    async with env.session(bundle="open") as page:
        await page.evaluate("rig.run('chip-verified-email', { lang: 'en' })")
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["shadowRootExposed"],
            "open build: shadow root should be inspectable by tests",
        )


async def test_hostile_page_css(env: Env) -> None:
    async with env.session() as clean:
        await show(clean, "chip-verified-email")
        baseline = (await clean.evaluate("rig.audit('chip-verified-email', 'en')"))["chip"]
    async with env.session(extra="&hostile=1") as page:
        await show(page, "chip-verified-email")
        audit = await page.evaluate("rig.audit('chip-verified-email', 'en')")
        chip = audit["chip"]
        env.report.check(chip is not None, "hostile css: the chip disappeared")
        if chip and baseline:
            env.report.check(
                abs(chip["w"] - baseline["w"]) <= 1 and abs(chip["h"] - baseline["h"]) <= 1,
                f"hostile css: chip size changed {chip} vs {baseline}",
            )
        for key in ("contrast", "overflow", "focusable", "names"):
            for item in audit[key]:
                env.report.fail(f"hostile css: {key}: {item}")
        display = await page.evaluate("getComputedStyle(document.querySelector('[data-meridian-assistant]')).display")
        env.report.check(display == "block", f"hostile css: host display {display!r}")
        env.report.check(
            await page.locator('[data-testid="chip-accept"]').is_visible(),
            "hostile css: the Insert button is not visible",
        )
        if env.shots:
            await page.screenshot(
                path=str(env.shots / "hostile-page-css.png"),
                clip={"x": 0, "y": 40, "width": 520, "height": 220},
                animations="disabled",
            )


async def test_strict_csp(env: Env) -> None:
    async with env.session(path="csp/rig.html") as page:
        await show(page, "chip-verified-email")
        background = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.chip')).backgroundColor")
        env.report.check(
            background == "rgb(23, 63, 54)", f"csp: styles did not apply under a strict style-src ({background})"
        )
        errors = getattr(page, "rig_errors", [])
        env.report.check(not errors, f"csp: console reported {errors[:3]}")


async def test_modal_dialog(env: Env) -> None:
    async with env.session() as page:
        await page.evaluate("document.getElementById('modal').showModal()")
        await show(page, "chip-verified-email")
        host = await page.evaluate("rig.hostInfo()")
        env.report.check(
            host["parent"] == "dialog",
            f"modal: the UI is not inside the open modal dialog ({host['parent']!r}); it would be inert",
        )
        button = await page.evaluate("rig.controlCenter('chip-accept')")
        env.report.check(
            await page.evaluate("([x, y]) => rig.hitTest(x, y)", [button["x"], button["y"]]) == "host",
            "modal: the chip is under the dialog",
        )
        await click(page, "chip-accept")
        env.report.check(
            await actions(page) == [{"type": "accept", "key": "f0:12"}],
            "modal: chip button not clickable while a modal dialog is open",
        )
        await page.evaluate("document.getElementById('modal').close()")
        await show(page, "chip-verified-email")
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["parent"] == "html",
            "modal: the UI did not return to the document after the dialog closed",
        )


async def test_no_layout_shift(env: Env) -> None:
    async with env.session() as page:
        for scene in (
            "chip-draft-long",
            "panel-ready-20",
            "toast-all",
            "ask-both",
            "chip-needs-missing-fact",
            "flash-verified",
        ):
            await page.evaluate("rig.ui.unmount()")
            before = await page.evaluate("rig.layoutSnapshot()")
            await show(page, scene)
            after = await page.evaluate("rig.layoutSnapshot()")
            env.report.check(before == after, f"layout: showing {scene} moved page content")


async def test_resize_reflow(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-clamp-right")
        await page.set_viewport_size({"width": 300, "height": 420})
        await page.wait_for_timeout(150)
        await page.evaluate(
            "rig.ui.updateChipAnchor(rig.anchorOf('fx-right'))"
        )  # the logic layer re-measures on resize
        audit = await page.evaluate("rig.audit('chip-clamp-right', 'en')")
        chip, view = audit["chip"], audit["viewport"]
        ok = chip and chip["x"] >= -0.5 and chip["x"] + chip["w"] <= view["w"] + 0.5
        env.report.check(bool(ok), f"resize: chip left the viewport after a resize {chip} in {view}")


async def test_follow_scroll_and_offscreen(env: Env) -> None:
    async with env.session() as page:
        info = await show(page, "chip-follow-scroll")
        audit = await page.evaluate("rig.audit('chip-follow-scroll', 'en')")
        expected_top = info["anchor"]["y"] + 60 + info["anchor"]["h"] + 6
        env.report.check(
            audit["chip"] is not None and abs(audit["chip"]["y"] - expected_top) <= 1.5,
            f"scroll: chip did not follow the anchor ({audit['chip']} vs top {expected_top})",
        )
        await show(page, "chip-anchor-offscreen")
        hidden = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.chip')).visibility")
        env.report.check(
            hidden == "hidden", f"scroll: chip should hide while its field is out of view, got visibility {hidden}"
        )
        await page.evaluate("rig.ui.updateChipAnchor(rig.anchorOf('fx-top'))")
        shown = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.chip')).visibility")
        env.report.check(shown == "visible", "scroll: chip did not come back when its field returned")


async def test_source_links(env: Env) -> None:
    async with env.session() as page:
        await page.context.route(
            "https://acme-robotics.example/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body="<title>ok</title>"),
        )
        await show(page, "chip-draft-long")
        await page.focus("#fx-text")
        with contextlib.suppress(Exception):
            async with page.expect_popup(timeout=700) as popup_info:
                await page.evaluate("rig.ui.root.querySelector('[data-testid=\"chip-source\"]').click()")
            await popup_info.value
            env.report.fail("links: a synthetic click opened a tab")
        async with page.expect_popup(timeout=5000) as popup_info:
            await page.locator('[data-testid="chip-source"]').first.click()
        popup = await popup_info.value
        env.report.check(popup.url.startswith("https://acme-robotics.example/about"), f"links: popup URL {popup.url}")
        await popup.close()
        opener = await page.evaluate("window.opener")
        env.report.check(opener is None, "links: the page itself changed")
        env.report.check(
            await page.locator('[data-testid="chip"]').is_visible(), "links: the chip vanished after opening a source"
        )


def valid_ui_prefs(value: dict) -> bool:
    """What the worker accepts as {t:'ui:set'}: dock, and at most 24 numeric dismissed-note timestamps with plain keys."""
    import re

    dismissed = value.get("dismissed", {})
    return (
        set(value) == {"dock", "dismissed"}
        and value["dock"] in ("left", "right")
        and len(dismissed) <= 24
        and all(
            re.fullmatch(r"[A-Za-z0-9_:.-]{1,40}", key) and isinstance(at, (int, float))
            for key, at in dismissed.items()
        )
    )


async def test_persisted_preferences(env: Env) -> None:
    """The panel's look goes through the worker ({t:'ui:get'|'ui:set'}); extension storage is closed to content scripts."""
    async with env.session() as page:
        await show(page, "panel-ready-20")
        await click(page, "panel-more")
        await click(page, "panel-dock-left")
        await page.wait_for_timeout(60)
        log = await page.evaluate("window.__bgLog")
        sets = [m for m in log if m["t"] == "ui:set"]
        env.report.check(
            bool(sets) and sets[-1]["value"]["dock"] == "left", f"prefs: dock not sent to the worker {log}"
        )
        env.report.check(
            await page.evaluate("rig.ui.root.querySelector('.dock').classList.contains('dock-left')"),
            "prefs: dock did not move left",
        )
        await show(page, "panel-drafts-unavailable")
        await click(page, "note-drafts-dismiss")
        env.report.check(not await is_visible(page, "note-drafts"), "prefs: the note was not dismissed")
        await page.wait_for_timeout(60)
        sent = [m["value"] for m in await page.evaluate("window.__bgLog") if m["t"] == "ui:set"]
        env.report.check(
            bool(sent) and "drafts_unavailable" in sent[-1]["dismissed"], f"prefs: dismissal not sent {sent[-1:]}"
        )
        env.report.check(all(valid_ui_prefs(v) for v in sent), f"prefs: the worker would reject a payload {sent}")
        env.report.check(
            await page.evaluate("window.__bgStats.storageTouches") == 0, "prefs: the UI touched chrome.storage directly"
        )
        await page.reload()
        await page.wait_for_selector("html[data-rig=ready]", state="attached")
        await show(page, "panel-drafts-unavailable")
        await page.wait_for_timeout(100)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelector('.dock').classList.contains('dock-left')"),
            "prefs: dock side was not restored from the worker",
        )
        env.report.check(not await is_visible(page, "note-drafts"), "prefs: a dismissed note came back after a reload")
        raw = await page.evaluate("localStorage.getItem('__fake_worker_ui')")
        env.report.check("value" not in raw and "Globex" not in raw, "prefs: unexpected data in the stored preferences")


async def test_prefs_sanitise_what_is_sent(env: Env) -> None:
    async with env.session() as page:
        result = await page.evaluate("""() => {
          const now = Date.now();
          const dismissed = { budget: now, 'bad key': now, ['k'.repeat(41)]: now, nan: NaN, str: 'x', inf: Infinity, drafts_unavailable: now - 10 * 24 * 3600e3 };
          for (let i = 0; i < 40; i++) dismissed['note_' + i] = now - i;
          return rig.prefs.sanitize({ dock: 'up', dismissed });
        }""")
        env.report.check(result["dock"] == "right", f"prefs: an unknown dock became {result['dock']!r}")
        keys = list(result["dismissed"])
        env.report.check(len(keys) <= 24, f"prefs: {len(keys)} dismissed notes kept (the worker keeps 24)")
        env.report.check(valid_ui_prefs(result), f"prefs: sanitised value is still not acceptable {result}")
        env.report.check(
            "budget" in keys and "drafts_unavailable" not in keys, f"prefs: expiry/newest wrong {keys[:5]}"
        )


async def test_prefs_when_the_worker_is_unreachable(env: Env) -> None:
    for mode in ("reject", "orphan"):
        async with env.session() as page:
            await page.evaluate(f"window.__bgMode.fail = '{mode}'")
            await show(page, "panel-ready-20")
            await click(page, "panel-more")
            await click(page, "panel-dock-left")
            await page.wait_for_timeout(120)
            env.report.check(
                await page.evaluate("rig.ui.root.querySelector('.dock').classList.contains('dock-left')"),
                f"prefs ({mode}): the dock choice did not work without the worker",
            )
            # a remount in the same page keeps the choice from memory
            await page.evaluate(
                "rig.ui.unmount(); rig.ui.mount({ panel: true }); rig.ui.setPanel(rig.make.panelView())"
            )
            await page.wait_for_timeout(60)
            env.report.check(
                await page.evaluate("rig.ui.root.querySelector('.dock').classList.contains('dock-left')"),
                f"prefs ({mode}): the in-memory fallback was lost on remount",
            )
            errors = getattr(page, "rig_errors", [])
            env.report.check(not errors, f"prefs ({mode}): console reported {errors[:2]}")


async def test_prefs_load_race(env: Env) -> None:
    """A slow ui:get answer must not undo a choice made while it was in flight."""
    async with env.session() as page:
        await page.evaluate(
            "localStorage.setItem('__fake_worker_ui', JSON.stringify({ dock: 'left', dismissed: { budget: Date.now() } }))"
        )
        await page.evaluate("window.__bgMode.delayGet = 500")
        await show(page, "panel-ready-20")
        await click(page, "panel-more")
        await click(page, "panel-dock-right")
        await page.wait_for_timeout(800)
        env.report.check(
            await page.evaluate("rig.ui.root.querySelector('.dock').classList.contains('dock-right')"),
            "prefs: a late answer from the worker overwrote the person's choice",
        )
        stored = json.loads(await page.evaluate("localStorage.getItem('__fake_worker_ui')"))
        env.report.check(stored["dock"] == "right", f"prefs: the worker was left with the old dock {stored}")
        env.report.check("budget" in stored["dismissed"], f"prefs: a dismissal known to the worker was lost {stored}")


async def test_iframe_viewport(env: Env) -> None:
    async with env.session() as page:
        await page.goto(f"{env.base}/frame-host.html")
        frame = page.frames[1]
        await frame.wait_for_selector("html[data-rig=ready]", state="attached")
        await frame.evaluate("rig.run('chip-draft-long', { lang: 'en' })")
        await page.wait_for_timeout(200)
        audit = await frame.evaluate("rig.audit('chip-draft-long', 'en')")
        chip, view = audit["chip"], audit["viewport"]
        env.report.check(
            chip["x"] >= 0
            and chip["x"] + chip["w"] <= view["w"]
            and chip["y"] >= 0
            and chip["y"] + chip["h"] <= view["h"],
            f"iframe: chip outside the frame {chip} {view}",
        )
        for key in ("contrast", "overflow", "focusable", "names"):
            for item in audit[key]:
                env.report.fail(f"iframe: {key}: {item}")
        env.report.check(
            await page.evaluate("document.querySelectorAll('[data-meridian-assistant]').length") == 0,
            "iframe: the child's UI leaked into the parent document",
        )
        if env.shots:
            await page.screenshot(path=str(env.shots / "iframe-300.png"), animations="disabled")


async def test_single_instance_hosts(env: Env) -> None:
    async with env.session() as page:
        for scene in ("chip-verified-email", "panel-ready-6", "toast-all"):
            await show(page, scene)
            info = await page.evaluate("rig.hostInfo()")
            env.report.check(info["present"], f"host: missing for {scene}")
            attrs = await page.evaluate("""() => {
              const host = document.querySelector('[data-meridian-assistant]');
              const cs = getComputedStyle(host);
              return { position: cs.position, z: cs.zIndex, pe: cs.pointerEvents, w: cs.width, h: cs.height, tab: host.tabIndex, last: document.documentElement.lastElementChild === host };
            }""")
            env.report.check(
                attrs["position"] == "fixed" and attrs["z"] == "2147483647" and attrs["pe"] == "none",
                f"host: styles {attrs}",
            )
            env.report.check(attrs["w"] == "0px" and attrs["h"] == "0px", f"host: occupies layout space {attrs}")
            env.report.check(
                attrs["tab"] == -1 and attrs["last"], f"host: not the last child of <html> / focusable itself {attrs}"
            )
            env.report.check(info["parent"] == "html", f"host: parent is {info['parent']}")
            env.report.check(info["popoverOpen"], f"host: not promoted to the top layer in {scene}")


# ---- persistent field markers -------------------------------------------------------------------------------------------
MARKS = """(over) => {
  const a = rig.anchorOf('fx-top');
  return [{ key: 'f0:5', anchor: a, tone: 'inferred', label: 'Years of experience', ...over }];
}"""


async def test_marks(env: Env) -> None:
    async with env.session() as page:
        info = await show(page, "mark-inferred")
        marks = page.locator('[data-testid="mark"]')
        env.report.check(await marks.count() == 1, "marks: the marker is not rendered")
        cls = await marks.first.get_attribute("class") or ""
        env.report.check("tone-inferred" in cls, f"marks: tone class missing from {cls!r}")
        name = await marks.first.get_attribute("aria-label")
        env.report.check(name == "Years of experience: inserted answer, check this", f"marks: accessible name {name!r}")
        env.report.check(await marks.first.get_attribute("role") == "img", "marks: role is not img")
        # never in the tab order: real Tab presses through the whole page never land on it
        await page.focus("#f-first")
        for _ in range(16):
            await page.keyboard.press(tab_keys(env)[0])
            focus = await page.evaluate("rig.focusedInfo()")
            env.report.check(
                not focus["inHost"], f"marks: Tab landed inside the UI with only a marker showing ({focus})"
            )
        # persistent: still there after a while and after focus moved around, until the logic omits it
        await page.wait_for_timeout(1200)
        env.report.check(await marks.count() == 1, "marks: the marker did not persist")
        # node reuse and tracking: a new anchor moves the same node
        await page.evaluate("window.__first = rig.ui.root.querySelector('[data-testid=mark]')")
        before = await marks.first.bounding_box()
        await page.evaluate(
            f"(over) => rig.ui.setMarks(({MARKS})(over))", {"anchor": {**info["anchor"], "y": info["anchor"]["y"] + 40}}
        )
        after = await marks.first.bounding_box()
        env.report.check(
            abs((after["y"] - before["y"]) - 40) <= 1,
            f"marks: the marker did not follow its field ({before} -> {after})",
        )
        env.report.check(
            await page.evaluate("window.__first === rig.ui.root.querySelector('[data-testid=mark]')"),
            "marks: updating an anchor rebuilt the node",
        )
        # tone change keeps the node, swaps class and icon
        await page.evaluate(f"(over) => rig.ui.setMarks(({MARKS})(over))", {"tone": "draft"})
        cls = await marks.first.get_attribute("class") or ""
        env.report.check("tone-draft" in cls and "tone-inferred" not in cls, f"marks: tone not updated {cls!r}")
        name = await marks.first.get_attribute("aria-label")
        env.report.check(name == "Years of experience: AI draft inserted, review it", f"marks: draft name {name!r}")
        # language switch relabels
        await page.evaluate("rig.ui.setLanguage('es')")
        name = await marks.first.get_attribute("aria-label")
        env.report.check(
            name == "Years of experience: borrador con IA insertado, revísalo", f"marks: Spanish name {name!r}"
        )
        await page.evaluate("rig.ui.setLanguage('en')")
        # replaced, not accumulated; duplicates ignored; capped
        await page.evaluate("""() => {
          const a = rig.anchorOf('fx-top');
          rig.ui.setMarks([{ key: 'a', anchor: a, tone: 'inferred' }, { key: 'b', anchor: a, tone: 'draft' }, { key: 'b', anchor: a, tone: 'draft' }]);
        }""")
        env.report.check(await marks.count() == 2, f"marks: expected 2 distinct markers, found {await marks.count()}")
        await page.evaluate("rig.ui.setMarks([{ key: 'b', anchor: rig.anchorOf('fx-top'), tone: 'draft' }])")
        env.report.check(await marks.count() == 1, "marks: a marker omitted from the set stayed on screen")
        await page.evaluate(
            """() => rig.ui.setMarks(Array.from({ length: 150 }, (_, i) => ({ key: 'm' + i, anchor: rig.anchorOf('fx-top'), tone: 'inferred' })))"""
        )
        env.report.check(await marks.count() <= 60, f"marks: {await marks.count()} markers rendered (cap is 60)")
        await page.evaluate("rig.ui.setMarks([])")
        env.report.check(await marks.count() == 0, "marks: the marker stayed after the logic cleared it")
        env.report.check(
            not await page.locator('[data-testid="marks-layer"]').is_visible(), "marks: an empty layer is still exposed"
        )
        # offscreen anchor hides the marker (and removes it from the accessibility tree); back on screen it returns
        await page.evaluate(f"(over) => rig.ui.setMarks(({MARKS})(over))", {"anchor": {**info["anchor"], "y": -300}})
        hidden = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=mark]')).visibility")
        env.report.check(hidden == "hidden", f"marks: a marker for an off-screen field is {hidden}")
        await page.evaluate(f"(over) => rig.ui.setMarks(({MARKS})(over))", {})
        shown = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=mark]')).visibility")
        env.report.check(shown == "visible", "marks: the marker did not come back with its field")
        # click-through: a click on the part of the marker that overlaps the field reaches the field
        box = await marks.first.bounding_box()
        anchor = info["anchor"]
        x, y = anchor["x"] + anchor["w"] - 4, anchor["y"] + 3
        env.report.check(
            box["x"] <= x <= box["x"] + box["width"] and box["y"] <= y <= box["y"] + box["height"],
            "marks: test point is not on the marker",
        )
        await page.mouse.click(x, y)
        env.report.check(
            await page.evaluate("document.activeElement && document.activeElement.id") == "fx-top",
            "marks: the marker swallowed a click meant for the field",
        )
        # unmount clears; markers set before mounting appear once mounted
        await page.evaluate("rig.ui.unmount()")
        env.report.check(
            await page.evaluate("document.querySelectorAll('[data-meridian-assistant]').length") == 0,
            "marks: unmount left the host",
        )
        await page.evaluate(f"(over) => {{ rig.ui.setMarks(({MARKS})(over)); rig.ui.mount({{ panel: false }}); }}", {})
        env.report.check(
            await page.locator('[data-testid="mark"]').count() == 1,
            "marks: a set made before mount was not shown after it",
        )


async def test_marks_geometry(env: Env) -> None:
    async with env.session() as page:
        for scene, expected in (
            ("mark-inferred", "corner"),
            ("mark-right-edge", "corner"),
            ("mark-top-edge", "below"),
            ("mark-fills-viewport", "inset"),
            ("mark-small-control", "side"),
        ):
            await show(page, scene)
            audit = await page.evaluate(f"rig.audit('{scene}', 'en')")
            env.report.check(len(audit["marks"]) == 1, f"marks: {scene} rendered {len(audit['marks'])} markers")
            if audit["marks"]:
                env.report.check(
                    audit["marks"][0]["placement"] == expected,
                    f"marks: {scene} placed {audit['marks'][0]['placement']!r}",
                )
    async with env.session(reduced_motion=True) as page:
        await show(page, "mark-inferred")
        animated = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=mark]')).animationName"
        )
        env.report.check(animated == "none", f"marks: the marker animates under reduced motion ({animated})")
    async with env.session() as page:
        await page.evaluate("rig.run('mark-inferred', { lang: 'en' })")
        animated = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=mark]')).animationName"
        )
        env.report.check(animated != "none", "marks: expected a short fade-in without the reduced-motion preference")
        await page.wait_for_timeout(300)
        animated = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=mark]')).animationName"
        )
        env.report.check(animated == "none", "marks: the fade-in never ended")


async def test_marks_rtl(env: Env) -> None:
    async with env.session(extra="&dir=rtl") as page:
        info = await show(page, "mark-inferred")
        audit = await page.evaluate("rig.audit('mark-inferred', 'en')")
        mark = audit["marks"][0]
        anchor = info["anchor"]
        env.report.check(
            mark["x"] < anchor["x"] + anchor["w"] / 2,
            f"rtl: the marker sits on the field's right edge on a right-to-left page ({mark} vs {anchor})",
        )
        await show(page, "chip-verified-email")
        chip = (await page.evaluate("rig.audit('chip-verified-email', 'en')"))["chip"]
        anchor = await page.evaluate("rig.anchorOf('fx-top')")
        env.report.check(
            abs((chip["x"] + chip["w"]) - (anchor["x"] + anchor["w"])) <= 2 or chip["x"] <= 8.5,
            f"rtl: the chip is not aligned to the field's start (right) edge ({chip} vs {anchor})",
        )
        await show(page, "chip-bidi-data")
        direction = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=chip-value]')).direction"
        )
        env.report.check(direction == "rtl", f"rtl: employer text in Arabic is laid out {direction}")
        direction = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.ui')).direction")
        env.report.check(direction == "ltr", f"rtl: the assistant's own UI took the page direction ({direction})")
        audit = await page.evaluate("rig.audit('chip-bidi-data', 'en')")
        for key in ("overflow", "contrast", "focusable", "clickthrough"):
            for item in audit[key]:
                env.report.fail(f"rtl: {key}: {item}")
    async with env.session() as page:
        await show(page, "chip-bidi-mixed")
        direction = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=chip-value]')).direction"
        )
        env.report.check(direction == "rtl", f"bidi: Hebrew text with digits is laid out {direction}")
        await show(page, "panel-bidi-job")
        direction = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=panel-job]')).direction"
        )
        env.report.check(direction == "rtl", f"bidi: an Arabic company name is laid out {direction}")
        direction = await page.evaluate(
            "getComputedStyle(rig.ui.root.querySelector('[data-testid=pairing-code]') || document.body).direction"
        )
        env.report.check(direction == "ltr", f"bidi: {direction}")


# ---- pairing view, ambiguous listeners, retry feedback, the Enter key's name ---------------------------------------------------
FRAME = {**BASE, "name": "beh-frame", "width": 300, "height": 420}
PAIRING_VIEW = """(over) => rig.ui.setPanel(rig.make.panelView({ connection: { phase: 'pairing', port: 47615,
  pairing: { pairingId: 'p', code: '482913', expiresAt: Date.now() + 90000 }, ...over } }))"""
AMBIGUOUS_VIEW = """(over) => rig.ui.setPanel(rig.make.panelView({ connection: { phase: 'unpaired', meridianPorts: [47615, 47616], ...over } }))"""
SAY = """() => {
  window.__said = [];
  const live = rig.ui.root.querySelector('[data-testid=panel-announce]');
  new MutationObserver(() => { if (live.textContent) window.__said.push(live.textContent); }).observe(live, { childList: true, characterData: true, subtree: true });
}"""
WATCH_RETRY = """() => {
  window.__retryLabels = [];
  const note = () => { const el = rig.ui.root.querySelector('[data-testid=calm-retry]'); const text = el ? el.textContent.trim() : ''; const busy = el ? el.getAttribute('aria-busy') : null;
    const last = window.__retryLabels[window.__retryLabels.length - 1]; if (!last || last[0] !== text || last[1] !== busy) window.__retryLabels.push([text, busy]); };
  new MutationObserver(note).observe(rig.ui.root, { childList: true, subtree: true, characterData: true, attributes: true });
  note();
}"""


async def test_pairing_view_says_what_the_options_page_says(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-conn-pairing")
        text = await page.locator('[data-testid="calm-pairing"]').inner_text()
        for needle in (
            "Type this code in the Meridian window",
            "Meridian itself must open a small window asking for this code",
            "Meridian answered on port 47615.",
            "No Meridian window? Open Meridian from the menu bar, then try again.",
            "press Cancel: something else on this Mac may be answering",
            "Expires in",
        ):
            env.report.check(needle in text, f"pairing view: {needle!r} missing from {text!r}")
        # the port is a plain number, never grouped like a count
        env.report.check(
            "47,615" not in text and "47 615" not in text, f"pairing view: the port was formatted ({text!r})"
        )
        # the code and its countdown come first; what to do when no window opens comes after them
        order = await page.evaluate(
            """() => ['pairing-code', 'pairing-countdown', 'pairing-window', 'pairing-help', 'calm-cancel'].map((id) => rig.ui.root.querySelector(`[data-testid=${id}]`).getBoundingClientRect().top)"""
        )
        env.report.check(
            order == sorted(order), f"pairing view: the code box is not above the help text and buttons {order}"
        )
        await page.evaluate("rig.resetActions()")
        await click(page, "calm-open-meridian")
        env.report.check(
            await actions(page) == [{"type": "open-meridian"}],
            f"pairing view: Open Meridian emitted {await actions(page)}",
        )
        await page.evaluate("rig.resetActions()")
        await click(page, "calm-cancel")
        env.report.check(
            await actions(page) == [{"type": "cancel-pairing"}], f"pairing view: Cancel emitted {await actions(page)}"
        )
        # no port known: the sentence about the port is left out, the rest stays
        await page.evaluate(f"({PAIRING_VIEW})({{ port: undefined }})")
        text = await page.locator('[data-testid="calm-pairing"]').inner_text()
        env.report.check(
            "answered on port" not in text and "No Meridian window?" in text, f"pairing view without a port: {text!r}"
        )
        # a value that is not a port is not shown either
        for junk in (0, 70000, 1.5, -1, "47615"):
            await page.evaluate(f"({PAIRING_VIEW})({{ port: {json.dumps(junk)} }})")
            text = await page.locator('[data-testid="calm-pairing"]').inner_text()
            env.report.check("answered on port" not in text, f"pairing view: port {junk!r} was shown ({text!r})")
        # two listeners listed while a pairing runs does not turn the code card into the refusal: the pairing already started
        await page.evaluate(f"({PAIRING_VIEW})({{ meridianPorts: [47615, 47616] }})")
        env.report.check(
            await is_visible(page, "pairing-code"), "pairing view: the code vanished because two ports were listed"
        )
        env.report.check(not await is_visible(page, "calm-ambiguous"), "pairing view: shown as ambiguous while pairing")
        await show(page, "panel-conn-pairing", "es")
        text = await page.locator('[data-testid="calm-pairing"]').inner_text()
        for needle in (
            "Meridian debe abrir por sí mismo una ventana pequeña que te pida este código",
            "Meridian ha respondido en el puerto 47615.",
            "Abre Meridian desde la barra de menús y vuelve a intentarlo",
            "pulsa Cancelar: puede que otro programa de este Mac esté respondiendo",
            "Caduca en",
        ):
            env.report.check(needle in text, f"pairing view (es): {needle!r} missing from {text!r}")
    async with env.session(FRAME) as page:
        # in a 300 px frame the code and its countdown are on screen without scrolling; the rest is one scroll away
        await show(page, "panel-conn-pairing")
        box = await page.evaluate(
            """() => { const r = (id) => rig.ui.root.querySelector(`[data-testid=${id}]`).getBoundingClientRect();
              const body = rig.ui.root.querySelector('[data-testid=panel-body]').getBoundingClientRect();
              return { code: r('pairing-code'), count: r('pairing-countdown'), body: { top: body.top, bottom: body.bottom }, scroll: rig.ui.root.querySelector('[data-testid=panel-body]').scrollHeight > rig.ui.root.querySelector('[data-testid=panel-body]').clientHeight }; }"""
        )
        env.report.check(
            box["code"]["top"] >= box["body"]["top"] and box["count"]["bottom"] <= box["body"]["bottom"],
            f"pairing view in a 300 px frame: the code or its countdown starts off screen {box}",
        )
        await page.locator('[data-testid="calm-cancel"]').scroll_into_view_if_needed()
        cancel = await page.locator('[data-testid="calm-cancel"]').bounding_box()
        env.report.check(
            cancel is not None and cancel["y"] + cancel["height"] <= 420 and cancel["x"] + cancel["width"] <= 300,
            f"pairing view in a 300 px frame: Cancel is out of reach {cancel}",
        )


async def test_ambiguous_listeners(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-conn-ambiguous")
        text = await page.locator('[data-testid="calm-ambiguous"]').inner_text()
        for needle in (
            "More than one program on this Mac says it is Meridian.",
            "To be safe, I send nothing to any of them (ports 47615, 47616).",
            "Quit the extra one, then try again.",
        ):
            env.report.check(needle in text, f"ambiguous: {needle!r} missing from {text!r}")
        for gone in ("calm-connect", "calm-unpaired", "calm-open-meridian", "progress-list"):
            env.report.check(
                not await is_visible(page, gone), f"ambiguous: {gone} is shown (a Connect that cannot work)"
            )
        env.report.check(
            await page.locator('[data-testid="calm-ambiguous"] .btn').count() == 1, "ambiguous: more than one button"
        )
        env.report.check(
            await page.locator('[data-testid="panel-conn"]').get_attribute("aria-label")
            == "Connection paused for your safety",
            "ambiguous: the connection dot is not named for the refusal",
        )
        env.report.check(
            "conn-ambiguous" in (await page.locator('[data-testid="panel-conn"]').get_attribute("class") or ""),
            "ambiguous: the dot has no alert colour class",
        )
        env.report.check(
            await page.locator('[data-testid="panel-conn"]').get_attribute("data-phase") == "unpaired",
            "ambiguous: the phase attribute stopped being the real phase",
        )
        # the dot carries the same alert colour as the other refusal (the class alone proves nothing without its rule)
        dot_colour = "getComputedStyle(rig.ui.root.querySelector('[data-testid=panel-conn]')).backgroundColor"
        ambiguous_colour = await page.evaluate(dot_colour)
        await show(page, "panel-conn-proof_failed")
        refusal_colour = await page.evaluate(dot_colour)
        await show(page, "panel-conn-unpaired")
        idle_colour = await page.evaluate(dot_colour)
        env.report.check(
            ambiguous_colour == refusal_colour != idle_colour,
            f"ambiguous: dot colour {ambiguous_colour} (refusal {refusal_colour}, idle {idle_colour})",
        )
        await show(page, "panel-conn-ambiguous")
        # the refusal comes with the revoked phase too
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ phase: 'revoked' }})")
        env.report.check(
            await is_visible(page, "calm-ambiguous") and not await is_visible(page, "calm-connect"),
            "ambiguous: revoked phase",
        )
        # one listener again: the normal Connect card is back, with no refusal left behind
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ meridianPorts: [47615], port: 47615 }})")
        env.report.check(
            await is_visible(page, "calm-connect") and not await is_visible(page, "calm-ambiguous"),
            "ambiguous: one listener left",
        )
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ phase: 'ready', port: 47615 }})")
        env.report.check(
            not await is_visible(page, "calm-ambiguous") and await is_visible(page, "progress-list"),
            "ambiguous: ready state",
        )
        # a pill still tells the truth about the connection
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ }})")
        await page.evaluate(
            "rig.ui.setPanel(rig.make.panelView({ collapsed: true, connection: { phase: 'unpaired', meridianPorts: [47615, 47616] } }))"
        )
        pill = await page.locator('[data-testid="panel-pill"] .conn').get_attribute("class")
        env.report.check("conn-ambiguous" in (pill or ""), f"ambiguous: the collapsed pill dot is {pill!r}")
    async with env.session() as page:
        # announced once, like every other connection change; Try again answers even when nothing changed
        await show(page, "panel-ready-6")
        await page.evaluate(SAY)
        # an unpaired browser that then turns out to have two listeners is told so (the phase did not change, the news did)
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ meridianPorts: [47615], port: 47615 }})")
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ }})")
        await page.evaluate(f"({AMBIGUOUS_VIEW})({{ }})")
        said = await page.evaluate("window.__said")
        expect = (
            "More than one program on this Mac says it is Meridian. To be safe, I send nothing to any of them "
            "(ports 47615, 47616). Quit the extra one, then try again."
        )
        env.report.check(said == ["Connect this browser to Meridian.", expect], f"ambiguous: announcement was {said}")
        await page.evaluate("rig.resetActions()")
        await page.evaluate(WATCH_RETRY)
        await click(page, "calm-retry")
        env.report.check(
            await actions(page) == [{"type": "retry-connection"}], f"ambiguous: Try again emitted {await actions(page)}"
        )
        labels = await page.evaluate("window.__retryLabels")
        env.report.check(
            ["Checking…", "true"] in labels, f"ambiguous: pressing Try again showed no feedback ({labels})"
        )
        await page.wait_for_timeout(1900)
        labels = await page.evaluate("window.__retryLabels")
        env.report.check(labels[-1] == ["Try again", None], f"ambiguous: the button did not come back ({labels})")
        said = await page.evaluate("window.__said")
        env.report.check(
            said == ["Connect this browser to Meridian.", expect, "Checking…", expect],
            f"ambiguous: Try again was not answered in speech {said}",
        )
        # focus stayed on the button through both renders (reached and pressed by keyboard: a mouse click never takes focus)
        await show(page, "panel-conn-ambiguous")
        await page.evaluate("rig.ui.focusPanel()")
        for _ in range(6):
            if (await page.evaluate("rig.focusedInfo()"))["testid"] == "calm-retry":
                break
            await page.keyboard.press(tab_keys(env)[0])
        env.report.check(
            (await page.evaluate("rig.focusedInfo()"))["testid"] == "calm-retry",
            "ambiguous: Try again is not reachable by keyboard",
        )
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(80)
        env.report.check(
            (await page.evaluate("rig.focusedInfo()"))["testid"] == "calm-retry",
            "ambiguous: the keyboard left Try again while it said Checking…",
        )
        await page.wait_for_timeout(1500)
        env.report.check(
            (await page.evaluate("rig.focusedInfo()"))["testid"] == "calm-retry",
            "ambiguous: the keyboard left Try again when the label came back",
        )
        # the answer changed while the button was busy: the new state is announced once, not announced again afterwards
        await page.evaluate(SAY)
        await click(page, "calm-retry")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ connection: { phase: 'ready', port: 47615 } }))")
        await page.wait_for_timeout(1500)
        said = await page.evaluate("window.__said")
        env.report.check(
            said == ["Checking…", "Connected to Meridian"], f"ambiguous: a changed answer was announced as {said}"
        )
    async with env.session() as page:
        await show(page, "panel-conn-ambiguous", "es")
        text = await page.locator('[data-testid="calm-ambiguous"]').inner_text()
        env.report.check(
            "Más de un programa de este Mac dice ser Meridian." in text
            and "Por seguridad, no envío nada a ninguno (puertos 47615, 47616)." in text
            and "Cierra el que sobre y vuelve a intentarlo." in text,
            f"ambiguous (es): {text!r}",
        )
        env.report.check(
            (await page.locator('[data-testid="calm-retry"]').inner_text()).strip() == "Reintentar",
            "ambiguous (es): button",
        )
        env.report.check(
            await page.locator('[data-testid="panel-conn"]').get_attribute("aria-label")
            == "Conexión en pausa por tu seguridad",
            "ambiguous (es): dot name",
        )
        await page.evaluate(WATCH_RETRY)
        await click(page, "calm-retry")
        labels = await page.evaluate("window.__retryLabels")
        env.report.check(["Comprobando…", "true"] in labels, f"ambiguous (es): feedback {labels}")


async def test_orphaned_header_is_not_a_connection_problem(env: Env) -> None:
    dot_colour = "getComputedStyle(rig.ui.root.querySelector('[data-testid=panel-conn]')).backgroundColor"
    async with env.session() as page:
        await show(page, "panel-conn-error")
        error_colour = await page.evaluate(dot_colour)
        env.report.check(
            await page.locator('[data-testid="panel-sub"]').inner_text() == "Connection problem", "orphaned: baseline"
        )
        await show(page, "panel-orphaned")
        env.report.check(
            await page.locator('[data-testid="panel-conn"]').get_attribute("aria-label") == "Needs a page reload",
            "orphaned: the dot is not named for the reload",
        )
        orphan_colour = await page.evaluate(dot_colour)
        env.report.check(
            orphan_colour != error_colour,
            f"orphaned: the dot is the alert colour of a failed connection ({orphan_colour})",
        )
        sub = await page.locator('[data-testid="panel-sub"]').inner_text()
        env.report.check("problem" not in sub.lower(), f"orphaned: the header says {sub!r}")
        # whatever phase the logic layer parked it in, an orphaned script says the same thing (here: still "ready")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ orphaned: true }))")
        sub = await page.locator('[data-testid="panel-sub"]').inner_text()
        env.report.check(sub == "Needs a page reload", f"orphaned: a stale ready phase shows {sub!r} in the header")
        # the collapsed pill does not light up red either
        await page.evaluate(
            "rig.ui.setPanel(rig.make.panelView({ collapsed: true, orphaned: true, connection: { phase: 'error', detail: 'orphaned' } }))"
        )
        pill = await page.locator('[data-testid="panel-pill"] .conn').get_attribute("class")
        env.report.check("conn-orphaned" in (pill or ""), f"orphaned: the collapsed pill dot is {pill!r}")
        await show(page, "panel-orphaned", "es")
        env.report.check(
            await page.locator('[data-testid="panel-sub"]').inner_text() == "Hay que recargar la página",
            "orphaned (es): header",
        )


async def test_panel_more_menu_fits(env: Env) -> None:
    """The dock buttons are one short line each in both languages, even in a 300 px frame (no ragged two-line label)."""
    for lang in ("en", "es"):
        async with env.session({**FRAME, "lang": lang}) as page:
            await show(page, "panel-ready-6", lang)
            await click(page, "panel-more")
            sizes = await page.evaluate(
                """() => ['panel-dock-left', 'panel-dock-right'].map((id) => {
                  const el = rig.ui.root.querySelector(`[data-testid=${id}]`); const r = el.getBoundingClientRect();
                  const label = el.lastChild.textContent; return [Math.round(r.height), label, el.scrollWidth > el.clientWidth]; })"""
            )
            heights = {item[0] for item in sizes}
            env.report.check(
                len(heights) == 1 and max(heights) <= 32 and not any(item[2] for item in sizes),
                f"panel menu ({lang}): the dock buttons are not one tidy line each {sizes}",
            )


async def test_retry_buttons_never_look_dead(env: Env) -> None:
    views = {
        "no_bridge": ("panel-conn-no_bridge", "Try now"),
        "proof_failed": ("panel-conn-proof_failed", "Try again"),
        "error": ("panel-conn-error", "Try again"),
    }
    async with env.session() as page:
        for phase, (scene, label) in views.items():
            await show(page, scene)
            await page.evaluate("rig.resetActions()")
            await page.evaluate(WATCH_RETRY)
            await click(page, "calm-retry")
            env.report.check(
                await actions(page) == [{"type": "retry-connection"}], f"retry {phase}: emitted {await actions(page)}"
            )
            await page.wait_for_timeout(1700)
            labels = await page.evaluate("window.__retryLabels")
            env.report.check(
                [label, None] == labels[0] and ["Checking…", "true"] in labels and labels[-1] == [label, None],
                f"retry {phase}: label sequence {labels}",
            )
        # a second press while it says Checking… still reaches the logic layer: feedback never swallows a click
        await show(page, "panel-conn-error")
        await page.evaluate("rig.resetActions()")
        await click(page, "calm-retry")
        await click(page, "calm-retry")
        env.report.check(len(await actions(page)) == 2, f"retry: a second press was swallowed ({await actions(page)})")
        # leaving the card (and the UI) while the timer runs is harmless
        raised: list[str] = []
        page.on("pageerror", lambda exc: raised.append(str(exc)))
        await show(page, "panel-conn-error")
        await click(page, "calm-retry")
        await page.evaluate("rig.ui.unmount()")
        await page.wait_for_timeout(1500)
        env.report.check(not raised, f"retry: unmounting during feedback raised {raised}")


MAC = "Object.defineProperty(navigator, 'userAgentData', { get: () => ({ platform: 'macOS' }) }); Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });"
WINDOWS = "Object.defineProperty(navigator, 'userAgentData', { get: () => ({ platform: 'Windows' }) }); Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });"


async def test_enter_key_name(env: Env) -> None:
    """Spanish: a Mac keyboard says Retorno, a PC keyboard Intro (never the wrong one); English says Enter on both."""
    cases = [
        ("es", MAC, "Retorno", "Intro"),
        ("es", WINDOWS, "Intro", "Retorno"),
        ("en", MAC, "Enter", "Intro"),
        ("en", WINDOWS, "Enter", "Retorno"),
    ]
    for lang, init, wanted, other in cases:
        async with env.session({**BASE, "lang": lang}, init=init) as page:
            await show(page, "chip-protected-explicit", lang)
            note = await page.locator('[data-testid="chip-protected"]').inner_text()
            env.report.check(wanted in note and "{" not in note, f"enter key ({lang}): note {note!r}")
            env.report.check(other not in note, f"enter key ({lang}): the other platform's name {other!r} in {note!r}")
            await page.wait_for_timeout(450)
            said = await page.locator('[data-testid="chip-announce"]').inner_text()
            env.report.check(
                wanted in said and other not in said and "{" not in said, f"enter key ({lang}): announcement {said!r}"
            )
            await show(page, "chip-needs-protected-enter", lang)
            quiet = await page.locator('[data-testid="chip-reason"]').inner_text()
            env.report.check(
                wanted in quiet and other not in quiet and "{" not in quiet,
                f"enter key ({lang}): blocked note {quiet!r}",
            )


# ---- connecting, announcements ---------------------------------------------------------------------------------------------
async def test_connecting_state(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-conn-connecting")
        env.report.check(await is_visible(page, "calm-connecting"), "connecting: no calm card")
        text = await page.locator('[data-testid="calm-connecting"]').inner_text()
        env.report.check(text.strip() == "Connecting to Meridian…", f"connecting: copy {text!r}")
        env.report.check(await is_visible(page, "spinner"), "connecting: no spinner")
        name = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=spinner]')).animationName")
        env.report.check(
            name == "spin", f"connecting: the spinner does not turn without the reduced-motion preference ({name})"
        )
        busy = await page.locator('[data-testid="calm-connecting"]').get_attribute("aria-busy")
        env.report.check(busy == "true", "connecting: the card does not say it is busy")
        env.report.check(
            await page.locator('[data-testid="panel-conn"]').get_attribute("aria-label") == "Connecting to Meridian…",
            "connecting: the connection dot has no name",
        )
        for testid in ("calm-retry", "calm-connect", "calm-open-meridian", "panel-fill-verified", "panel-footer"):
            env.report.check(not await is_visible(page, testid), f"connecting: {testid} is shown while connecting")
        env.report.check(
            (await page.locator('[data-testid="panel-announce"]').inner_text()).strip() == "",
            "connecting: a transient state was announced to screen readers",
        )
        await show(page, "panel-conn-connecting", "es")
        text = await page.locator('[data-testid="calm-connecting"]').inner_text()
        env.report.check(text.strip() == "Conectando con Meridian…", f"connecting: Spanish copy {text!r}")
        await show(page, "panel-collapsed-connecting")
        env.report.check(await is_visible(page, "panel-pill"), "connecting: the collapsed pill is missing")
    async with env.session(reduced_motion=True) as page:
        await show(page, "panel-conn-connecting")
        name = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=spinner]')).animationName")
        env.report.check(name == "none", f"connecting: the spinner turns under reduced motion ({name})")
        env.report.check(await is_visible(page, "spinner"), "connecting: the spinner vanished under reduced motion")


async def test_panel_announcements(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-6")
        live = page.locator('[data-testid="panel-announce"]')
        env.report.check((await live.inner_text()).strip() == "", "announce: a healthy first render was announced")
        await page.evaluate("""() => {
          window.__said = [];
          const live = rig.ui.root.querySelector('[data-testid=panel-announce]');
          new MutationObserver(() => { if (live.textContent) window.__said.push(live.textContent); }).observe(live, { childList: true, characterData: true, subtree: true });
        }""")
        view = "(phase, extra) => rig.ui.setPanel(rig.make.panelView({ connection: { phase, port: 47615, ...extra } }))"
        await page.evaluate(f"({view})('connecting')")
        await page.evaluate(f"({view})('ready')")
        env.report.check(
            await page.evaluate("window.__said.length") == 0, "announce: connecting/ready flicker was announced"
        )
        await page.evaluate(f"({view})('no_bridge')")
        await page.evaluate(f"({view})('no_bridge')")
        said = await page.evaluate("window.__said")
        env.report.check(said == ["Meridian isn't open."], f"announce: no_bridge said {said}")
        await page.evaluate(
            f"({view})('pairing', {{ pairing: {{ pairingId: 'p', code: '482913', expiresAt: Date.now() + 90000 }} }})"
        )
        said = await page.evaluate("window.__said")
        env.report.check(
            said[-1] == "Type this code in the Meridian window. Pairing code 4 8 2 9 1 3",
            f"announce: pairing said {said[-1:]}",
        )
        await page.evaluate(
            f"({view})('pairing', {{ pairing: {{ pairingId: 'p', code: '482913', expiresAt: Date.now() + 80000 }} }})"
        )
        env.report.check(
            len(await page.evaluate("window.__said")) == len(said), "announce: a countdown tick repeated the code"
        )
        await page.evaluate(f"({view})('ready')")
        said = await page.evaluate("window.__said")
        env.report.check(said[-1] == "Connected to Meridian", f"announce: recovery said {said[-1:]}")


async def test_chip_announces_once_per_focus(env: Env) -> None:
    async with env.session() as page:
        await page.evaluate("rig.run('chip-skip', { lang: 'en' })")
        await page.evaluate("""() => {
          window.__said = [];
          const live = rig.ui.root.querySelector('[data-testid=chip-announce]');
          new MutationObserver(() => { if (live.textContent) window.__said.push(live.textContent); }).observe(live, { childList: true, characterData: true, subtree: true });
          const anchor = rig.anchorOf('fx-top');
          window.__view = (over) => rig.ui.showChip(rig.make.chipView(anchor, over));
        }""")
        await page.evaluate("__view({})")
        await page.wait_for_timeout(450)
        said = await page.evaluate("window.__said")
        env.report.check(
            len(said) == 1 and said[0].startswith("Verified: alex.newport@example.com."),
            f"announce: first focus said {said}",
        )
        # typing into the field, the undo window and the dwell change the view but not what there is to say
        for change in (
            "{ conflictsWithCurrent: true }",
            "{ undoable: true }",
            "{ value: 'alex.newport@example.org' }",
            "{ dwellRemainingMs: 400 }",
        ):
            await page.evaluate(f"__view({change})")
            await page.wait_for_timeout(420)
        env.report.check(
            len(await page.evaluate("window.__said")) == 1,
            f"announce: re-renders were announced {await page.evaluate('window.__said')}",
        )
        # three quick focus changes: only the last one is spoken
        await page.evaluate(
            "__view({ key: 'f0:20', value: 'A' }); __view({ key: 'f0:21', value: 'B' }); __view({ key: 'f0:22', value: 'C' })"
        )
        await page.wait_for_timeout(450)
        said = await page.evaluate("window.__said")
        env.report.check(len(said) == 2 and "C" in said[1], f"announce: rapid focus changes said {said}")
        # a draft that was being written and is now ready is worth a second announcement for the same field
        busy = "{ key: 'f0:30', state: 'GENERATING', tone: 'busy', value: '', insert: 'never', operation: null, generation: { status: 'GENERATING' } }"
        ready = "{ key: 'f0:30', state: 'GENERATED_DRAFT', tone: 'draft', value: 'A validated draft.', generation: { status: 'DONE', wordCount: 3 } }"
        await page.evaluate(f"__view({busy})")
        await page.wait_for_timeout(450)
        await page.evaluate(f"__view({ready})")
        await page.wait_for_timeout(450)
        said = await page.evaluate("window.__said")
        env.report.check(len(said) == 4 and "AI draft" in said[3], f"announce: busy -> ready said {said[2:]}")
        # leaving the field and coming back is a new focus: spoken again
        await page.evaluate(
            "rig.ui.hideChip(); __view({ key: 'f0:30', state: 'GENERATED_DRAFT', tone: 'draft', value: 'A validated draft.', generation: { status: 'DONE', wordCount: 3 } })"
        )
        await page.wait_for_timeout(450)
        env.report.check(
            len(await page.evaluate("window.__said")) == 5, "announce: refocusing the field was not announced"
        )


async def test_panel_keeps_its_scroll(env: Env) -> None:
    """In a short window (200% zoom) the panel body scrolls; progress updates must not throw it back to the top."""
    async with env.session({**BASE, "width": 550, "height": 350}) as page:
        await show(page, "panel-ready-20")
        await page.evaluate("rig.ui.root.querySelector('.p-body').scrollTop = 90")
        before = await page.evaluate("rig.ui.root.querySelector('.p-body').scrollTop")
        env.report.check(before > 0, "scroll: the panel body does not scroll in a short window")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillableCount: 3, pendingDrafts: 1 }))")
        after = await page.evaluate("rig.ui.root.querySelector('.p-body').scrollTop")
        env.report.check(after == before, f"scroll: a panel update moved the scrolled body from {before} to {after}")


# ---- high contrast, print, zoom -----------------------------------------------------------------------------------------------
FORCED_PROBE = """() => {
  const root = rig.ui.root;
  const canvas = getComputedStyle(document.body).backgroundColor;
  const seen = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    const border = ['Top', 'Right', 'Bottom', 'Left'].some((side) => parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none' && cs['border' + side + 'Color'] !== canvas);
    const fill = cs.backgroundColor !== canvas && cs.backgroundColor !== 'rgba(0, 0, 0, 0)';
    return border || fill;
  };
  const out = {};
  for (const [name, selector] of Object.entries({
    chip: '.chip:not(.is-hidden)', accept: '[data-testid=chip-accept]', panel: '.panel', pill: '.pill-btn', mark: '[data-testid=mark]', toast: '.toast',
    card: '.card', input: '.input', fill: '.bar-fill', track: '.bar', dwell: '.dwell-bar', code: '.code', calm: '.calm',
    preview: '.fill-preview', review: '.note-card.review', foldedChip: '.chip[data-compact=true]',
  })) out[name] = seen(root.querySelector(selector));
  return out;
}"""


async def test_forced_colors(env: Env) -> None:
    """Windows high-contrast / forced-colors mode: borders and system colours must keep every shape and state visible."""
    async with env.session() as page:
        await page.emulate_media(forced_colors="active")
        for scene, must_see in (
            ("chip-verified-email", ("chip", "accept")),
            ("chip-draft-dwell", ("chip", "dwell")),
            ("chip-needs-draft-button", ("chip",)),
            ("panel-ready-20", ("panel", "fill", "track")),
            ("panel-collapsed", ("pill",)),
            ("panel-conn-pairing", ("code", "calm")),
            ("panel-conn-ambiguous", ("calm",)),
            ("ask-save", ("card",)),
            ("mark-inferred", ("mark",)),
            ("toast-all", ("toast",)),
            ("panel-research-needs-company", ("input",)),
            ("chip-fold-pill", ("chip", "foldedChip")),
            ("chip-group-row", ("chip",)),
            ("panel-fill-preview", ("panel", "preview")),
            ("panel-review-long", ("panel", "review")),
        ):
            await page.evaluate("([id, lang]) => rig.run(id, { lang })", [scene, "en"])
            await page.wait_for_timeout(120)
            seen = await page.evaluate(FORCED_PROBE)
            for name in must_see:
                env.report.check(
                    bool(seen.get(name)), f"forced colors: {scene}: the {name} has no visible fill or border ({seen})"
                )
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", [scene, "en"])
            for key in ("contrast", "overflow", "focusable", "names", "clickthrough"):
                for item in audit[key]:
                    env.report.fail(f"forced colors: {scene}: {key}: {item}")
            if env.shots:
                box = audit["bounds"] or {"x": 0, "y": 0, "w": 400, "h": 300}
                await page.screenshot(
                    path=str(env.shots / f"forced-colors-{scene}.png"),
                    clip={
                        "x": max(0, box["x"] - 16),
                        "y": max(0, box["y"] - 16),
                        "width": min(1000, box["w"] + 32),
                        "height": min(680, box["h"] + 32),
                    },
                    animations="disabled",
                )
        # the focus ring survives (the system colours it)
        await show(page, "panel-ready-20")
        await page.evaluate("rig.ui.focusPanel()")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(
            info["inHost"] and not (info["outline"] or "none").startswith("none"),
            f"forced colors: no focus ring ({info})",
        )


async def test_print_hides_the_ui(env: Env) -> None:
    async with env.session() as page:
        await show(page, "combo-page")
        await page.emulate_media(media="print")
        display = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.ui')).display")
        env.report.check(display == "none", f"print: the assistant is part of the printed page ({display})")
        await page.emulate_media(media="screen")
        display = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('.ui')).display")
        env.report.check(display != "none", "print: the assistant did not come back on screen")


# ---- hostile pages ----------------------------------------------------------------------------------------------------------------
async def test_top_layer_over_hostile_ancestors(env: Env) -> None:
    async with env.session(extra="&ancestors=1") as page:
        await show(page, "chip-verified-email")
        button = await page.evaluate("rig.controlCenter('chip-accept')")
        cover = await page.evaluate(
            "([x, y]) => document.elementsFromPoint(x, y).map((el) => el.id || el.tagName.toLowerCase()).slice(0, 3)",
            [button["x"], button["y"]],
        )
        env.report.check(
            cover[0] != "cover", f"ancestors: a page overlay with z-index 2147483647 is above the chip {cover}"
        )
        await click(page, "chip-accept")
        env.report.check(
            await actions(page) == [{"type": "accept", "key": "f0:12"}],
            "ancestors: the chip is not clickable under a full-page overlay",
        )
        await show(page, "panel-ready-6")
        await click(page, "panel-more")
        env.report.check(
            await is_visible(page, "panel-more-box"), "ancestors: the panel is not usable under a full-page overlay"
        )
        env.report.check((await page.evaluate("rig.hostInfo()"))["popoverOpen"], "ancestors: not in the top layer")
    for extra in (
        "&zoomhtml=1.25",
        "&zoombody=1.25",
        "&transformhtml=scale(0.9)",
        "&transformbody=scale(0.9)",
        "&transformhtml=translateX(40px)",
    ):
        async with env.session(extra=extra) as page:
            info = await show(page, "chip-verified-email")
            chip = (await page.evaluate("rig.audit('chip-verified-email', 'en')"))["chip"]
            anchor = await page.evaluate("rig.anchorOf('fx-top')")
            del info
            env.report.check(
                abs(chip["x"] - anchor["x"]) <= 2 and abs(chip["y"] - (anchor["y"] + anchor["h"] + 6)) <= 2,
                f"page scaling {extra}: the chip is at {chip} instead of under the field {anchor}",
            )
            env.report.check(
                abs(chip["w"] - 351.1) <= 3,
                f"page scaling {extra}: the chip was scaled with the page ({chip['w']:.0f} px wide)",
            )


async def test_css_isolation(env: Env) -> None:
    async with env.session(extra="&isolate=1") as page:
        await show(page, "panel-all-notes")
        await page.evaluate("rig.run('combo-page', { lang: 'en' })")
        await page.wait_for_timeout(150)
        report = await page.evaluate("""() => {
          const root = rig.ui.root;
          const page = new Set(['rgb(255, 0, 255)', 'rgb(0, 255, 0)', 'rgb(255, 0, 0)', 'rgb(255, 255, 0)']);
          const leaks = [];
          for (const el of root.querySelectorAll('*')) {
            if (el.namespaceURI === 'http://www.w3.org/2000/svg') continue;
            const cs = getComputedStyle(el);
            for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'borderLeftColor', 'outlineColor', 'accentColor'])
              if (page.has(cs[prop])) leaks.push(`${el.className || el.tagName}.${prop}=${cs[prop]}`);
          }
          const value = root.querySelector('[data-testid=chip-value]');
          const vs = getComputedStyle(value);
          const ui = getComputedStyle(root.querySelector('.ui'));
          return {
            leaks, fontStyle: vs.fontStyle, letter: vs.letterSpacing, word: vs.wordSpacing, transform: vs.textTransform, indent: vs.textIndent,
            shadow: vs.textShadow, size: vs.fontSize, family: vs.fontFamily, align: vs.textAlign, variant: vs.fontVariantCaps, hyphens: vs.hyphens,
            cursor: getComputedStyle(root.querySelector('.chip')).cursor, scheme: ui.colorScheme, whiteSpace: getComputedStyle(root.querySelector('.p-job')).whiteSpace,
            chipBg: getComputedStyle(root.querySelector('.chip')).backgroundColor, ink: ui.getPropertyValue('--ink').trim(), tone: ui.getPropertyValue('--tone').trim(),
            parts: root.querySelectorAll('[part], [exportparts]').length,
          };
        }""")
        env.report.check(not report["leaks"], f"isolation: page colours reached the UI: {report['leaks'][:4]}")
        env.report.check(report["chipBg"] == "rgb(23, 63, 54)", f"isolation: chip background {report['chipBg']}")
        env.report.check(report["ink"] == "#F3F3EA", f"isolation: the page's --ink reached the UI ({report['ink']})")
        env.report.check(report["tone"] == "", f"isolation: the page's --tone reached the UI ({report['tone']!r})")
        env.report.check(
            report["size"] == "14px", f"isolation: chip value font size {report['size']} inherited from the page"
        )
        env.report.check(report["fontStyle"] == "normal" and report["transform"] == "none", f"isolation: {report}")
        env.report.check(
            report["letter"] == "normal" and report["word"] == "0px" and report["indent"] == "0px",
            f"isolation: {report}",
        )
        env.report.check(report["shadow"] == "none" and report["variant"] == "normal", f"isolation: {report}")
        env.report.check(
            "Comic" not in report["family"] and "Times" not in report["family"],
            f"isolation: font family {report['family']}",
        )
        env.report.check(
            report["cursor"] in ("auto", "default"), f"isolation: cursor {report['cursor']} inherited from the page"
        )
        env.report.check(report["scheme"] == "dark", f"isolation: colour scheme {report['scheme']}")
        env.report.check(report["parts"] == 0, "isolation: the shadow tree exposes ::part hooks to the page")
        audit = await page.evaluate("rig.audit('combo-page', 'en')")
        for key in ("contrast", "overflow", "clickthrough"):
            for item in audit[key]:
                env.report.fail(f"isolation: {key}: {item}")
        # a page that tints every ::backdrop must not tint the viewport because the host is in the top layer
        await page.evaluate("rig.ui.hideChip()")
        pixel = await page.evaluate("""() => new Promise((resolve) => requestAnimationFrame(() => resolve(true)))""")
        del pixel
        png = await page.screenshot(clip={"x": 2, "y": 2, "width": 4, "height": 4})
        from io import BytesIO

        from PIL import Image

        r, g, b = Image.open(BytesIO(png)).convert("RGB").getpixel((1, 1))
        env.report.check(
            r > 235 and g > 235 and b > 235, f"isolation: the page's ::backdrop style tinted the viewport ({r},{g},{b})"
        )


async def test_shadow_dialog_and_late_modal(env: Env) -> None:
    async with env.session(extra="&shadow=1") as page:
        await page.evaluate("document.getElementById('shadow-host').shadowRoot.getElementById('sh-dialog').showModal()")
        await page.evaluate("document.getElementById('shadow-host').shadowRoot.getElementById('sd-field').focus()")
        await show(page, "chip-verified-email")
        parent = (await page.evaluate("rig.hostInfo()"))["parent"]
        env.report.check(
            parent == "dialog", f"shadow dialog: the UI is in <{parent}>, outside the page component's modal dialog"
        )
        button = await page.evaluate("rig.controlCenter('chip-accept')")
        env.report.check(
            await page.evaluate("([x, y]) => rig.hitTest(x, y)", [button["x"], button["y"]]) == "host",
            "shadow dialog: the chip is inert under the component's modal dialog",
        )
        await click(page, "chip-accept")
        env.report.check(
            await actions(page) == [{"type": "accept", "key": "f0:12"}],
            "shadow dialog: the Insert button cannot be clicked",
        )
        await page.evaluate("document.getElementById('shadow-host').shadowRoot.getElementById('sh-dialog').close()")
        await page.focus("#f-first")
        await page.wait_for_timeout(60)
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["parent"] == "html",
            "shadow dialog: the UI did not return to the document",
        )
    async with env.session() as page:
        await show(page, "panel-ready-6")
        env.report.check((await page.evaluate("rig.hostInfo()"))["parent"] == "html", "late modal: precondition")
        await page.evaluate("document.getElementById('modal').showModal()")
        await page.wait_for_timeout(80)
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["parent"] == "dialog",
            "late modal: a dialog opened after the panel appeared left it inert",
        )
        await click(page, "panel-more")
        env.report.check(
            await is_visible(page, "panel-more-box"), "late modal: the panel cannot be clicked inside the dialog"
        )
        await page.evaluate("document.getElementById('modal').close()")
        await page.wait_for_timeout(80)
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["parent"] == "html" and await is_visible(page, "panel"),
            "late modal: the panel vanished with the dialog it was moved into",
        )
        env.report.check(
            (await page.evaluate("rig.hostInfo()"))["popoverOpen"],
            "late modal: the UI left the top layer after moving back",
        )


# Behaviours that need a Chromium-only feature of the test driver (not of the product): skipped under --browser webkit.
CHROMIUM_ONLY: set[str] = set()


async def run_behaviours(
    browser, base: str, report: Report, shots: Path | None, only: str | None = None, engine: str = "chromium"
) -> None:
    env = Env(browser, base, report, shots)
    env.engine = engine
    tests = [
        test_i18n,
        test_perf,
        test_chip_never_in_tab_order,
        test_panel_keyboard,
        test_focus_panel_shortcut,
        test_focus_not_stolen,
        test_chip_click_through,
        test_chip_avoids_the_next_field,
        test_typing_in_panel_input,
        test_untrusted_clicks,
        test_action_mapping,
        test_closing_panel_keeps_focus_target,
        test_focus_kept_when_a_control_disappears,
        test_dwell,
        test_undo_expiry,
        test_pairing_countdown,
        test_pairing_view_says_what_the_options_page_says,
        test_ambiguous_listeners,
        test_orphaned_header_is_not_a_connection_problem,
        test_panel_more_menu_fits,
        test_retry_buttons_never_look_dead,
        test_enter_key_name,
        test_toasts_and_live_regions,
        test_chip_announces_once_per_focus,
        test_panel_announcements,
        test_panel_keeps_its_scroll,
        test_connecting_state,
        test_marks,
        test_marks_geometry,
        test_marks_rtl,
        test_stable_nodes,
        test_lifecycle,
        test_language_switch,
        test_platform_labels,
        test_reduced_motion,
        test_closed_root,
        test_hostile_page_css,
        test_strict_csp,
        test_modal_dialog,
        test_no_layout_shift,
        test_resize_reflow,
        test_follow_scroll_and_offscreen,
        test_source_links,
        test_persisted_preferences,
        test_prefs_sanitise_what_is_sent,
        test_prefs_when_the_worker_is_unreachable,
        test_prefs_load_race,
        test_iframe_viewport,
        test_single_instance_hosts,
        test_forced_colors,
        test_print_hides_the_ui,
        test_top_layer_over_hostile_ancestors,
        test_css_isolation,
        test_shadow_dialog_and_late_modal,
    ]
    from behaviours_wave3 import wave3_tests  # imported here: that module imports this one

    tests += wave3_tests()
    from behaviours_wave4 import wave4_tests

    tests += wave4_tests()
    from behaviours_wave5 import wave5_tests

    tests += wave5_tests()
    from behaviours_wave6 import wave6_tests

    tests += wave6_tests()
    for test in tests:
        if only and only not in test.__name__:
            continue
        if engine == "webkit" and test.__name__ in CHROMIUM_ONLY:
            print(f"  behaviour {test.__name__[5:]}: skipped under webkit")
            continue
        before = len(report.failures)
        try:
            await test(env)
        except Exception as exc:  # a crashing test is a failing test, with its name
            report.fail(f"{test.__name__} crashed: {type(exc).__name__}: {str(exc)[:300]}")
        status = "ok" if len(report.failures) == before else "FAILED"
        print(f"  behaviour {test.__name__[5:]}: {status}")
