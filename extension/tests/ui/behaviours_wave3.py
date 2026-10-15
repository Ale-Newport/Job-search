"""Behaviours added in wave 3 (review fixes of the visual layer): option outlines, quiet file chips, stale drafts, tracker menu items,
duplicate banner, first-use card, end-of-form review, key cheat sheet, chip placement (beside, panel, question), typing pill,
screen-reader announcements, research form dismissal, chip keyboard actions, flash that follows the page, privacy copy."""

from __future__ import annotations

import datetime
import json

from behaviours import BASE, FRAME, MAC, WINDOWS, Env, actions, click, is_visible, show
from harness import overlap

CHIP_RECT = """() => {
  const chip = rig.ui.root.querySelector('.chip:not(.is-hidden)');
  if (!chip) return null;
  const r = chip.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height, placement: chip.getAttribute('data-placement'), pill: chip.getAttribute('data-pill') === 'true' };
}"""

SAID = """() => {
  window.__said = [];
  const live = rig.ui.root.querySelector('[data-testid=announce]');
  new MutationObserver(() => { if (live.textContent) window.__said.push(live.textContent); }).observe(live, { childList: true, characterData: true, subtree: true });
}"""

DAYS = "(n) => new Date(Date.now() - n * 86400000).toISOString()"


async def shown(page, scene: str, lang: str = "en") -> dict:
    """show() plus the real clicks the scene declares (opening a menu, a sheet), like the scene matrix does."""
    info = await show(page, scene, lang)
    for item in await page.evaluate("rig.scenes()"):
        if item["id"] == scene:
            for testid in item["clicks"]:
                await click(page, testid)
    return info


async def chip_rect(page) -> dict | None:
    return await page.evaluate(CHIP_RECT)


async def text_of(page, testid: str) -> str:
    return (await page.locator(f'[data-testid="{testid}"]').first.inner_text()).strip()


async def count(page, testid: str) -> int:
    return await page.locator(f'[data-testid="{testid}"]').count()


# ---- CPL-05: the recommended option is outlined -----------------------------------------------------------------------------
async def test_option_outlines(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-option-radio")
        env.report.check(await count(page, "option-mark") == 1, "options: a chip with one optionAnchor must draw exactly one outline")
        target = await page.evaluate("rig.anchorOf('fx-opt-no')")
        box = await page.locator('[data-testid="option-mark"]').first.bounding_box()
        env.report.check(
            box is not None and abs(box["x"] - (target["x"] - 3)) <= 1.5 and abs(box["y"] - (target["y"] - 3)) <= 1.5
            and abs(box["width"] - (target["w"] + 6)) <= 1.5 and abs(box["height"] - (target["h"] + 6)) <= 1.5,
            f"options: the outline is not on the recommended option {box} vs {target}",
        )
        style = await page.evaluate(
            """() => { const cs = getComputedStyle(rig.ui.root.querySelector('[data-testid=option-mark]'));
              return { width: cs.borderTopWidth, style: cs.borderTopStyle, color: cs.borderTopColor, events: cs.pointerEvents }; }"""
        )
        env.report.check(
            style == {"width": "2px", "style": "solid", "color": "rgb(168, 228, 206)", "events": "none"},
            f"options: the outline is not a 2 px mint line that lets clicks through {style}",
        )
        env.report.check(
            await page.locator('[data-testid="option-mark"] svg').count() == 1, "options: no check mark on the outline"
        )
        # the click reaches the radio under the outline, and the chip keeps off the highlighted option
        centre = {"x": target["x"] + target["w"] / 2, "y": target["y"] + target["h"] / 2}
        hit = await page.evaluate("([x, y]) => rig.hitTest(x, y)", [centre["x"], centre["y"]])
        env.report.check(hit in ("label", "input"), f"options: the outline swallows the click meant for the control ({hit})")
        chip = await chip_rect(page)
        env.report.check(chip is not None and overlap(chip, target) < 1, f"options: the chip covers the recommended option {chip} vs {target}")
        # it follows the field when the page scrolls and goes with the chip
        await page.evaluate("(a) => rig.ui.updateChipAnchor({ ...a, y: a.y + 40 })", await page.evaluate("rig.anchorOf('fx-opt-yes')"))
        moved = await page.locator('[data-testid="option-mark"]').first.bounding_box()
        env.report.check(
            moved is not None and box is not None and abs((moved["y"] - box["y"]) - 40) <= 1.5,
            f"options: the outline did not follow the scrolled field {box} -> {moved}",
        )
        await page.evaluate("rig.ui.hideChip()")
        env.report.check(await count(page, "option-mark") == 0, "options: the outline stayed after the chip was hidden")
        # several recommended members: one outline each
        await show(page, "chip-option-checkboxes")
        env.report.check(await count(page, "option-mark") == 2, "options: a checkbox group must outline every recommended member")
        # a single optionAnchor, a protected answer: one; after the insertion, with nothing to insert, or with no rectangle: none
        await show(page, "chip-option-protected")
        env.report.check(await count(page, "option-mark") == 1, "options: a protected answer must still show its option")
        for scene in ("chip-option-after-insert", "chip-option-needs-you", "chip-verified-radio", "chip-verified-email"):
            await show(page, scene)
            env.report.check(await count(page, "option-mark") == 0, f"options: {scene} must not draw an outline")
        # leaving the field with a different chip clears it; a fresh one for another field draws its own
        await show(page, "chip-option-radio")
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { key: 'f0:77' }))")
        env.report.check(await count(page, "option-mark") == 0, "options: the outline of the previous field stayed on")
        # an outline for a rectangle that is off screen is not drawn
        await page.evaluate(
            "rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { operation: 'CHECK_RADIO', optionLabel: 'No', optionAnchor: { x: 20, y: -500, w: 80, h: 28 } }))"
        )
        env.report.check(
            not await is_visible(page, "option-mark"), "options: an outline was drawn for a rectangle that is off screen"
        )
        await page.evaluate("rig.ui.unmount()")
        env.report.check(await count(page, "option-mark") == 0, "options: an outline survived the unmount")
    async with env.session(reduced_motion=True) as page:
        await show(page, "chip-option-radio")
        name = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=option-mark]')).animationName")
        env.report.check(name == "none", f"options: the outline animates under reduced motion ({name})")
    async with env.session() as page:
        await show(page, "chip-option-radio")
        name = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=option-mark]')).animationName")
        env.report.check(name == "fade-in", f"options: the outline has no gentle entrance without the preference ({name})")


# ---- CPL-06: a skipped file field says why -----------------------------------------------------------------------------------
async def test_skipped_file_chips(env: Env) -> None:
    wanted = {
        "chip-skip-file-unapproved": ("CV not approved yet", "Approve your CV in Meridian (Documents)", True),
        "chip-skip-file-cover-letter": ("Yours to upload", "I only attach your approved CV. Upload a cover letter yourself.", False),
        "chip-skip-file-none": ("No CV in Meridian yet", "Import your CV in Meridian (Documents)", True),
        "chip-skip-file-generic": ("Yours to upload", "I only attach your approved CV, and only to a CV upload.", False),
    }
    async with env.session() as page:
        for scene, (label, reason, button) in wanted.items():
            await show(page, scene)
            env.report.check(await text_of(page, "chip-label") == label, f"skip: {scene} label {await text_of(page, 'chip-label')!r}")
            env.report.check(reason in await text_of(page, "chip-reason"), f"skip: {scene} reason {await text_of(page, 'chip-reason')!r}")
            env.report.check(await is_visible(page, "chip-open-meridian") == button, f"skip: {scene} Open Meridian button should be {button}")
            for banned in ("chip-accept", "chip-replace", "hint-tab"):
                env.report.check(await count(page, banned) == 0, f"skip: {scene} offers {banned}")
            if button:
                await page.evaluate("rig.resetActions()")
                await click(page, "chip-open-meridian")
                env.report.check(await actions(page) == [{"type": "open-meridian"}], f"skip: Open Meridian emitted {await actions(page)}")
        # an unknown code shows the server's own sentence, only as a last resort
        await show(page, "chip-skip-file-unknown-code")
        env.report.check(
            await text_of(page, "chip-reason") == "A reason the server worded itself.", f"skip: unknown code {await text_of(page, 'chip-reason')!r}"
        )
        # without a reason a SKIP field stays silent; with one it can be dismissed like any chip
        await show(page, "chip-skip")
        env.report.check(not await is_visible(page, "chip"), "skip: a SKIP field without a reason showed a chip")
        await show(page, "chip-skip-file-cover-letter")
        await page.evaluate("rig.resetActions()")
        await click(page, "chip-dismiss")
        env.report.check(await actions(page) == [{"type": "dismiss", "key": "f0:12"}], f"skip: dismiss emitted {await actions(page)}")
        # announced calmly, in the person's language
        await page.wait_for_timeout(450)
        said = await text_of(page, "chip-announce")
        env.report.check("No suggestion to insert." in said and "Upload a cover letter yourself" in said, f"skip: announcement {said!r}")
        await show(page, "chip-skip-file-unapproved", "es")
        env.report.check(await text_of(page, "chip-label") == "CV aún sin aprobar", "skip (es): label")
        env.report.check("Apruébalo en Meridian (Documents)" in await text_of(page, "chip-reason"), "skip (es): reason")
        env.report.check(await text_of(page, "chip-open-meridian") == "Abrir Meridian", "skip (es): button")


