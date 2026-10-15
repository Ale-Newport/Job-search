"""Behaviours added in wave 5 (UXFIN): the NEXT field comes first when the chip picks a side, the click-only "Use another CV" menu, the
calm note of paused automatic drafts and the line about a rule that still starts the assistant."""

from __future__ import annotations

from behaviours import Env, actions, click, show
from behaviours_wave3 import chip_rect, count, shown, text_of
from harness import overlap

CHIP_CONTROLS = "[...rig.ui.root.querySelectorAll('.chip button, .chip a')].map((el) => ({ id: el.getAttribute('data-testid'), tab: el.getAttribute('tabindex') }))"


# ---- the next field first --------------------------------------------------------------------------------------------------------
async def test_the_next_field_comes_first(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-hide")
        a = await page.evaluate("rig.anchorOf('fx-pair-a')")
        b = await page.evaluate("rig.anchorOf('fx-pair-b')")
        label = {"x": a["x"], "y": a["y"] - 22, "w": a["w"], "h": 20}
        prev = {"x": a["x"], "y": a["y"] - 66, "w": a["w"], "h": 34}
        blocked = {"x": a["x"] + 400, "y": a["y"] - 20, "w": 300, "h": 130}  # keeps "beside" from being the cleanest side: this is about above and below
        view = "([a, avoid, over]) => rig.ui.showChip(rig.make.chipView(a, { questionText: 'Last name', avoid, ...over }))"
        # named: the chip goes above, on the label and the field above, rather than on the next field
        await page.evaluate(view, [a, [b, label, prev, blocked], {"avoidNext": 1}])
        await page.wait_for_timeout(100)
        chip = await chip_rect(page)
        env.report.check(chip is not None and chip["pill"] and chip["placement"] == "above", f"next: the named next field should push the chip above as a pill {chip}")
        env.report.check(chip is not None and overlap(chip, b) < 1, f"next: the chip still covers the next field {chip} vs {b}")
        # not named: all rectangles count the same and the cheaper side (below, one rectangle) stays (a new chip: no side is remembered)
        await page.evaluate("rig.ui.hideChip()")
        await page.evaluate(view, [a, [b, label, prev, blocked], {}])
        await page.wait_for_timeout(100)
        chip = await chip_rect(page)
        env.report.check(chip is not None and chip["placement"] == "below", f"next: with nothing said about which rectangle is the next field the chip should stay below {chip}")
        # a count that is nonsense never breaks the placement (more than the list, negative, not a number)
        for bad in (99, -3, 1.9):
            await page.evaluate(view, [a, [b, label, prev, blocked], {"avoidNext": bad}])
            await page.wait_for_timeout(60)
            env.report.check(await chip_rect(page) is not None, f"next: avoidNext {bad} made the chip disappear")
        # the field's own options still come first: a choice group's options are never covered, whatever the next field says
        await show(page, "chip-option-radio")
        env.report.check(await count(page, "option-mark") == 1, "next: the recommended option lost its outline")
        # no room above at all: the pill goes below, over the next field (a click-through, with the key printed on it)
        await show(page, "chip-ahead-no-room-above")
        chip = await chip_rect(page)
        env.report.check(chip is not None and chip["pill"] and chip["placement"] == "below", f"next: with no room above the pill should go below {chip}")


# ---- Use another CV ---------------------------------------------------------------------------------------------------------------------
async def test_another_cv_is_a_click_and_never_a_key(env: Env) -> None:
    for lang, use, option in (("en", "Use another CV", "Attach Alex-Newport-CV-data-engineering.pdf"), ("es", "Usar otro CV", "Adjuntar Alex-Newport-CV-data-engineering.pdf")):
        async with env.session() as page:
            await show(page, "chip-cv-menu", lang)
            env.report.check(await count(page, "chip-docs-toggle") == 1 and await text_of(page, "chip-docs-toggle") == use, f"cv ({lang}): the menu link is missing or reads {await text_of(page, 'chip-docs-toggle')!r}")
            env.report.check(await count(page, "chip-doc-list") == 0, f"cv ({lang}): the list is open before the click")
            env.report.check(await page.locator('[data-testid="chip-docs-toggle"]').first.get_attribute("aria-expanded") == "false", f"cv ({lang}): aria-expanded before the click")
            # no control of the chip is a Tab stop, and the menu carries no numbered key (Tab inserts the document on offer, always)
            controls = await page.evaluate(CHIP_CONTROLS)
            env.report.check(all(c["tab"] == "-1" for c in controls), f"cv ({lang}): a chip control is a Tab stop {controls}")
            tabbable = await page.evaluate("rig.tabbables()")
            env.report.check("chip-docs-toggle" not in tabbable and "chip-doc" not in tabbable, f"cv ({lang}): the menu is in the Tab order {tabbable}")
            await click(page, "chip-docs-toggle")
            await page.wait_for_timeout(120)
            env.report.check(await count(page, "chip-doc") == 2, f"cv ({lang}): the list should show both other documents ({await count(page, 'chip-doc')})")
            env.report.check(await page.locator('[data-testid="chip-docs-toggle"]').first.get_attribute("aria-expanded") == "true", f"cv ({lang}): aria-expanded after the click")
            first = page.locator('[data-testid="chip-doc"]').first
            env.report.check(await first.get_attribute("aria-label") == option, f"cv ({lang}): accessible name {await first.get_attribute('aria-label')!r}")
            env.report.check(await first.get_attribute("tabindex") == "-1" and await first.locator("kbd").count() == 0, f"cv ({lang}): a row is a Tab stop or carries a key")
            env.report.check("3" in (await first.inner_text()), f"cv ({lang}): the version is not shown {await first.inner_text()!r}")
            env.report.check(await actions(page) == [], f"cv ({lang}): opening the menu emitted {await actions(page)}")
            # a key press with the page's own focus on the field does nothing about it
            await page.focus("#fx-top")
            for key in ("Tab", "Enter", "ArrowDown", "1", "2"):
                await page.keyboard.press(key)
            env.report.check(not [a for a in await actions(page) if a["type"] == "use-cv"], f"cv ({lang}): a key chose a document {await actions(page)}")
            await page.wait_for_timeout(50)
            if not await count(page, "chip-doc"):
                await shown(page, "chip-cv-menu-open", lang)
            await page.locator('[data-testid="chip-doc"]').first.click()
            await page.wait_for_timeout(100)
            env.report.check(await actions(page) == [{"type": "use-cv", "id": "doc-2", "key": "f0:12"}], f"cv ({lang}): the click emitted {await actions(page)}")
            env.report.check(await count(page, "chip-doc-list") == 0, f"cv ({lang}): the menu stays open after a choice")
    async with env.session() as page:
        # text only: a hostile file name is a string, never markup; a list longer than six is cut; a row without a name is not drawn
        await show(page, "chip-hide")
        a = await page.evaluate("rig.anchorOf('fx-top')")
        docs = [{"id": f"d{i}", "filename": f"<img src=x onerror=window.__pwned=1> {i}.pdf", "version": i} for i in range(9)] + [{"id": "", "filename": "no id.pdf", "version": 1}, {"id": "z", "filename": "  ", "version": 1}]
        await page.evaluate("([a, docs]) => rig.ui.showChip(rig.make.chipView(a, { operation: 'ATTACH_FILE', value: 'CV.pdf', alternatives: docs }))", [a, docs])
        await page.wait_for_timeout(100)
        await click(page, "chip-docs-toggle")
        await page.wait_for_timeout(100)
        env.report.check(await count(page, "chip-doc") == 6, f"cv: at most six documents are listed ({await count(page, 'chip-doc')})")
        env.report.check(await page.evaluate("rig.pwned()") is None, "cv: a file name ran as markup")
        env.report.check(await page.evaluate("rig.ui.root.querySelectorAll('.chip img').length") == 0, "cv: a file name became an element")
        # a pill never carries the menu (it has no room for it): a chip with another document to offer does not fold, even with nothing clean around it
        await show(page, "chip-fold-pill")
        before = await chip_rect(page)
        env.report.check(before is not None and before["pill"], "cv: the reference pill is missing")
        anchor = await page.evaluate("({ x: 300, y: 300, w: 260, h: 34 })")
        await page.evaluate(
            "([a, d]) => rig.ui.showChip(rig.make.chipView(a, { operation: 'ATTACH_FILE', value: 'CV.pdf', placement: 'beside', alternatives: d, "
            "avoid: [{ x: a.x, y: a.y + 74, w: a.w, h: a.h }, { x: a.x, y: a.y - 74, w: a.w, h: a.h }] }))", [anchor, docs[:2]])
        await page.wait_for_timeout(100)
        chip = await chip_rect(page)
        env.report.check(chip is not None and not chip["pill"] and await count(page, "chip-docs-toggle") == 1, f"cv: a chip with documents to choose from folded to a pill {chip}")


# ---- paused drafts ------------------------------------------------------------------------------------------------------------------------
async def test_paused_drafts_note_belongs_to_this_page(env: Env) -> None:
    for lang, words in (("en", "paused automatic drafts on this page: ask for one with Draft with AI"), ("es", "pausado los borradores automáticos en esta página: pide uno con Redactar con IA")):
        async with env.session() as page:
            await show(page, "panel-drafts-paused", lang)
            env.report.check(words in await text_of(page, "note-drafts-paused"), f"paused ({lang}): the note reads {await text_of(page, 'note-drafts-paused')!r}")
            await click(page, "note-drafts-paused-dismiss")
            await page.wait_for_timeout(100)
            env.report.check(await count(page, "note-drafts-paused") == 0, f"paused ({lang}): the note stays after it was closed")
            # the same flag again (the next render of this page): still closed; nothing was remembered for other pages
            await page.evaluate("rig.ui.setPanel(rig.make.panelView({ generation: { available: true, budgetExhausted: false, draftsPaused: true } }))")
            await page.wait_for_timeout(80)
            env.report.check(await count(page, "note-drafts-paused") == 0, f"paused ({lang}): a closed note came back with the next render")
            saved = await page.evaluate("window.__bgLog.filter((m) => m.t === 'ui:set').map((m) => JSON.stringify(m.value))")
            env.report.check(not any("paused" in s for s in saved), f"paused ({lang}): the closing was written to the remembered look {saved}")
            # the flag goes (a new session) and comes back (it ran out again): the note is there again
            await page.evaluate("rig.ui.setPanel(rig.make.panelView({}))")
            await page.wait_for_timeout(60)
            await page.evaluate("rig.ui.setPanel(rig.make.panelView({ generation: { available: true, budgetExhausted: false, draftsPaused: true } }))")
            await page.wait_for_timeout(80)
            env.report.check(await count(page, "note-drafts-paused") == 1, f"paused ({lang}): the note did not come back with a new session")
    async with env.session() as page:
        # a writing model that is missing says that, not both; a budget that ran out says that
        await show(page, "panel-drafts-paused-and-unavailable")
        env.report.check(await count(page, "note-drafts-paused") == 0 and await count(page, "note-drafts") == 1, "paused: with no writing model the note about the model is the one")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ generation: { available: true, budgetExhausted: true, draftsPaused: true } }))")
        await page.wait_for_timeout(80)
        env.report.check(await count(page, "note-drafts-paused") == 0 and await count(page, "note-budget") == 1, "paused: with the budget used up the note about the budget is the one")