# ---- CPL-09: a stale draft -------------------------------------------------------------------------------------------------------
async def test_stale_drafts(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-stale-research")
        env.report.check("older" not in await text_of(page, "chip-stale") and "Written before the latest research" in await text_of(page, "chip-stale"), "stale: research note")
        env.report.check(await is_visible(page, "chip-accept"), "stale: a draft with newer research may still be inserted")
        env.report.check(await is_visible(page, "hint-tab"), "stale: the Tab hint went away although Tab still inserts")
        env.report.check(await text_of(page, "chip-rewrite") == "Rewrite with new research", "stale: button label")
        cls = await page.locator('[data-testid="chip-rewrite"]').get_attribute("class") or ""
        env.report.check("ghost" in cls and "primary" not in cls, f"stale: with Insert still on offer the rewrite is the quiet action ({cls})")
        await page.evaluate("rig.resetActions()")
        await click(page, "chip-rewrite")
        env.report.check(await actions(page) == [{"type": "rewrite", "key": "f0:30"}], f"stale: rewrite emitted {await actions(page)}")
        for scene, label in (("chip-stale-profile", "Rewrite with my updated profile"), ("chip-stale-company-rejected", "Rewrite with new research")):
            await show(page, scene)
            env.report.check(await text_of(page, "chip-rewrite") == label, f"stale: {scene} button {await text_of(page, 'chip-rewrite')!r}")
            cls = await page.locator('[data-testid="chip-rewrite"]').get_attribute("class") or ""
            env.report.check("primary" in cls, f"stale: {scene}: the rewrite is the one action left ({cls})")
            for gone in ("chip-accept", "hint-tab", "chip-replace"):
                env.report.check(await count(page, gone) == 0, f"stale: {scene} still offers {gone}")
            await page.wait_for_timeout(450)
            said = await text_of(page, "chip-announce")
            env.report.check("This draft is out of date." in said and "Tab to insert it" not in said, f"stale: {scene} announcement {said!r}")
        note = await text_of(page, "chip-stale")
        env.report.check("isn't the right company" in note, f"stale: company note {note!r}")
        await show(page, "chip-stale-research")
        await page.wait_for_timeout(450)
        said = await text_of(page, "chip-announce")
        env.report.check("press Tab to insert it" in said and "out of date" in said, f"stale: research announcement {said!r}")
        # becoming stale while the field is focused is worth saying again; a draft that is not stale is not mentioned
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-text'), { key: 'f0:30', state: 'GENERATED_DRAFT', tone: 'draft', value: 'A draft.', whyCode: 'generated', generation: { status: 'DONE', wordCount: 2 } }))")
        await page.wait_for_timeout(450)
        env.report.check("out of date" not in await text_of(page, "chip-announce"), "stale: a fresh draft was called out of date")
        await show(page, "chip-stale-company-rejected", "es")
        env.report.check(await text_of(page, "chip-rewrite") == "Reescribir con la nueva investigación", "stale (es): button")
        env.report.check("no inserto este borrador" in await text_of(page, "chip-stale"), "stale (es): note")
        await show(page, "chip-stale-profile", "es")
        env.report.check(await text_of(page, "chip-rewrite") == "Reescribir con mi perfil actualizado", "stale (es): profile button")


async def test_already_inserted_line(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-already-inserted")
        env.report.check(await text_of(page, "chip-already") == "Already inserted", "already: the line is missing")
        env.report.check(await count(page, "chip-accept") == 0 and await count(page, "chip-replace") == 0, "already: an Insert button is offered for a value that is in the field")
        await show(page, "chip-already-inserted", "es")
        env.report.check(await text_of(page, "chip-already") == "Ya insertado", "already (es)")


# ---- CPL-10: the tracker items of the menu ---------------------------------------------------------------------------------------------
async def test_tracker_menu_items(env: Env) -> None:
    async with env.session() as page:
        await shown(page, "panel-more-track")
        env.report.check(await text_of(page, "panel-track-save") == "Save to tracker", "tracker menu: Save label")
        env.report.check(await text_of(page, "panel-track-applied") == "I applied: record this", "tracker menu: Applied label")
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-track-save")
        env.report.check(await actions(page) == [{"type": "track-save"}], f"tracker menu: Save emitted {await actions(page)}")
        env.report.check(not await is_visible(page, "panel-more-box"), "tracker menu: the menu stayed open after the choice")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["testid"] == "panel-more", f"tracker menu: focus did not return to the menu button ({info['testid']})")
        await shown(page, "panel-more-track")
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-track-applied")
        env.report.check(await actions(page) == [{"type": "track-applied"}], f"tracker menu: Applied emitted {await actions(page)}")
        await shown(page, "panel-more-track-prospective")
        env.report.check(await count(page, "panel-track-save") == 0 and await count(page, "panel-track-applied") == 1, "tracker menu: a posting Meridian already has offers Save")
        await shown(page, "panel-more-track-applied")
        env.report.check(await count(page, "panel-track-save") + await count(page, "panel-track-applied") == 0, "tracker menu: an applied posting offers recording again")
        # nothing to record while the assistant cannot reach Meridian, and nothing without a job
        await show(page, "panel-conn-no_bridge")
        await click(page, "panel-more")
        env.report.check(await count(page, "panel-track-applied") == 0, "tracker menu: offered while Meridian is unreachable")
        await show(page, "panel-no-job")
        await click(page, "panel-more")
        env.report.check(await count(page, "panel-track-applied") == 0, "tracker menu: offered without a job")
        await shown(page, "panel-more-track", "es")
        env.report.check(await text_of(page, "panel-track-save") == "Guardar en el Tracker", "tracker menu (es): Save")
        env.report.check(await text_of(page, "panel-track-applied") == "Ya la envié: registrarla", "tracker menu (es): Applied")
    async with env.session(FRAME) as page:
        await shown(page, "panel-more-track", "es")
        audit = await page.evaluate("rig.audit('panel-more-track', 'es')")
        env.report.check(not audit["overflow"], f"tracker menu: overflow in a 300 px frame {audit['overflow']}")


# ---- CPL-13: the duplicate banner ---------------------------------------------------------------------------------------------------------
async def test_duplicate_banner(env: Env) -> None:
    year = datetime.date.today().year
    async with env.session() as page:
        await show(page, "panel-duplicate-applied")
        env.report.check(await text_of(page, "dup-title") == "You already applied to this job", "duplicate: title")
        env.report.check("You applied on" in await text_of(page, "dup-hint") and "I won't insert anything" in await text_of(page, "dup-hint"), "duplicate: hint")
        await page.evaluate(SAID)
        await page.wait_for_timeout(1600)  # the same words are not said twice within 1.5 s
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: null }))")
        await page.evaluate(
            f"rig.ui.setPanel(rig.make.panelView({{ job: {{ company: 'Acme Robotics', title: 'Engineer', location: '', tracked: 'applied' }}, duplicate: {{ state: 'applied', since: ({DAYS})(3) }} }}))"
        )
        env.report.check(any("You already applied to this job." in line for line in await page.evaluate("window.__said")), f"duplicate: not announced {await page.evaluate('window.__said')}")
        # Continue is the person's call: it acknowledges and the banner goes, also when the same view is sent again
        await page.evaluate("rig.resetActions()")
        await click(page, "dup-continue")
        env.report.check(await actions(page) == [{"type": "duplicate-continue"}], f"duplicate: Continue emitted {await actions(page)}")
        env.report.check(not await is_visible(page, "panel-duplicate"), "duplicate: the banner stayed after Continue")
        await page.evaluate(f"rig.ui.setPanel(rig.make.panelView({{ duplicate: {{ state: 'applied', since: ({DAYS})(3) }} }}))")
        env.report.check(not await is_visible(page, "panel-duplicate"), "duplicate: the banner came back although the person continued")
        # a later duplicate (the logic dropped the first) is a new warning
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: null }))")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: { state: 'applied' } }))")
        env.report.check(await is_visible(page, "panel-duplicate"), "duplicate: a new warning was swallowed by the old answer")
        await page.evaluate("rig.resetActions()")
        await click(page, "dup-close")
        env.report.check(await actions(page) == [{"type": "deactivate"}], f"duplicate: Close emitted {await actions(page)}")
        # dates: the day it was recorded, in the person's order and language; a date-only value is not shifted by the time zone
        for since, en, es in (
            (f"{year - 1}-03-14", f"14 Mar {year - 1}", f"14 mar {year - 1}"),
            (f"{year}-01-05", "5 Jan", "5 ene"),
            (f"{year}-01-05T23:30:00Z", None, None),
        ):
            await page.evaluate(f"rig.ui.setPanel(rig.make.panelView({{ duplicate: {{ state: 'applied', since: {json.dumps(since)} }} }}))")
            hint = await text_of(page, "dup-hint")
            if en:
                env.report.check(en in hint, f"duplicate: date {since!r} shown as {hint!r}, wanted {en!r}")
                await page.evaluate("rig.ui.setLanguage('es')")
                env.report.check(es in await text_of(page, "dup-hint"), f"duplicate (es): date {since!r} shown as {await text_of(page, 'dup-hint')!r}")
                await page.evaluate("rig.ui.setLanguage('en')")
            else:
                env.report.check("Jan" in hint, f"duplicate: a timestamp was not read as a day ({hint!r})")
        for junk in ("<b>not a date</b>", "", "9999-99-99", "1850-01-01", "x" * 90):
            await page.evaluate(f"rig.ui.setPanel(rig.make.panelView({{ duplicate: {{ state: 'applied', since: {json.dumps(junk)} }} }}))")
            hint = await text_of(page, "dup-hint")
            env.report.check(hint.startswith("Meridian has it recorded as applied") and "<" not in hint, f"duplicate: junk date {junk[:20]!r} gave {hint!r}")
        # a job in progress is calmer: the same buttons, no claim that it was sent
        await show(page, "panel-duplicate-preparing")
        env.report.check(await text_of(page, "dup-title") == "This job is already in your Tracker", "duplicate: preparing title")
        env.report.check("You started it on" in await text_of(page, "dup-hint") and "won't insert" not in await text_of(page, "dup-hint"), "duplicate: preparing hint")
        await show(page, "panel-duplicate-applied", "es")
        env.report.check(await text_of(page, "dup-title") == "Ya enviaste tu candidatura a esta oferta", "duplicate (es): title")
        env.report.check(await text_of(page, "dup-continue") == "Continuar de todos modos", "duplicate (es): Continue")
        # a collapsed panel keeps the warning: the pill says so
        await show(page, "panel-duplicate-collapsed")
        label = await page.locator('[data-testid="panel-pill"]').get_attribute("aria-label")
        env.report.check("Already applied to this job" in (label or ""), f"duplicate: the collapsed pill does not say so ({label!r})")
        env.report.check(await is_visible(page, "pill-dup"), "duplicate: no marker on the collapsed pill")
        await show(page, "panel-collapsed")
        env.report.check(not await is_visible(page, "pill-dup"), "duplicate: a pill without a duplicate carries the marker")
        # nothing without a ready connection or on an orphaned page
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: { state: 'applied' }, connection: { phase: 'no_bridge' } }))")
        env.report.check(not await is_visible(page, "panel-duplicate"), "duplicate: shown while Meridian is unreachable")
    async with env.session(FRAME) as page:
        await show(page, "panel-duplicate-applied", "es")
        audit = await page.evaluate("rig.audit('panel-duplicate-applied', 'es')")
        env.report.check(not audit["overflow"], f"duplicate: overflow in a 300 px frame {audit['overflow']}")


# ---- CPL-18: the first-use card --------------------------------------------------------------------------------------------------------
async def test_first_use_card(env: Env) -> None:
    async with env.session(init=MAC) as page:
        await show(page, "panel-first-use")
        env.report.check(await count(page, "panel-first-use") == 1, "first use: the card is missing")
        env.report.check(await page.locator('[data-testid="first-steps"] li').count() == 3, "first use: not three steps")
        env.report.check("⌘⇧Y" in await text_of(page, "first-shortcut"), f"first use (mac): shortcut {await text_of(page, 'first-shortcut')!r}")
        env.report.check("default" in await text_of(page, "first-shortcut"), "first use: the shortcut is not called the default (it can be remapped)")
        # "See all keys" opens the cheat sheet inside the panel
        await page.evaluate("rig.resetActions()")
        await click(page, "first-keys")
        env.report.check(await actions(page) == [{"type": "keys-help"}], f"first use: See all keys emitted {await actions(page)}")
        env.report.check(await is_visible(page, "help-sheet"), "first use: the cheat sheet did not open")
        # "Got it" is for good: remembered by the worker and still gone after the panel is rebuilt
        await show(page, "panel-first-use")
        await page.evaluate("window.__bgLog.length = 0")
        await click(page, "first-dismiss")
        env.report.check(not await is_visible(page, "panel-first-use"), "first use: the card stayed after Got it")
        await page.wait_for_timeout(200)
        sent = [m for m in await page.evaluate("window.__bgLog") if m.get("t") == "ui:set"]
        env.report.check(bool(sent) and "first_use" in sent[-1]["value"]["dismissed"], f"first use: the dismissal was not sent to the worker {sent}")
        await show(page, "panel-first-use")
        await page.wait_for_timeout(250)
        env.report.check(not await is_visible(page, "panel-first-use"), "first use: the card came back after a reload although it was dismissed")
        # no card unless the logic asks for one
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ firstUse: false }))")
        env.report.check(not await is_visible(page, "panel-first-use"), "first use: shown although the logic did not ask")
    async with env.session(init=WINDOWS) as page:
        await show(page, "panel-first-use")
        env.report.check("Ctrl+Shift+Y" in await text_of(page, "first-shortcut"), f"first use (windows): {await text_of(page, 'first-shortcut')!r}")
    async with env.session(bundle="safari") as page:
        await show(page, "panel-first-use")
        env.report.check(await count(page, "first-shortcut") == 0, "first use (Safari): a shortcut Safari does not have was printed")
        env.report.check(await is_visible(page, "first-steps"), "first use (Safari): the steps are missing")
        await click(page, "first-keys")
        rows = await page.locator('[data-testid="help-row"]').all_inner_texts()
        env.report.check(not any("assistant on or off" in row for row in rows), f"first use (Safari): the sheet lists the toolbar shortcut {rows}")
    async with env.session({**BASE, "lang": "es"}) as page:
        await show(page, "panel-first-use", "es")
        env.report.check(await text_of(page, "first-dismiss") == "Entendido", "first use (es): Got it")
        steps = await page.locator('[data-testid="first-steps"] li').all_inner_texts()
        env.report.check("Haz clic en el primer campo" in steps[0] and "Mayús+Tab" in steps[2], f"first use (es): steps {steps}")
    async with env.session(FRAME) as page:
        for lang in ("en", "es"):
            await show(page, "panel-first-use", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-first-use", lang])
            env.report.check(not audit["overflow"], f"first use: overflow in a 300 px frame ({lang}) {audit['overflow']}")


# ---- FR-03: the end-of-form review ----------------------------------------------------------------------------------------------------------
async def test_review_card(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-review")
        env.report.check(await text_of(page, "review-title") == "8 required questions are still empty", f"review: title {await text_of(page, 'review-title')!r}")
        env.report.check(await count(page, "review-item") == 5, f"review: {await count(page, 'review-item')} items listed")
        env.report.check(await text_of(page, "review-more") == "+3 more", f"review: the rest is a count ({await text_of(page, 'review-more')!r})")
        await page.evaluate("rig.resetActions()")
        await click(page, "review-item")
        env.report.check(await actions(page) == [{"type": "focus-item", "key": "f0:11"}], f"review: an item emitted {await actions(page)}")
        await page.evaluate("rig.resetActions()")
        await page.evaluate(SAID)
        await click(page, "review-dismiss")
        env.report.check(await actions(page) == [{"type": "review-dismiss"}], f"review: dismiss emitted {await actions(page)}")
        env.report.check(not await is_visible(page, "panel-review"), "review: the card stayed after Dismiss")
        view = "rig.ui.setPanel(rig.make.panelView({ review: { missingRequired: 2, items: [{ key: 'f0:11', label: 'Notice period' }, { key: 'f0:12', label: 'Salary' }] } }))"
        await page.evaluate(view)
        env.report.check(not await is_visible(page, "panel-review"), "review: the card came back although it was dismissed")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ review: null }))")
        await page.evaluate(view)
        env.report.check(await is_visible(page, "panel-review"), "review: a new review was swallowed by the old dismissal")
        env.report.check(await text_of(page, "review-title") == "2 required questions are still empty", "review: count")
        env.report.check(any("2 required questions are still empty" in line for line in await page.evaluate("window.__said")), f"review: not announced {await page.evaluate('window.__said')}")
        await show(page, "panel-review-one")
        env.report.check(await text_of(page, "review-title") == "1 required question is still empty", "review: singular")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ review: { missingRequired: 0, items: [] } }))")
        env.report.check(not await is_visible(page, "panel-review"), "review: a card for nothing missing")
        await show(page, "panel-review", "es")
        env.report.check(await text_of(page, "review-title") == "Faltan 8 preguntas obligatorias por responder", "review (es): title")
        env.report.check(await text_of(page, "review-more") == "+3 más", "review (es): count")
    async with env.session(FRAME) as page:
        await show(page, "panel-review", "es")
        audit = await page.evaluate("rig.audit('panel-review', 'es')")
        env.report.check(not audit["overflow"], f"review: overflow in a 300 px frame {audit['overflow']}")