# ---- the rule that still applies -----------------------------------------------------------------------------------------------------------
async def test_a_rule_that_still_applies_is_one_calm_line(env: Env) -> None:
    for lang, words in (("en", "A rule for *.greenhouse.io still starts the assistant here. Remove it on the options page or in Meridian (Settings → Browser assistant)."),
                        ("es", "Una regla para *.greenhouse.io sigue iniciando el asistente aquí.")):
        async with env.session() as page:
            await shown(page, "panel-more-always-on-rule", lang)
            env.report.check(words in await text_of(page, "panel-always-on-rule"), f"rule ({lang}): the line reads {await text_of(page, 'panel-always-on-rule')!r}")
            role = await page.locator('[data-testid="panel-always-on-rule"]').first.get_attribute("role")
            env.report.check(role == "status", f"rule ({lang}): the line is not a polite status ({role})")
            switch = page.locator('[data-testid="panel-always-on"]').first
            env.report.check(await switch.get_attribute("aria-checked") == "true", f"rule ({lang}): the switch should still say ON")
            env.report.check(await count(page, "calm-error") == 0 and await count(page, "toast") == 0, f"rule ({lang}): it is not an alarm")
    async with env.session() as page:
        await shown(page, "panel-more-always-on-rule-unnamed")
        env.report.check("Another rule still starts the assistant here" in await text_of(page, "panel-always-on-rule"), "rule: a sentence without a readable rule name needs the general line")
        await shown(page, "panel-more-always-on-rule-gone")
        env.report.check(await count(page, "panel-always-on-rule") == 0, "rule: the line is shown while the switch says OFF")


# ---- Save to tracker on a posting that is already sent -------------------------------------------------------------------------------------
async def test_already_recorded_is_said_not_saved(env: Env) -> None:
    for lang, words in (("en", "Already recorded in Meridian as applied"), ("es", "Ya está registrada en Meridian como enviada")):
        async with env.session() as page:
            await show(page, "chip-hide", lang)
            await page.evaluate("rig.ui.toast({ kind: 'already_recorded', tone: 'info' })")
            await page.wait_for_timeout(120)
            said = await text_of(page, "toast")
            env.report.check(words in said, f"already recorded ({lang}): the toast reads {said!r}")
            env.report.check("saved" not in said.lower() and "guardad" not in said.lower(), f"already recorded ({lang}): it says it saved something: {said!r}")
            kind = await page.locator('[data-testid="toast"]').first.get_attribute("data-kind")
            env.report.check(kind == "already_recorded", f"already recorded ({lang}): data-kind {kind!r}")


def wave5_tests() -> list:
    return [
        test_already_recorded_is_said_not_saved,
        test_the_next_field_comes_first,
        test_another_cv_is_a_click_and_never_a_key,
        test_paused_drafts_note_belongs_to_this_page,
        test_a_rule_that_still_applies_is_one_calm_line,
    ]