# ---- UX-12: Help is the key cheat sheet ----------------------------------------------------------------------------------------------------------
async def test_help_sheet(env: Env) -> None:
    async with env.session(init=MAC) as page:
        await show(page, "panel-ready-20")
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-help")
        env.report.check(await actions(page) == [{"type": "keys-help"}], f"help: emitted {await actions(page)} (it must not leave the page)")
        env.report.check(await is_visible(page, "help-sheet"), "help: the cheat sheet did not open")
        env.report.check(not await is_visible(page, "progress-list"), "help: the field list is still showing")
        rows = await page.locator('[data-testid="help-row"]').all_inner_texts()
        joined = "\n".join(rows)
        for needle in ("Tab", "⇧Tab", "Enter", "Esc", "⌥R", "⌥Z", "⌃⌥1 – ⌃⌥7", "⌥⇧M", "⌥⇧Z", "⌘⇧Y", "← ↑ ↓ →"):
            env.report.check(needle in joined, f"help: {needle!r} is not in the sheet {rows}")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["testid"] == "help-back", f"help: focus did not go to the sheet's Back button ({info['testid']})")
        env.report.check(await page.locator('[data-testid="panel-help"]').get_attribute("aria-expanded") == "true", "help: the link does not say it is open")
        # Back returns to the panel and puts the keyboard back on the Help link
        await click(page, "help-back")
        env.report.check(not await is_visible(page, "help-sheet") and await is_visible(page, "progress-list"), "help: Back did not return to the panel")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["testid"] == "panel-help", f"help: focus did not return to the Help link ({info['testid']})")
        # Settings is still the way to the options page; a second press on Help closes it as well
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-settings")
        env.report.check(await actions(page) == [{"type": "open-options", "anchor": "behaviour"}], f"help: Settings emitted {await actions(page)}")
        await click(page, "panel-help")
        await click(page, "panel-help")
        env.report.check(not await is_visible(page, "help-sheet"), "help: Help did not toggle")
        # Esc leaves the sheet, and so does putting the panel away
        await click(page, "panel-help")
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "help-sheet") and await is_visible(page, "progress-list"), "help: Esc did not leave the sheet")
        await click(page, "panel-help")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ collapsed: true }))")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ collapsed: false }))")
        env.report.check(not await is_visible(page, "help-sheet"), "help: the sheet was still open after the panel was collapsed and opened again")
        # the sheet survives a progress update, and goes when the connection does
        await click(page, "panel-help")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillableCount: 1 }))")
        env.report.check(await is_visible(page, "help-sheet"), "help: a panel update closed the sheet")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ connection: { phase: 'no_bridge' } }))")
        env.report.check(await is_visible(page, "calm-no_bridge") and not await is_visible(page, "help-sheet"), "help: the sheet hid a connection problem")
    async with env.session(init=WINDOWS) as page:
        await shown(page, "panel-help")
        joined = "\n".join(await page.locator('[data-testid="help-row"]').all_inner_texts())
        for needle in ("Shift+Tab", "Alt+R", "Alt+Shift+1 – Alt+Shift+7", "Alt+Shift+M", "Alt+Shift+Z", "Ctrl+Shift+Y"):
            env.report.check(needle in joined, f"help (windows): {needle!r} missing from {joined!r}")
    async with env.session({**BASE, "lang": "es"}, init=WINDOWS) as page:
        await shown(page, "panel-help", "es")
        joined = "\n".join(await page.locator('[data-testid="help-row"]').all_inner_texts())
        env.report.check("Intro" in joined and "Mayús+Tab" in joined and "Alt+Mayús+M" in joined, f"help (es, windows): {joined!r}")
        env.report.check(await text_of(page, "help-title") == "Atajos de teclado", "help (es): title")
    async with env.session({**BASE, "lang": "es"}, init=MAC) as page:
        await shown(page, "panel-help", "es")
        joined = "\n".join(await page.locator('[data-testid="help-row"]').all_inner_texts())
        env.report.check("Retorno" in joined and "⌥⇧M" in joined, f"help (es, mac): {joined!r}")
    async with env.session(FRAME) as page:
        for lang in ("en", "es"):
            await shown(page, "panel-help", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-help", lang])
            env.report.check(not audit["overflow"], f"help: overflow in a 300 px frame ({lang}) {audit['overflow']}")


# ---- UX-01 / UX-05: question first, beside the field, off the neighbours and off the panel ----------------------------------------------------------
async def test_chip_question_and_placement(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-question")
        first = await page.evaluate("rig.ui.root.querySelector('.chip').firstElementChild.getAttribute('data-testid')")
        env.report.check(first == "chip-question", f"question: the first line of the chip is {first!r}")
        question = await page.locator('[data-testid="chip-question"]').bounding_box()
        badge = await page.locator('[data-testid="chip-badge"]').bounding_box()
        env.report.check(question["y"] + question["height"] <= badge["y"] + 1, "question: not above the trust badge")
        await show(page, "chip-question-long")
        box = await page.locator('[data-testid="chip-question"]').bounding_box()
        env.report.check(box["height"] <= 18, f"question: a long question took more than one line ({box['height']} px)")
        # no question text, no line
        await show(page, "chip-verified-email")
        env.report.check(await count(page, "chip-question") == 0, "question: an empty line for a chip without a question")
        # beside: to the right of the field, level with it, when the chip fits there
        info = await show(page, "chip-beside")
        chip = await chip_rect(page)
        a = info["anchor"]
        env.report.check(chip["placement"] == "beside", f"beside: placement {chip['placement']}")
        env.report.check(chip["x"] >= a["x"] + a["w"], f"beside: the chip starts left of the field's end {chip} vs {a}")
        env.report.check(abs(chip["y"] - a["y"]) <= 2, f"beside: the chip is not level with the field {chip} vs {a}")
        # not enough room beside: below, or above
        await show(page, "chip-beside-no-room")
        env.report.check((await chip_rect(page))["placement"] == "below", "beside: no room to the right, yet the chip is not below")
        # a long draft goes beside the field when it fits at its own width ...
        await show(page, "chip-beside-draft")
        env.report.check((await chip_rect(page))["placement"] == "beside", "beside: a long draft that fits beside the field did not go there")
        # the previous field, the next fields and the focused field's own label: the chip finds the one side that covers none
        info = await show(page, "chip-avoid-previous-and-label")
        chip = await chip_rect(page)
        a = info["anchor"]
        avoid = [await page.evaluate("rig.anchorOf('fx-pair-b')"), {"x": a["x"], "y": a["y"] - 56, "w": a["w"], "h": 50}, {"x": a["x"], "y": a["y"] - 22, "w": 200, "h": 18}]
        env.report.check(all(overlap(chip, r) < 1 for r in avoid), f"avoid: the chip still covers the previous field, the label or the next field {chip} vs {avoid}")
        env.report.check(chip["placement"] == "beside", f"avoid: placement {chip['placement']}")
        # on its way to the neighbours' side the chip does not flip while the page scrolls
        for dy in (10, 30, 60):
            await page.evaluate("([a, dy]) => rig.ui.updateChipAnchor({ ...a, y: a.y + dy })", [a, dy])
            env.report.check((await chip_rect(page))["placement"] == "beside", f"avoid: the chip flipped after scrolling {dy} px")
        # a field as tall as the window: the chip goes beside it, and only where there is no room beside it covers it
        await show(page, "chip-tall-field")
        env.report.check((await chip_rect(page))["placement"] == "beside", "beside: a tall field did not get its chip beside it")
        # one more candidate does not disturb the plain case: nothing in the way, the preferred side stays
        await show(page, "chip-verified-email")
        env.report.check((await chip_rect(page))["placement"] == "below", "placement: a plain chip left its preferred side")
        await show(page, "chip-pref-above")
        env.report.check((await chip_rect(page))["placement"] == "above", "placement: a chip preferring above left it")
    async with env.session({**BASE, "width": 320, "height": 256}) as page:
        await show(page, "chip-tall-field")
        chip = await chip_rect(page)
        env.report.check(chip is not None and chip["placement"] == "overlay", f"beside: at 400% zoom there is no room beside a tall field, the chip must overlay it ({chip})")
    # ... and is never narrowed to fit: with 330 px beside the field a 440 px draft stays above or below, a short answer narrows
    async with env.session({**BASE, "width": 800}) as page:
        await show(page, "chip-beside-draft")
        chip = await chip_rect(page)
        env.report.check(chip["placement"] != "beside" and chip["w"] > 400, f"beside: a long draft was squeezed beside the field {chip}")
        await show(page, "chip-beside")
        chip = await chip_rect(page)
        env.report.check(chip["placement"] in ("beside", "below") and chip["x"] + chip["w"] <= 800 - 8 + 0.5, f"beside: a short chip does not fit the window {chip}")


async def test_chip_keeps_off_the_panel(env: Env) -> None:
    anchor = "{ x: 380, y: 300, w: 220, h: 34 }"
    async with env.session() as page:
        await show(page, "panel-ready-20")
        panel = await page.evaluate("(() => { const r = rig.ui.root.querySelector('.panel').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()")
        # beside the field would land on the expanded panel: the chip goes where it does not
        await page.evaluate(f"rig.ui.showChip(rig.make.chipView({anchor}, {{ placement: 'beside' }}))")
        chip = await chip_rect(page)
        env.report.check(chip["placement"] != "beside" and overlap(chip, panel) < 1, f"panel: the chip landed on the panel {chip} vs {panel}")
        # the same view with the panel collapsed to its pill (small, away from the field) is free to go beside
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ collapsed: true }))")
        await page.evaluate(f"rig.ui.showChip(rig.make.chipView({anchor}, {{ key: 'f0:13', placement: 'beside' }}))")
        env.report.check((await chip_rect(page))["placement"] == "beside", "panel: the chip kept away from a pill that is nowhere near it")
        # an open prompt is part of the dock and is kept clear of as well
        await show(page, "ask-save")
        card = await page.evaluate("(() => { const r = rig.ui.root.querySelector('.card').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()")
        await page.evaluate(f"rig.ui.showChip(rig.make.chipView({anchor}, {{ placement: 'beside' }}))")
        chip = await chip_rect(page)
        env.report.check(overlap(chip, card) < 1, f"panel: the chip landed on the open prompt {chip} vs {card}")
    # 200% zoom (a 640 px window) with the panel open on the right: the chip is narrowed to stay clear of it (UX-05)
    async with env.session({**BASE, "width": 640, "height": 450}) as page:
        await show(page, "panel-ready-20")
        panel = await page.evaluate("(() => { const r = rig.ui.root.querySelector('.panel').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()")
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { questionText: 'What is the best email address to reach you on?' }))")
        chip = await chip_rect(page)
        env.report.check(overlap(chip, panel) < 1, f"panel: at 640 px the chip landed on the open panel {chip} vs {panel}")
        env.report.check(chip["w"] >= 250, f"panel: the chip was squeezed below a readable width ({chip['w']} px)")
        audit = await page.evaluate("rig.audit('chip-verified-email', 'en')")
        env.report.check(not audit["overflow"], f"panel: the narrowed chip overflows {audit['overflow']}")
        # a field the panel does not reach keeps its chip at full width
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-mid'), { key: 'f0:41', placement: 'below' }))")
        again = await chip_rect(page)
        env.report.check(overlap(again, panel) < 1 or again["w"] >= 330, f"panel: a chip with room was narrowed anyway {again}")
    async with env.session({**BASE, "width": 640, "height": 450}) as page:
        await show(page, "panel-collapsed")
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { placement: 'beside' }))")
        chip = await chip_rect(page)
        pill = await page.evaluate("(() => { const r = rig.ui.root.querySelector('.pill-btn').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()")
        env.report.check(overlap(chip, pill) < 1, f"panel: the chip landed on the collapsed panel at 640 px {chip} vs {pill}")


# ---- UX-01: the chip shrinks while the person types ------------------------------------------------------------------------------------------------
async def test_typing_pill(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-conflict")
        full = await chip_rect(page)
        env.report.check(not full["pill"], "typing: the chip is a pill before anyone typed")
        # a synthetic input event (the assistant's own insertions look like this, with the value already in the field) changes nothing
        await page.evaluate("const f = document.getElementById('fx-top'); f.value = 'x'; f.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: 'a' })); f.value = ''")
        env.report.check(not (await chip_rect(page))["pill"], "typing: a synthetic input event shrank the chip")
        await page.focus("#fx-top")
        await page.keyboard.type("al")
        pill = await chip_rect(page)
        env.report.check(pill["pill"] and pill["h"] <= 44 and pill["h"] < full["h"], f"typing: the chip did not shrink to one line {pill} vs {full}")
        env.report.check(await count(page, "chip-pill-text") == 1 and await count(page, "chip-why") == 0, "typing: the pill still carries the full chip")
        env.report.check(await is_visible(page, "chip-replace"), "typing: the pill lost Replace")
        await page.evaluate("rig.resetActions()")
        await click(page, "chip-replace")
        env.report.check(await actions(page) == [{"type": "replace", "key": "f0:12"}], f"typing: Replace in the pill emitted {await actions(page)}")
        # a fresh view of the same field keeps the pill; the field going empty again brings the full chip back
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { conflictsWithCurrent: true }))")
        env.report.check((await chip_rect(page))["pill"], "typing: a re-sent view brought the full chip back while typing")
        await page.keyboard.press("Backspace")
        await page.keyboard.press("Backspace")
        env.report.check(not (await chip_rect(page))["pill"], "typing: an emptied field did not bring the full chip back")
        await page.keyboard.type("x")
        env.report.check((await chip_rect(page))["pill"], "typing: typing again did not shrink it")
        # another field is a new chip
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-mid'), { key: 'f0:99' }))")
        env.report.check(not (await chip_rect(page))["pill"], "typing: the next field's chip started as a pill")
        # a quiet chip shrinks too, and keeps its trust word
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { key: 'f0:5', state: 'NEEDS_YOU', tone: 'neutral', insert: 'never', value: '', operation: null, reasonCode: 'missing_fact' }))")
        await page.keyboard.type("b")
        env.report.check("Needs you" in await text_of(page, "chip-pill-text"), "typing: a quiet pill lost its trust word")
        # no chip, nothing to shrink; typing in the panel's own input is not typing into a field
        await page.evaluate("rig.ui.hideChip()")
        await page.keyboard.type("c")
        env.report.check(await chip_rect(page) is None, "typing: typing made a chip appear")
        await show(page, "panel-research-form-company")
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top')))")
        await click(page, "research-not-this")
        await page.keyboard.type("Globex")
        env.report.check(not (await chip_rect(page))["pill"], "typing: typing into the panel's own input shrank the chip")


# ---- UX-04: what a screen reader hears --------------------------------------------------------------------------------------------------------------------
async def test_insertion_and_prompt_announcements(env: Env) -> None:
    async with env.session(init=MAC) as page:
        await show(page, "chip-verified-email")
        await page.evaluate(SAID)
        # the logic can say what it inserted ...
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Email address', value: 'alex.newport@example.com' })")
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex.newport@example.com"], f"announce: {await page.evaluate('window.__said')}")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Email address', value: 'alex.newport@example.com' })")
        env.report.check(len(await page.evaluate("window.__said")) == 1, "announce: the same sentence twice in a row was said twice")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Phone' })")
        env.report.check((await page.evaluate("window.__said"))[-1] == "Inserted Phone", "announce: without a value")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Why Acme?', value: 'x'.repeat(200) })")
        said = (await page.evaluate("window.__said"))[-1]
        env.report.check(said.startswith("Inserted Why Acme?: ") and len(said) < 90 and said.endswith("…"), f"announce: a long value was not shortened ({said!r})")
        # the region draws nothing and never takes focus
        clip = await page.evaluate("(() => { const el = rig.ui.root.querySelector('[data-testid=announce]'); const r = el.getBoundingClientRect(); return [el.classList.contains('sr'), el.getAttribute('aria-live'), el.getAttribute('role'), r.width, r.height]; })()")
        env.report.check(clip == [True, "polite", "status", 1, 1], f"announce: the live region is not a polite, invisible status {clip}")
    async with env.session() as page:
        # ... and when it does not, the insertion flash says it for the field the chip is on
        await show(page, "chip-verified-email")
        await page.evaluate(SAID)
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(350)
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex.newport@example.com"], f"announce (flash): {await page.evaluate('window.__said')}")
        # a flash somewhere else (a bulk fill, a field the chip left) says nothing of its own
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-mid'), 'verified')")
        await page.wait_for_timeout(350)
        env.report.check(len(await page.evaluate("window.__said")) == 1, "announce (flash): a flash on another field was announced")
        # the "Inserted: label" toast of a click already speaks: the flash does not repeat it
        await page.evaluate("window.__said.length = 0")
        await page.wait_for_timeout(1600)
        await page.evaluate("rig.ui.toast({ kind: 'filled', tone: 'ok', params: { label: 'Email address' } }); rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(350)
        env.report.check(await page.evaluate("window.__said.length") == 0, "announce (flash): said an insertion the toast already announced")
        # an insertion the logic announced itself is not said a second time by its flash, whichever of the two comes first ...
        await page.wait_for_timeout(1600)
        await page.evaluate("window.__said.length = 0")
        # (the logic words it a little differently from the chip: the dedupe of identical sentences must not be what keeps it to one)
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Email address', value: 'alex@example.test' }); rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(450)
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex@example.test"], f"announce (flash): said twice, announce first ({await page.evaluate('window.__said')})")
        await page.wait_for_timeout(1600)
        await page.evaluate("window.__said.length = 0")
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified'); rig.ui.announce({ kind: 'inserted', label: 'Email address', value: 'alex@example.test' })")
        await page.wait_for_timeout(450)
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex@example.test"], f"announce (flash): said twice, flash first ({await page.evaluate('window.__said')})")
        # ... but the logic announcing SOME insertions (the ones that finish after the focus moved on: a file, a combobox) does not silence the
        # others: a Tab insertion it did not announce is still said from its flash
        await page.wait_for_timeout(1600)
        await page.evaluate("window.__said.length = 0")
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(450)
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex.newport@example.com"], f"announce (flash): the fallback fell silent after the logic announced something ({await page.evaluate('window.__said')})")
        # an insertion that finishes after the focus moved on (no chip for that field) is said from the logic's own words
        await page.evaluate("rig.ui.hideChip()")
        await page.wait_for_timeout(1600)
        await page.evaluate("window.__said.length = 0")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Resume / CV *', value: 'Alex-Newport-CV.pdf' })")
        env.report.check(await page.evaluate("window.__said") == ["Inserted Resume / CV: Alex-Newport-CV.pdf"], f"announce: a late insertion said {await page.evaluate('window.__said')}")
    async with env.session() as page:
        # the field scrolled between the chip and the insertion: the flash is still for the field the chip is on
        await show(page, "chip-verified-email")
        await page.evaluate(SAID)
        await page.evaluate("(a) => { rig.ui.updateChipAnchor({ ...a, y: a.y + 40 }); rig.ui.flash({ ...a, y: a.y + 40 }, 'verified'); }", await page.evaluate("rig.anchorOf('fx-top')"))
        await page.wait_for_timeout(350)
        env.report.check(await page.evaluate("window.__said") == ["Inserted Email address: alex.newport@example.com"], f"announce (flash, scrolled): {await page.evaluate('window.__said')}")
    async with env.session(init=MAC) as page:
        # the prompts appear next to the panel, not at the field: they say that they are there and how to reach them
        await show(page, "chip-skip")
        await page.evaluate(SAID)
        await page.evaluate("rig.ui.askSave({ key: 'f0:7', fieldLabel: 'Notice period', valuePreview: 'One month', allowReuse: true, edited: false })")
        env.report.check(
            await page.evaluate("window.__said") == ["Meridian asks: save this answer? Press ⌥⇧M to answer."], f"announce: the save prompt said {await page.evaluate('window.__said')}"
        )
        await page.evaluate("rig.ui.askSave({ key: 'f0:7', fieldLabel: 'Notice period', valuePreview: 'One month', allowReuse: true, edited: false })")
        env.report.check(len(await page.evaluate("window.__said")) == 1, "announce: the same prompt was announced again")
        await page.evaluate("rig.ui.askSave({ key: 'f0:8', fieldLabel: 'Salary', valuePreview: '60k', allowReuse: true, edited: false })")
        await page.wait_for_timeout(1600)
        await page.evaluate("rig.ui.askTrack({ company: 'Acme', title: 'Engineer', evidence: '' })")
        said = await page.evaluate("window.__said")
        env.report.check(said[-1] == "Meridian asks: record this application? Press ⌥⇧M to answer.", f"announce: the record prompt said {said[-1:]}")
        await page.evaluate("rig.ui.setLanguage('es')")
        await page.evaluate("rig.ui.askTrack(null)")
        await page.wait_for_timeout(1600)
        await page.evaluate("rig.ui.askTrack({ company: 'Acme', title: 'Engineer', evidence: '' })")
        env.report.check((await page.evaluate("window.__said"))[-1] == "Meridian pregunta: ¿registrar esta candidatura? Pulsa ⌥⇧M para responder.", "announce (es): record prompt")
    async with env.session(init=WINDOWS) as page:
        await show(page, "chip-skip")
        await page.evaluate(SAID)
        await page.evaluate("rig.ui.askSave({ key: 'f0:7', fieldLabel: 'Notice period', valuePreview: 'One month', allowReuse: true, edited: false })")
        env.report.check("Press Alt+Shift+M to answer" in (await page.evaluate("window.__said"))[-1], "announce (windows): the shortcut is not named")
    async with env.session({**BASE, "lang": "es"}, init=WINDOWS) as page:
        await show(page, "chip-skip", "es")
        await page.evaluate(SAID)
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Correo electrónico', value: 'alex@example.com' })")
        await page.evaluate("rig.ui.askSave({ key: 'f0:7', fieldLabel: 'Aviso previo', valuePreview: 'Un mes', allowReuse: true, edited: false })")
        said = await page.evaluate("window.__said")
        env.report.check(said[0] == "Insertado en Correo electrónico: alex@example.com", f"announce (es): {said}")
        env.report.check(said[1] == "Meridian pregunta: ¿guardar esta respuesta? Pulsa Alt+Mayús+M para responder.", f"announce (es): {said}")


# ---- UX-06: the research form is never a surprise ----------------------------------------------------------------------------------------------------------------
async def test_research_form_dismissal(env: Env) -> None:
    async with env.session() as page:
        # a website that could not be confirmed is a sentence and a link
        await show(page, "panel-research-site-unconfirmed")
        env.report.check(not await is_visible(page, "research-form-site"), "research: the website form opened by itself")
        env.report.check("couldn't confirm Acme Robotics's website. Add it?" in await text_of(page, "research-need"), "research: the sentence")
        await click(page, "research-add-site")
        env.report.check(await is_visible(page, "research-form-site"), "research: the link did not open the form")
        await click(page, "research-cancel-site")
        env.report.check(not await is_visible(page, "research-form-site"), "research: Cancel did not close a form the person opened")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ research: rig.make.researchView({ state: 'partial', quality: 'company_only', needsSite: true, site: null }) }))")
        env.report.check(not await is_visible(page, "research-form-site"), "research: the form came back after Cancel")
        # when the research cannot go on without it (needs_input) the form opens, and Cancel and Esc work and are remembered
        await show(page, "panel-research-needs-site")
        env.report.check(await is_visible(page, "research-form-site"), "research: a needed website did not open its form")
        env.report.check("couldn't confirm Acme Robotics's website. Add it?" in await text_of(page, "research-headline"), f"research: headline {await text_of(page, 'research-headline')!r}")
        await click(page, "research-cancel-site")
        env.report.check(not await is_visible(page, "research-form-site"), "research: Cancel does nothing on a needed website (a dead control)")
        env.report.check(await is_visible(page, "research-add-site") and await is_visible(page, "research-not-this"), "research: the way back to the form and to the company correction is gone")
        again = "rig.ui.setPanel(rig.make.panelView({ research: rig.make.researchView({ state: 'needs_input', quality: 'none', needsSite: true, sources: [], site: null }) }))"
        await page.evaluate(again)
        env.report.check(not await is_visible(page, "research-form-site"), "research: the form reopened after Cancel (not remembered)")
        # Esc
        await show(page, "panel-research-needs-site")
        await page.locator('[data-testid="research-input-site"]').focus()
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "research-form-site"), "research: Esc did not close the form")
        await page.evaluate(again)
        env.report.check(not await is_visible(page, "research-form-site"), "research: the form reopened after Esc")
        env.report.check(await actions(page) == [], f"research: closing emitted {await actions(page)}")
        # another company is another question
        await page.evaluate(again.replace("quality: 'none'", "quality: 'none', company: 'Globex'"))
        env.report.check(await is_visible(page, "research-form-site"), "research: a new company's question stayed silent")
        # a company that cannot be told: the form opens, and is as easy to leave
        await show(page, "panel-research-needs-company")
        env.report.check(await is_visible(page, "research-form-company"), "research: the company form did not open")
        await page.locator('[data-testid="research-input-company"]').focus()
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "research-form-company") and await is_visible(page, "research-not-this"), "research: the company form could not be left")
        # sending answers it for good
        await show(page, "panel-research-needs-site")
        await page.locator('[data-testid="research-input-site"]').focus()
        await page.keyboard.type("acme-robotics.example")
        await page.keyboard.press("Enter")
        env.report.check(await actions(page) == [{"type": "research-site", "domain": "acme-robotics.example"}], f"research: send emitted {await actions(page)}")
        env.report.check(not await is_visible(page, "research-form-site"), "research: the form stayed after sending")
        # no need, no sentence
        await show(page, "panel-research-done-full")
        env.report.check(await count(page, "research-need") == 0, "research: a sentence about a website nobody asked about")
        await show(page, "panel-research-site-unconfirmed", "es")
        env.report.check("No he podido confirmar la web de Acme Robotics. ¿La añades?" in await text_of(page, "research-need"), "research (es): the sentence")
    async with env.session(FRAME) as page:
        await show(page, "panel-research-site-unconfirmed", "es")
        audit = await page.evaluate("rig.audit('panel-research-site-unconfirmed', 'es')")
        env.report.check(not audit["overflow"], f"research: overflow in a 300 px frame {audit['overflow']}")


# ---- UX-10: the chip's actions from the keyboard -------------------------------------------------------------------------------------------------------------------
KEYLOG = """() => {
  window.__keys = [];
  document.addEventListener('keydown', (ev) => { if (!/^(Alt|Shift|Control|Meta)/.test(ev.code)) window.__keys.push([ev.code, ev.defaultPrevented]); });
}"""


async def test_chip_keyboard_actions(env: Env) -> None:
    # UXR-03: the chord must type nothing. Option+digit types a character on a Mac (¡ ™ £ ¢ ∞ § ¶), so a Mac uses Control+Option and the
    # rest Alt+Shift; the printed hint, aria-keyshortcuts and the key match are the same chord.
    async with env.session(init=MAC) as page:
        await page.context.route(
            "https://acme-robotics.example/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body="<title>ok</title>"),
        )
        await show(page, "chip-conflict")
        await page.evaluate(KEYLOG)
        await page.focus("#fx-top")
        env.report.check("⌃⌥1" in await text_of(page, "chip-replace"), f"keys: the Replace button does not print its Mac chord ({await text_of(page, 'chip-replace')!r})")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Control+Alt+1")
        env.report.check(await actions(page) == [{"type": "replace", "key": "f0:12"}], f"keys: Control+Option+1 emitted {await actions(page)}")
        env.report.check(await page.evaluate("window.__keys.length") == 0, "keys: a handled key still reached the page")
        env.report.check(await page.evaluate("document.activeElement && document.activeElement.id") == "fx-top", "keys: the page field lost focus")
        # a key the chip has no action for goes to the page untouched
        await page.keyboard.press("Control+Alt+2")
        env.report.check(await page.evaluate("window.__keys") == [["Digit2", False]], f"keys: an unused key was swallowed {await page.evaluate('window.__keys')}")
        # Option+digit alone is how a Mac types ¡ ™ £ ...: the key must reach the field, whatever the chip is showing, and run nothing
        await page.evaluate("window.__keys.length = 0; rig.resetActions()")
        for combo in ("Alt+1", "Alt+Shift+1", "Control+1", "Meta+Control+Alt+1", "Control+Alt+Shift+1", "Alt+Digit1"):
            await page.keyboard.press(combo)
        env.report.check(await actions(page) == [], f"keys: a combination that is not the chord ran an action ({await actions(page)})")
        env.report.check(
            await page.evaluate("window.__keys") == [["Digit1", False]] * 6, f"keys: a combination that types something was swallowed {await page.evaluate('window.__keys')}"
        )
        # a page script cannot press them
        await page.evaluate("rig.resetActions()")
        await page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Digit1', altKey: true, ctrlKey: true, bubbles: true }))")
        env.report.check(await actions(page) == [], "keys: an untrusted key press ran a chip action")
        # while the person types the pill still has Replace: Option+1 must give them their ¡, not Replace
        await page.keyboard.type("al")
        env.report.check((await chip_rect(page))["pill"], "keys: precondition (the chip is a pill while typing)")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Alt+1")
        env.report.check(await actions(page) == [], "keys: Option+1 pressed while typing replaced the person's own text")
        # a draft: Shorter / Longer / Different angle, Show more, the sources
        await show(page, "chip-draft-long")
        await page.focus("#fx-text")
        for number, instruction in ((2, "shorter"), (3, "longer"), (4, "different")):
            await page.evaluate("rig.resetActions()")
            await page.keyboard.press(f"Control+Alt+{number}")
            env.report.check(
                await actions(page) == [{"type": "regenerate", "key": "f0:30", "instruction": instruction}], f"keys: Control+Option+{number} emitted {await actions(page)}"
            )
        for number, testid in ((2, "chip-regen-shorter"), (3, "chip-regen-longer"), (4, "chip-regen-different"), (5, "chip-more"), (6, "chip-source"), (7, "chip-sources-toggle")):
            hint = await page.locator(f'[data-testid="{testid}"]').first.inner_text()
            env.report.check(f"⌃⌥{number}" in hint, f"keys: {testid} does not print ⌃⌥{number} ({hint!r})")
            shortcuts = await page.locator(f'[data-testid="{testid}"]').first.get_attribute("aria-keyshortcuts")
            env.report.check(shortcuts == f"Control+Alt+{number}", f"keys: {testid} aria-keyshortcuts {shortcuts!r}")
        expanded = lambda: page.locator('[data-testid="chip-more"]').get_attribute("aria-expanded")  # noqa: E731
        env.report.check(await expanded() == "false", "keys: precondition (collapsed)")
        await page.keyboard.press("Control+Alt+5")
        env.report.check(await expanded() == "true", "keys: Control+Option+5 did not show more")
        await page.keyboard.press("Control+Alt+5")
        env.report.check(await expanded() == "false", "keys: Control+Option+5 did not show less")
        await page.keyboard.press("Control+Alt+7")
        env.report.check(await page.locator('[data-testid="chip-sources-toggle"]').get_attribute("aria-expanded") == "true", "keys: Control+Option+7 did not list every source")
        await page.keyboard.press("Control+Alt+7")
        async with page.expect_popup(timeout=5000) as popup_info:
            await page.keyboard.press("Control+Alt+6")
        popup = await popup_info.value
        env.report.check(popup.url.startswith("https://acme-robotics.example/about"), f"keys: Control+Option+6 opened {popup.url}")
        await popup.close()
        env.report.check(await page.evaluate("window.opener") is None and await page.evaluate("document.activeElement && document.activeElement.id") == "fx-text", "keys: opening a source disturbed the page")
        # the logic can run the same action itself (it matches the chord too): True only when the chip really has that control
        await page.evaluate("rig.resetActions()")
        env.report.check(await page.evaluate("rig.ui.chipAction(2)") is True, "keys: chipAction(2) did not run Shorter")
        env.report.check(await actions(page) == [{"type": "regenerate", "key": "f0:30", "instruction": "shorter"}], f"keys: chipAction(2) emitted {await actions(page)}")
        env.report.check(await page.evaluate("[rig.ui.chipAction(1), rig.ui.chipAction(0), rig.ui.chipAction(8), rig.ui.chipAction(2.5)]") == [False] * 4, "keys: chipAction ran something that is not on the chip")
        env.report.check((await page.evaluate("rig.ui.capabilities"))["chipActions"] is True, "keys: the UI does not declare chipActions")
        # no chip, no keys
        await page.evaluate("rig.ui.hideChip()")
        await page.evaluate("window.__keys.length = 0; rig.resetActions()")
        await page.keyboard.press("Control+Alt+2")
        env.report.check(await actions(page) == [] and await page.evaluate("window.__keys.length") == 1, "keys: a hidden chip answered to keys")
        # the chip off screen does not either
        await show(page, "chip-anchor-offscreen")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Control+Alt+1")
        env.report.check(await actions(page) == [], "keys: a chip whose field is off screen answered to keys")
    async with env.session(init=WINDOWS) as page:
        await show(page, "chip-conflict")
        await page.evaluate(KEYLOG)
        env.report.check("Alt+Shift+1" in await text_of(page, "chip-replace"), f"keys (windows): {await text_of(page, 'chip-replace')!r}")
        env.report.check(await page.locator('[data-testid="chip-replace"]').get_attribute("aria-keyshortcuts") == "Alt+Shift+1", "keys (windows): aria-keyshortcuts")
        await page.focus("#fx-top")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Alt+Shift+1")
        env.report.check(await actions(page) == [{"type": "replace", "key": "f0:12"}], f"keys (windows): Alt+Shift+1 emitted {await actions(page)}")
        # Control+Alt is AltGr on many Windows layouts (it types @ # | \\): never taken; Alt+digit alone is the browser's
        await page.evaluate("rig.resetActions()")
        for combo in ("Control+Alt+1", "Alt+1", "Control+Alt+Shift+1", "Meta+Alt+Shift+1"):
            await page.keyboard.press(combo)
        env.report.check(await actions(page) == [], f"keys (windows): a combination that is not the chord ran an action ({await actions(page)})")
        # the chord the logic names wins over the platform guess (a Safari on a Mac behind a different user agent, say)
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { conflictsWithCurrent: true, keyHints: { chipActionPrefix: 'mac' } }))")
        env.report.check("⌃⌥1" in await text_of(page, "chip-replace"), "keys: the chord the logic named was not printed")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Control+Alt+1")
        env.report.check(await actions(page) == [{"type": "replace", "key": "f0:12"}], f"keys: the named chord did not run the action {await actions(page)}")
    # the matcher itself, on events a page could never send (AltGr is a way of typing characters: it is never the chord)
    async with env.session() as page:
        await show(page, "chip-hide")
        got = await page.evaluate(
            """() => {
          const ev = (o) => ({ altKey: false, ctrlKey: false, shiftKey: false, metaKey: false, code: 'Digit1', getModifierState: (k) => !!o.gr && k === 'AltGraph', ...o });
          return [
            rig.chipKeyNumber(ev({ altKey: true, shiftKey: true }), false, 7), rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, gr: true }), false, 7),
            rig.chipKeyNumber(ev({ altKey: true, ctrlKey: true }), true, 7), rig.chipKeyNumber(ev({ altKey: true, ctrlKey: true, gr: true }), true, 7),
            rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, code: 'Digit7' }), false, 7), rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, code: 'Digit8' }), false, 7),
            rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, code: 'Digit0' }), false, 7), rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, code: 'Numpad1' }), false, 7),
            rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, metaKey: true }), false, 7), rig.chipKeyNumber(ev({ altKey: true, shiftKey: true, code: undefined }), false, 7),
          ];
        }"""
        )
        env.report.check(got == [1, 0, 1, 0, 7, 0, 0, 0, 0, 0], f"keys: the chord matcher answered {got}")
    # an expanded short viewport hides the rewrite row: its keys are off with it
    async with env.session({**BASE, "width": 320, "height": 256}) as page:
        await show(page, "chip-draft-long")
        await page.evaluate("rig.resetActions()")
        await page.focus("#fx-text")
        await page.keyboard.press("Alt+Shift+2")
        visible = await is_visible(page, "chip-regen-shorter")
        env.report.check(visible or await actions(page) == [], f"keys: a key ran an action whose button is hidden ({await actions(page)})")


# ---- UX-14: the insertion flash follows its field --------------------------------------------------------------------------------------------------------------------------
async def test_flash_follows_scroll(env: Env) -> None:
    async with env.session() as page:
        await page.evaluate("document.body.style.minHeight = '3000px'")
        await show(page, "chip-hide")
        # a field that belongs to the page (the rig's other fields are fixed to the window, which scrolling never moves; a pane that scrolls
        # by itself is test_flash_follows_a_scrolling_pane)
        await page.evaluate("""() => { const d = document.createElement('div'); d.id = 'flow-field'; d.style.cssText = 'position:absolute;left:760px;top:540px;width:220px;height:30px;background:#ddd';
          document.body.append(d); }""")
        field = await page.evaluate("rig.anchorOf('flow-field')")
        await page.evaluate("(a) => rig.ui.flash(a, 'verified')", field)
        before = await page.locator('[data-testid="flash"]').first.bounding_box()
        await page.evaluate("window.scrollTo(0, 150)")
        await page.wait_for_timeout(60)
        after = await page.locator('[data-testid="flash"]').first.bounding_box()
        env.report.check(before is not None and after is not None and abs((before["y"] - after["y"]) - 150) <= 1.5, f"flash: it stayed put while the page scrolled {before} -> {after}")
        await page.evaluate("window.scrollTo(0, 40)")
        await page.wait_for_timeout(60)
        again = await page.locator('[data-testid="flash"]').first.bounding_box()
        env.report.check(abs((before["y"] - again["y"]) - 40) <= 1.5, f"flash: it did not follow a second scroll {before} -> {again}")
        # an outline drawn after the page already scrolled is where its field is; a later scroll moves it from there
        await page.evaluate("window.scrollTo(0, 100)")
        late = await page.evaluate("rig.anchorOf('flow-field')")
        await page.evaluate("(a) => rig.ui.flash(a, 'draft')", late)
        fresh = await page.locator('[data-testid="flash"]').last.bounding_box()
        env.report.check(abs(fresh["y"] - (late["y"] - 3)) <= 1.5, f"flash: a late flash was drawn off its anchor {fresh} vs {late}")
        await page.evaluate("window.scrollTo(0, 160)")
        await page.wait_for_timeout(60)
        moved = await page.locator('[data-testid="flash"]').last.bounding_box()
        env.report.check(abs((fresh["y"] - moved["y"]) - 60) <= 1.5, f"flash: a late flash did not follow ({fresh} -> {moved})")
        # a field fixed to the window does not move when the window scrolls, and neither does its outline
        await page.wait_for_timeout(1000)
        await page.evaluate("window.scrollTo(0, 0)")
        fixed = await page.evaluate("rig.anchorOf('fx-top')")
        await page.evaluate("(a) => rig.ui.flash(a, 'verified')", fixed)
        await page.evaluate("window.scrollTo(0, 90)")
        await page.wait_for_timeout(60)
        still = await page.locator('[data-testid="flash"]').last.bounding_box()
        env.report.check(abs(still["y"] - (fixed["y"] - 3)) <= 1.5, f"flash: the outline of a fixed field moved with the window {still} vs {fixed}")
        await page.wait_for_timeout(1000)
        env.report.check(await count(page, "flash") == 0, "flash: it did not go away")
        # after it is gone, scrolling does nothing and costs nothing
        await page.evaluate("window.scrollTo(0, 0)")
        env.report.check(await count(page, "flash") == 0, "flash: scrolling brought one back")


# ---- SEC-13: privacy copy ---------------------------------------------------------------------------------------------------------------------------------------------------------------
async def test_privacy_copy(env: Env) -> None:
    async with env.session() as page:
        for lang, must, banned in (
            ("en", ("Your profile and answers stay on this Mac", "except the text Meridian sends to the writing model you chose"), ("Everything stays on this Mac", "never the internet")),
            ("es", ("Tu perfil y tus respuestas se quedan en este Mac", "salvo el texto que Meridian envía al modelo de IA que has elegido"), ("Todo se queda en este Mac", "nunca con internet")),
        ):
            for scene in ("panel-conn-unpaired", "panel-conn-unpaired-noport"):
                await show(page, scene, lang)
                text = await text_of(page, "calm-unpaired")
                for needle in must:
                    env.report.check(needle in text, f"privacy ({lang}): {needle!r} missing from the unpaired card {text!r}")
                for bad in banned:
                    env.report.check(bad.lower() not in text.lower(), f"privacy ({lang}): the overclaim {bad!r} is still in {text!r}")
            strings = await page.evaluate("rig.allStrings()")
            for key, value in strings[lang].items():
                for bad in banned:
                    env.report.check(bad.lower() not in value.lower(), f"privacy ({lang}): {key} still says {bad!r}: {value!r}")
        await shown(page, "panel-research-privacy")
        env.report.check("from this Mac. Your profile and answers are never sent to them." in await text_of(page, "research-privacy"), "privacy: research line")
        await shown(page, "panel-research-privacy", "es")
        env.report.check("Tu perfil y tus respuestas nunca se envían a ellas" in await text_of(page, "research-privacy"), "privacy (es): research line")


# ---- UX-03: Fill verified can be taken back ---------------------------------------------------------------------------------------------------------
async def test_batch_undo(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-batch-undo")
        env.report.check(await text_of(page, "batch-count") == "5 answers inserted", f"undo all: count {await text_of(page, 'batch-count')!r}")
        # below the Fill verified button, so the button just pressed does not move
        fill = await page.locator('[data-testid="panel-fill-verified"]').bounding_box()
        row = await page.locator('[data-testid="panel-batch-undo"]').bounding_box()
        env.report.check(row["y"] > fill["y"] + fill["height"], "undo all: the row sits above the Fill verified button")
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-undo-all")
        env.report.check(await actions(page) == [{"type": "undo-all"}], f"undo all: the panel button emitted {await actions(page)}")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ batchUndo: null }))")
        env.report.check(not await is_visible(page, "panel-batch-undo"), "undo all: the row stayed after the window closed")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ batchUndo: { count: 1 } }))")
        env.report.check(await text_of(page, "batch-count") == "1 answer inserted", "undo all: singular")
        # the toast of Fill verified carries the same action, for as long as the answers can be taken back
        await show(page, "toast-undoable")
        env.report.check(await page.locator('[data-testid="toast-undo"]').get_attribute("tabindex") == "-1", "undo all: the toast button is in the tab order")
        await page.wait_for_timeout(9000)
        env.report.check(await count(page, "toast") == 1, "undo all: the toast went before the answers could no longer be taken back")
        await page.evaluate("rig.resetActions()")
        await click(page, "toast-undo")
        env.report.check(await actions(page) == [{"type": "undo-all"}], f"undo all: the toast button emitted {await actions(page)}")
        env.report.check(await count(page, "toast") == 0, "undo all: the toast stayed after Undo all")
        # a plain toast has no button; an error never has one
        await page.evaluate("rig.ui.toast({ kind: 'filled', tone: 'ok', params: { count: 3 } }); rig.ui.toast({ kind: 'error', tone: 'warn', undoable: true })")
        env.report.check(await count(page, "toast-undo") == 0, "undo all: a toast that cannot be undone offers it")
        await page.evaluate("rig.ui.toast({ kind: 'undone', tone: 'info', params: { count: 5 } })")
        env.report.check("5 answers taken back" in await page.locator('[data-testid="toasts"]').inner_text(), "undo all: the confirmation")
        await page.wait_for_timeout(1)
        await show(page, "toast-undoable", "es")
        env.report.check(await text_of(page, "toast-undo") == "Deshacer todo", "undo all (es): button")
        await page.evaluate("rig.ui.toast({ kind: 'undone', tone: 'info', params: { count: 1 } })")
        env.report.check("1 respuesta deshecha" in await page.locator('[data-testid="toasts"]').inner_text(), "undo all (es): confirmation")
        await show(page, "panel-batch-undo", "es")
        env.report.check(await text_of(page, "panel-undo-all") == "Deshacer todo", "undo all (es): panel button")
    async with env.session(FRAME) as page:
        await show(page, "panel-batch-undo", "es")
        audit = await page.evaluate("rig.audit('panel-batch-undo', 'es')")
        env.report.check(not audit["overflow"], f"undo all: overflow in a 300 px frame {audit['overflow']}")


async def test_writing_model_note_waits_for_an_answer(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-drafts-unknown")
        env.report.check(not await is_visible(page, "note-drafts"), "model note: shown before Meridian said whether a model is set up")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ generation: { available: false, known: true, budgetExhausted: false } }))")
        env.report.check(await is_visible(page, "note-drafts"), "model note: not shown once it is known that none is set up")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ generation: { available: false, budgetExhausted: false } }))")
        env.report.check(await is_visible(page, "note-drafts"), "model note: a view without `known` lost the note")


def wave3_tests() -> list:
    return [
        test_option_outlines,
        test_skipped_file_chips,
        test_stale_drafts,
        test_already_inserted_line,
        test_tracker_menu_items,
        test_duplicate_banner,
        test_first_use_card,
        test_review_card,
        test_help_sheet,
        test_chip_question_and_placement,
        test_chip_keeps_off_the_panel,
        test_typing_pill,
        test_insertion_and_prompt_announcements,
        test_research_form_dismissal,
        test_chip_keyboard_actions,
        test_flash_follows_scroll,
        test_privacy_copy,
        test_batch_undo,
        test_writing_model_note_waits_for_an_answer,
    ]
