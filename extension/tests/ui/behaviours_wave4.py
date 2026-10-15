"""Behaviours added in wave 4 (second review of the visual layer): a chip that never covers the options of a choice group, folds to one
line when no side is clean and keeps off the panel; chip keys that type nothing; one Tab stop for every list and row of links; Esc inside
the panel; the review card's full text, fold and cap; text hygiene of labels; the flash that follows scrolling panes; the fill
confirmation, duplicates by status, the permission line; announcements of insertions that finish after the focus moved on."""

from __future__ import annotations

from behaviours import BASE, FRAME, Env, actions, click, is_visible, show
from behaviours_wave3 import SAID, chip_rect, count, shown, text_of
from harness import overlap

RECT = "(id) => rig.anchorOf(id)"
UNION = """(ids) => {
  const rs = ids.map((id) => rig.anchorOf(id));
  const x = Math.min(...rs.map((r) => r.x)); const y = Math.min(...rs.map((r) => r.y));
  return { x, y, w: Math.max(...rs.map((r) => r.x + r.w)) - x, h: Math.max(...rs.map((r) => r.y + r.h)) - y };
}"""
PANEL_RECT = "(() => { const r = rig.ui.root.querySelector('.panel').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()"
WIDE_1440 = {**BASE, "name": "beh-1440", "width": 1440, "height": 900}
NARROW = {**BASE, "name": "beh-360", "width": 360, "height": 640}


async def rects(page, ids: list[str]) -> list[dict]:
    return [await page.evaluate(RECT, i) for i in ids]


async def show_group(page, inputs: list[str], options: list[str], recommended: str, over: str = "{}", prev: str = "fxg-prev", nxt: str = "fxg-next") -> tuple[dict, list[dict]]:
    """A radio group the way the logic describes it: the controls are the anchor, every option (control + label) is in `avoid`."""
    anchor = await page.evaluate(UNION, inputs)
    opts = await rects(page, options)
    avoid = [await page.evaluate(RECT, nxt), *opts, await page.evaluate(RECT, prev)]
    rec = await page.evaluate(RECT, recommended)
    await page.evaluate(
        f"([a, avoid, rec]) => rig.ui.showChip(rig.make.chipView(a, {{ operation: 'CHECK_RADIO', value: 'No', optionLabel: 'No', questionText: 'Will you require sponsorship?', "
        f"optionAnchor: rec, avoid, placement: 'beside', ...({over}) }}))",
        [anchor, avoid, rec],
    )
    await page.wait_for_timeout(80)
    return anchor, opts


# ---- UXR-02: the options of the focused group are never under the chip ---------------------------------------------------------------------------------------
async def test_options_are_never_covered(env: Env) -> None:
    for width in (1100, 1920):
        async with env.session({**BASE, "name": f"beh-{width}", "width": width, "height": 900}) as page:
            await show(page, "chip-hide")
            anchor, opts = await show_group(page, ["fxg-h-i1", "fxg-h-i2"], ["fxg-h-o1", "fxg-h-o2"], "fxg-h-i2")
            chip = await chip_rect(page)
            env.report.check(chip is not None, f"options ({width}): no chip")
            env.report.check(all(overlap(chip, o) < 1 for o in opts), f"options ({width}): the chip covers an option of the group {chip} vs {opts}")
            env.report.check(chip["placement"] == "beside", f"options ({width}): the chip should still sit beside the group, after its last option ({chip['placement']})")
            env.report.check(chip["x"] >= max(o["x"] + o["w"] for o in opts), f"options ({width}): beside must start after the printed options {chip} vs {opts}")
            # the recommended option keeps its outline and nothing on top of it
            outline = await page.locator('[data-testid="option-mark"]').first.bounding_box()
            env.report.check(outline is not None and overlap(chip, {"x": outline["x"], "y": outline["y"], "w": outline["width"], "h": outline["height"]}) < 1, f"options ({width}): the outline is covered")
            # one option per line: all three printed labels stay readable
            anchor, opts = await show_group(page, ["fxg-v-i1", "fxg-v-i2", "fxg-v-i3"], ["fxg-v-o1", "fxg-v-o2", "fxg-v-o3"], "fxg-v-i1")
            chip = await chip_rect(page)
            env.report.check(all(overlap(chip, o) < 1 for o in opts), f"options ({width}): the chip covers an option of a vertical group {chip} vs {opts}")
            # while the page scrolls the chip keeps to the same clean side
            for dy in (12, 40):
                await page.evaluate("([a, dy]) => rig.ui.updateChipAnchor({ ...a, y: a.y + dy })", [anchor, dy])
                moved = await chip_rect(page)
                now = [{**o, "y": o["y"] + dy} for o in opts]
                env.report.check(all(overlap(moved, o) < 1 for o in now), f"options ({width}): after scrolling {dy} px the chip covers an option")
    # options much taller than their controls (touch targets): above and below would lie on them, so a long answer that cannot fold goes
    # where it covers no option, even though that covers the field above (a neighbour is soft, an option is not)
    async with env.session({**BASE, "name": "beh-edge", "width": 1100, "height": 900}) as page:
        await show(page, "chip-hide")
        far = {"x": 1010, "y": 380, "w": 14, "h": 14}
        opt = {"x": 1010, "y": 380, "w": 80, "h": 26}  # the option reaches 12 px below its control
        above = {"x": 600, "y": 330, "w": 480, "h": 40}  # a neighbour just above
        await page.evaluate(
            "([a, o, n]) => rig.ui.showChip(rig.make.chipView(a, { operation: 'CHECK_RADIO', value: 'x'.repeat(130), optionLabel: 'No', placement: 'below', avoid: [o, n] }))", [far, opt, above]
        )
        await page.wait_for_timeout(80)
        chip = await chip_rect(page)
        env.report.check(overlap(chip, opt) < 1, f"options: a chip with no room beside covered the option below its control {chip} vs {opt}")
        # a group the logic did not describe: nothing says where the labels run, so the chip does not go beside the controls
        anchor = await page.evaluate(UNION, ["fxg-h-i1", "fxg-h-i2"])
        # (the outline of the recommended option is only its control: it says nothing of where the labels run)
        await page.evaluate(
            "([a, n, o]) => rig.ui.showChip(rig.make.chipView(a, { key: 'f0:50', operation: 'CHECK_RADIO', value: 'No', optionLabel: 'No', optionAnchor: o, placement: 'beside', avoid: [n] }))",
            [anchor, await page.evaluate(RECT, "fxg-next"), await page.evaluate(RECT, "fxg-h-i2")],
        )
        await page.wait_for_timeout(80)
        env.report.check((await chip_rect(page))["placement"] != "beside", "options: an undescribed choice group got its chip beside the controls")
        # a plain text field is not a group: beside stays beside it
        await show(page, "chip-beside")
        env.report.check((await chip_rect(page))["placement"] == "beside", "options: a text field lost its beside placement")


# ---- UXR-12: no clean side: one line, off the panel and the neighbours -----------------------------------------------------------------------------------------
FOLD_VIEW = """([a, over]) => rig.ui.showChip(rig.make.chipView(a, { questionText: 'What is the best email address to reach you on?', placement: 'beside',
  avoid: [{ x: a.x, y: a.y + 74, w: a.w, h: a.h }, { x: a.x, y: a.y - 74, w: a.w, h: a.h }], ...over }))"""


async def test_chip_folds_to_one_line_when_no_side_is_clean(env: Env) -> None:
    anchor = {"x": 340, "y": 400, "w": 480, "h": 34}  # the form column of a 1440 px window; the panel starts at 1084
    nxt = {"x": 340, "y": 474, "w": 480, "h": 34}
    prev = {"x": 340, "y": 326, "w": 480, "h": 34}
    async with env.session(WIDE_1440) as page:
        await show(page, "panel-ready-20")
        panel = await page.evaluate(PANEL_RECT)
        await page.evaluate(FOLD_VIEW, [anchor, {}])
        await page.wait_for_timeout(80)
        chip = await chip_rect(page)
        env.report.check(chip["pill"], f"fold: no side was clean (the panel on the right, a field above and below) and the chip did not fold {chip}")
        env.report.check(all(overlap(chip, r) < 1 for r in (panel, nxt, prev)), f"fold: the folded chip still covers the panel or a neighbour {chip} vs {panel} {nxt} {prev}")
        env.report.check(chip["placement"] == "beside", f"fold: the one-line chip should sit beside the field ({chip['placement']})")
        env.report.check(chip["h"] <= 44, f"fold: the folded chip is not one line ({chip['h']} px)")
        env.report.check(await is_visible(page, "chip-pill-key") and "⇥" in await text_of(page, "chip-pill-key"), "fold: the pill does not say which key inserts the answer")
        # the same hint the full chip carries (the Tab hint), so that what looks for it finds it in either form
        env.report.check(await is_visible(page, "hint-tab") and not await is_visible(page, "hint-enter"), "fold: the pill's key is not the chip's Tab hint")
        env.report.check("alex.newport@example.com" in await text_of(page, "chip-pill-text"), "fold: the pill lost the answer")
        env.report.check(await count(page, "chip-why") == 0 and await count(page, "chip-hints") == 0, "fold: the pill still carries the full chip")
        env.report.check(await text_of(page, "chip-accept") == "Insert", "fold: the pill has no Insert for the pointer")
        await page.evaluate("rig.resetActions()")
        await click(page, "chip-accept")
        env.report.check(await actions(page) == [{"type": "accept", "key": "f0:12"}], f"fold: Insert in the pill emitted {await actions(page)}")
        env.report.check(await page.locator('[data-testid="chip"]').get_attribute("data-compact") == "true", "fold: the pill is not marked as folded")
        # the trust word is not drawn (no room) but it is there for the screen reader, in the person's language
        env.report.check(await text_of(page, "chip-trust") == "Verified" and await page.locator('[data-testid="chip-trust"]').evaluate("(el) => el.classList.contains('sr')"), "fold: the pill lost its trust word")
        # scrolling keeps it a pill (no flicker between the two forms), the answer and the name are those of the full chip
        for dy in (6, 20, 35):
            await page.evaluate("([a, dy]) => rig.ui.updateChipAnchor({ ...a, y: a.y + dy })", [anchor, dy])
            env.report.check((await chip_rect(page))["pill"], f"fold: the chip changed form while scrolling {dy} px")
        label = await page.locator('[data-testid="chip"]').get_attribute("aria-label")
        env.report.check(label == "Suggestion for Email address", f"fold: the pill's name is {label!r}")
        # a protected answer says Enter, an answer the field already disagrees with offers Replace, a 'check this' answer says so
        # a protected answer is where Tab does not insert: never folded, its note and its Enter key stay
        await page.evaluate(FOLD_VIEW, [anchor, {"key": "f0:61", "insert": "explicit", "tone": "locked", "value": "Not a veteran", "keys": ["enter", "shift-tab", "esc"]}])
        env.report.check(not (await chip_rect(page))["pill"] and await is_visible(page, "chip-protected") and await is_visible(page, "hint-enter"), "fold: a protected answer was folded to a pill without its note")
        await page.evaluate(FOLD_VIEW, [anchor, {"key": "f0:62", "state": "INFERRED", "tone": "inferred", "value": "6"}])
        env.report.check("Check this" in await text_of(page, "chip-pill-text"), f"fold: a 'check this' answer is not marked ({await text_of(page, 'chip-pill-text')!r})")
        await page.evaluate(FOLD_VIEW, [anchor, {"key": "f0:63", "conflictsWithCurrent": True}])
        env.report.check(await is_visible(page, "chip-replace") and await count(page, "chip-pill-key") == 0, "fold: a conflicting answer's pill must offer Replace and no Tab hint")
        await page.evaluate("rig.resetActions()")
        await click(page, "chip-replace")
        env.report.check(await actions(page) == [{"type": "replace", "key": "f0:63"}], f"fold: Replace in the pill emitted {await actions(page)}")
        # what a pill cannot say is never folded: a draft, an Undo, a reason, a stale note, a blocked insertion
        draft = "{ key: 'f0:70', state: 'GENERATED_DRAFT', tone: 'draft', value: 'x'.repeat(160), canRegenerate: true }"
        for name, over, expect_testid in (
            ("a draft", draft, "chip-more"), ("an Undo", "{ key: 'f0:71', undoable: true }", "chip-undo"), ("a stale note", "{ key: 'f0:72', staleReason: 'research' }", "chip-stale"),
            ("a blocked insertion", "{ key: 'f0:73', insert: 'never', duplicateBlocksInsert: true }", "chip-duplicate"),
        ):
            await page.evaluate(f"([a]) => rig.ui.showChip(rig.make.chipView(a, {{ placement: 'below', avoid: [{{ x: a.x, y: a.y + 74, w: a.w, h: a.h }}, {{ x: a.x, y: a.y - 74, w: a.w, h: a.h }}], ...({over}) }}))", [anchor])
            await page.wait_for_timeout(60)
            env.report.check(not (await chip_rect(page))["pill"] and await count(page, expect_testid) >= 1, f"fold: {name} was folded to a pill that cannot show it")
        # a chip whose side is clean is never folded
        await page.evaluate("([a]) => rig.ui.showChip(rig.make.chipView({ ...a, y: 120 }, { key: 'f0:80', placement: 'below' }))", [anchor])
        env.report.check(not (await chip_rect(page))["pill"], "fold: a chip with room was folded anyway")
        # ... and one that only brushes a neighbour (a sliver of its margin, under a 24 px square) keeps its explanation and its keys
        await page.evaluate("([a]) => rig.ui.showChip(rig.make.chipView(a, { key: 'f0:81', placement: 'below' }))", [anchor])
        whole = await chip_rect(page)
        sliver_below = {"x": whole["x"] + whole["w"] - 30, "y": whole["y"] + whole["h"] - 8, "w": 60, "h": 30}
        sliver_above = {"x": whole["x"] + whole["w"] - 30, "y": anchor["y"] - 6 - whole["h"] - 22, "w": 60, "h": 30}
        await page.evaluate("([a, avoid]) => rig.ui.showChip(rig.make.chipView(a, { key: 'f0:82', placement: 'below', avoid }))", [anchor, [sliver_below, sliver_above]])
        brushed = await chip_rect(page)
        env.report.check(not brushed["pill"], f"fold: a chip that only brushes a neighbour was folded to a pill {brushed}")
    # the window grows: the same chip unfolds once the full chip sits cleanly
    async with env.session({**BASE, "name": "beh-grow", "width": 1440, "height": 900}) as page:
        await show(page, "panel-ready-20")
        await page.evaluate(FOLD_VIEW, [anchor, {}])
        await page.wait_for_timeout(60)
        env.report.check((await chip_rect(page))["pill"], "fold: precondition (folded at 1440)")
        await page.set_viewport_size({"width": 1920, "height": 900})
        await page.wait_for_timeout(250)
        grown = await chip_rect(page)
        env.report.check(not grown["pill"] and grown["placement"] == "beside", f"fold: the chip stayed folded after the window grew wide enough {grown}")
    # the full 1920 px window had room all along
    async with env.session({**BASE, "name": "beh-1920", "width": 1920, "height": 900}) as page:
        await show(page, "panel-ready-20")
        await page.evaluate(FOLD_VIEW, [anchor, {}])
        await page.wait_for_timeout(60)
        env.report.check(not (await chip_rect(page))["pill"], "fold: a chip folded in a window with room beside the field")
    # 1024 px: the field is nearly as wide as the window, above and below are the neighbours', so one line goes beside, below or above, covering none
    async with env.session({**BASE, "name": "beh-1024", "width": 1024, "height": 768}) as page:
        await show(page, "panel-ready-20")
        panel = await page.evaluate(PANEL_RECT)
        a = {"x": 300, "y": 400, "w": 340, "h": 34}
        await page.evaluate(FOLD_VIEW, [a, {}])
        await page.wait_for_timeout(60)
        chip = await chip_rect(page)
        env.report.check(overlap(chip, panel) < 1, f"fold (1024): the chip landed on the panel {chip} vs {panel}")


async def test_a_label_around_the_field_is_not_an_option(env: Env) -> None:
    """The label of a text field often WRAPS the field (it is level with it): that is no option of a group. A stacked form with a few pixels
    between its fields leaves no clean place for the chip; it keeps to the side the logic chose (above, so that the NEXT field stays in
    view) instead of leaving it because the label's row counts as something that must never be touched."""
    anchor = {"x": 40, "y": 374, "w": 420, "h": 34}
    prev = {"x": 40, "y": 316, "w": 420, "h": 34}
    nxt = {"x": 40, "y": 432, "w": 420, "h": 34}
    wrapper = {"x": 40, "y": 358, "w": 420, "h": 50}
    async with env.session({**BASE, "name": "beh-560", "width": 560, "height": 900}) as page:
        await show(page, "chip-hide")
        view = "([a, avoid]) => rig.ui.showChip(rig.make.chipView(a, { key: 'f0:91', placement: 'above', avoid }))"
        await page.evaluate(view, [anchor, [nxt, prev, wrapper]])
        await page.wait_for_timeout(80)
        chip = await chip_rect(page)
        env.report.check(overlap(chip, nxt) < 1, f"label: the chip lay on the NEXT field although the side above was the logic's choice {chip} vs {nxt}")
        env.report.check(chip["placement"] == "above", f"label: the chip left the side the logic chose ({chip['placement']})")


# ---- the typing pill must not outlive the insertion -----------------------------------------------------------------------------------------------------------
async def test_typing_pill_ends_with_the_insertion(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-conflict")
        await page.focus("#fx-top")
        await page.keyboard.type("Sm")
        env.report.check((await chip_rect(page))["pill"], "typing: precondition (a pill while typing)")
        # Replace: the logic answers with the same field, now undoable: the full chip with its Undo, not a pill
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { undoable: true, conflictsWithCurrent: false }))")
        env.report.check(not (await chip_rect(page))["pill"] and await is_visible(page, "chip-undo"), "typing: Replace left the chip a pill with no Undo")
        # typing again after the insertion is typing again
        await page.keyboard.type("x")
        env.report.check((await chip_rect(page))["pill"], "typing: typing after the insertion did not shrink the chip")
        # an insertion the logic reports through the flash ends it too
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        env.report.check(not (await chip_rect(page))["pill"], "typing: the insertion's flash did not end the pill")
        # a flash on another field does not
        await page.keyboard.type("y")
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-mid'), 'verified')")
        env.report.check((await chip_rect(page))["pill"], "typing: a flash on another field ended the pill")
        # the field already holds the answer: nothing to compete with
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { alreadyInserted: true }))")
        env.report.check(not (await chip_rect(page))["pill"], "typing: an already inserted answer stayed a pill")


# ---- UXR-09: the flash follows whatever scrolls ---------------------------------------------------------------------------------------------------------------------
async def test_flash_follows_a_scrolling_pane(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-hide")
        field = await page.evaluate(RECT, "fx-scroll-a")
        await page.evaluate("(a) => rig.ui.flash(a, 'verified')", field)
        before = await page.locator('[data-testid="flash"]').first.bounding_box()
        env.report.check(before is not None and abs(before["y"] - (field["y"] - 3)) <= 1.5, f"flash (pane): drawn off its field {before} vs {field}")
        await page.evaluate("document.getElementById('fx-scroller').scrollTop = 70")
        await page.wait_for_timeout(120)
        moved = await page.locator('[data-testid="flash"]').first.bounding_box()
        now = await page.evaluate(RECT, "fx-scroll-a")
        env.report.check(abs((before["y"] - moved["y"]) - 70) <= 1.5 and abs(moved["y"] - (now["y"] - 3)) <= 1.5,
                         f"flash (pane): it hung in place while the pane scrolled {before} -> {moved} (field now {now})")
        await page.evaluate("document.getElementById('fx-scroller').scrollTop = 20")
        await page.wait_for_timeout(120)
        again = await page.locator('[data-testid="flash"]').first.bounding_box()
        env.report.check(abs((before["y"] - again["y"]) - 20) <= 1.5, f"flash (pane): it did not follow the pane back {before} -> {again}")
        # the window and the pane scroll together
        await page.evaluate("document.body.style.minHeight = '3000px'")
        await page.wait_for_timeout(1000)
        await page.evaluate("document.getElementById('fx-scroller').scrollTop = 0")
        field = await page.evaluate(RECT, "fx-scroll-a")
        await page.evaluate("(a) => rig.ui.flash(a, 'draft')", field)
        base = await page.locator('[data-testid="flash"]').last.bounding_box()
        await page.evaluate("document.getElementById('fx-scroller').scrollTop = 33; window.scrollTo(0, 50)")
        await page.wait_for_timeout(120)
        both = await page.locator('[data-testid="flash"]').last.bounding_box()
        now = await page.evaluate(RECT, "fx-scroll-a")
        env.report.check(abs(both["y"] - (now["y"] - 3)) <= 1.5, f"flash (pane): it lost the field when pane and window scrolled together {both} vs {now} (was {base})")
        await page.wait_for_timeout(1000)
        env.report.check(await count(page, "flash") == 0, "flash (pane): it did not go away")
        # a pane that lives inside a page component's shadow tree: its scroll events never leave the tree, the outline still follows
        await page.evaluate("""() => {
          const host = document.createElement('div'); host.id = 'pane-host'; host.style.cssText = 'position:fixed;left:700px;top:520px;width:240px;height:90px';
          const root = host.attachShadow({ mode: 'open' });
          root.innerHTML = '<div id="pane" style="width:240px;height:90px;overflow:auto;background:#fff"><input id="pane-a" style="display:block;margin:10px 8px;width:200px;height:26px"><div style="height:400px"></div></div>';
          document.body.append(host);
        }""")
        inner = await page.evaluate("(() => { const r = document.getElementById('pane-host').shadowRoot.getElementById('pane-a').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()")
        await page.evaluate("(a) => rig.ui.flash(a, 'verified')", inner)
        started = await page.locator('[data-testid="flash"]').last.bounding_box()
        await page.wait_for_timeout(120)
        await page.evaluate("document.getElementById('pane-host').shadowRoot.getElementById('pane').scrollTop = 6")
        await page.wait_for_timeout(200)
        followed = await page.locator('[data-testid="flash"]').last.bounding_box()
        env.report.check(followed is not None and started is not None and abs((started["y"] - followed["y"]) - 6) <= 1.5, f"flash (pane): an outline did not follow a pane inside a shadow tree {started} -> {followed}")
        await page.wait_for_timeout(900)
        # a point with no element of the page (the UI's own chip, a gap): the window scroll still moves it
        await page.evaluate("window.scrollTo(0, 0); rig.ui.flash({ x: 5, y: 5, w: 4, h: 4 }, 'verified'); window.scrollTo(0, 25)")
        await page.wait_for_timeout(120)
        edge = await page.locator('[data-testid="flash"]').last.bounding_box()
        env.report.check(edge is not None and abs(edge["y"] - (5 - 3 - 25)) <= 1.5, f"flash (pane): no field at its centre and it ignored the window scroll {edge}")


# ---- UXR-03: chip keys that type nothing (in test_chip_keyboard_actions) and the logic's own call -----------------------------------------------------------------
# ---- UXR-13: one Tab stop for every list and row of links -------------------------------------------------------------------------------------------------------------
async def stops(page) -> list[str]:
    return [item.split(":", 1)[1] for item in await page.evaluate("rig.tabbables()")]


async def test_roving_lists(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        tabbable = await stops(page)
        env.report.check(len(tabbable) < 8, f"roving: the panel is {len(tabbable)} Tab stops long {tabbable}")
        env.report.check(tabbable.count("progress-item") == 1, f"roving: the field list is {tabbable.count('progress-item')} Tab stops, not one")
        env.report.check(sum(1 for t in tabbable if t in ("research-toggle", "research-not-this", "research-add-site")) == 1, f"roving: the research links are not one stop {tabbable}")
        env.report.check(sum(1 for t in tabbable if t in ("panel-settings", "panel-help")) == 1, f"roving: the footer links are not one stop {tabbable}")
        tabindexes = await page.evaluate("[...rig.ui.root.querySelectorAll('[data-testid=progress-item]')].map((el) => el.getAttribute('tabindex'))")
        env.report.check(tabindexes.count("0") == 1 and tabindexes.count("-1") == len(tabindexes) - 1, f"roving: tabindex of the rows {tabindexes}")
        current = await page.evaluate("rig.ui.root.querySelector('.p-item.current').getAttribute('data-key')")
        landed = await page.evaluate("rig.ui.root.querySelector('[data-testid=progress-item][tabindex=\"0\"]').getAttribute('data-key')")
        env.report.check(landed == current, f"roving: Tab should land on the row of the field the person is on ({landed} vs {current})")
        # the real keyboard: Tab to the list, arrows, Home, End, Enter
        tab, back = ("Alt+Tab", "Alt+Shift+Tab") if env.engine == "webkit" else ("Tab", "Shift+Tab")
        await page.focus("#fx-text")
        for _ in range(12):
            await page.keyboard.press(tab)
            info = await page.evaluate("rig.focusedInfo()")
            if info["testid"] == "progress-item":
                break
        else:
            env.report.fail("roving: Tab never reached the field list")
            return
        key = lambda: page.evaluate("rig.ui.root.activeElement.getAttribute('data-key')")  # noqa: E731
        start = await key()
        await page.keyboard.press("ArrowDown")
        second = await key()
        env.report.check(second != start, "roving: ArrowDown did not move to the next row")
        env.report.check(await page.evaluate("rig.ui.root.activeElement.getAttribute('tabindex')") == "0", "roving: the focused row is not the tab stop")
        env.report.check(await page.evaluate("rig.ui.root.querySelector('[data-key=\"%s\"]').getAttribute('tabindex')" % start) == "-1", "roving: the row left behind is still a tab stop")
        await page.keyboard.press("ArrowUp")
        env.report.check(await key() == start, "roving: ArrowUp did not come back")
        await page.keyboard.press("Home")
        first = await key()
        await page.keyboard.press("ArrowUp")
        env.report.check(await key() == first, "roving: ArrowUp at the first row left the list")
        await page.keyboard.press("End")
        last = await key()
        await page.keyboard.press("ArrowDown")
        env.report.check(await key() == last, "roving: ArrowDown at the last row left the list")
        await page.keyboard.press("Home")
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("ArrowDown")
        chosen = await key()
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Enter")
        env.report.check(await actions(page) == [{"type": "focus-item", "key": chosen}], f"roving: Enter on a row emitted {await actions(page)}")
        # a progress update while the person is in the list keeps the keyboard on the same row
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillableCount: 3 }))")
        env.report.check(await key() == chosen, "roving: a panel update moved the keyboard off its row")
        # Tab leaves the list for the next control and Shift+Tab comes back to the same row
        await page.keyboard.press(tab)
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-fill-verified", f"roving: Tab from the list went to {(await page.evaluate('rig.focusedInfo()'))['testid']}")
        await page.keyboard.press(back)
        env.report.check(await key() == chosen, "roving: Shift+Tab did not come back to the row the person left")
        # leaving the list for good and coming back later lands on the field the person is on then, not on the row they once visited
        await page.keyboard.press(tab)
        await page.evaluate("(() => { const v = rig.make.panelView({}); v.progress.items = v.progress.items.map((item, n) => ({ ...item, current: n === 3 })); rig.ui.setPanel(v); })()")
        await page.keyboard.press(back)
        env.report.check(await key() == "f0:3", f"roving: coming back landed on {await key()}, not on the field the person is on (f0:3)")
        # letters are the page's: nothing is swallowed
        await page.evaluate("window.__x = []; document.addEventListener('keydown', (e) => window.__x.push(e.key))")
        await page.keyboard.press("a")
        env.report.check(await page.evaluate("window.__x") == ["a"], "roving: a letter key was swallowed")
        # the research links: one stop, left and right
        await page.evaluate("rig.ui.root.querySelector('[data-testid=research-toggle]').focus()")
        await page.keyboard.press("ArrowRight")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "research-not-this", "roving: ArrowRight did not move along the research links")
        await page.keyboard.press("End")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "research-add-site", "roving: End did not reach the last research link")
        # the footer
        await page.evaluate("rig.ui.root.querySelector('[data-testid=panel-settings]').focus()")
        await page.keyboard.press("ArrowRight")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-help", "roving: ArrowRight did not move from Settings to Help")
        await page.keyboard.press("ArrowLeft")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-settings", "roving: ArrowLeft did not come back")
        # synthetic arrows do nothing
        await page.evaluate("rig.ui.root.querySelector('[data-testid=progress-item]').focus()")
        before = await key()
        await page.evaluate("rig.ui.root.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, composed: true }))")
        env.report.check(await key() == before, "roving: a script moved the keyboard with a synthetic arrow")
    # the busiest panel (every note, the review, the menu closed) is still short; the open menu adds its own switches
    async with env.session() as page:
        await show(page, "panel-review-everything")
        busy = await stops(page)
        env.report.check(busy.count("progress-item") == 1 and busy.count("review-item") <= 1, f"roving: the busy panel's lists are not single stops {busy}")
        await show(page, "panel-more-open")
        env.report.check((await stops(page)).count("progress-item") == 1, "roving: the open menu broke the list")
    async with env.session(FRAME) as page:
        await show(page, "panel-ready-20")
        env.report.check(len(await stops(page)) < 8, f"roving: the panel in a 300 px frame is {len(await stops(page))} stops long")


# ---- UX-02: Esc inside the panel gives the keyboard back to the form ---------------------------------------------------------------------------------------------------
async def test_panel_escape(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        await page.evaluate("rig.ui.focusPanel()")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(await actions(page) == [{"type": "panel-escape"}], f"escape: Esc in the panel emitted {await actions(page)}")
        # an open menu closes first, and the keyboard stays on its button; the next Esc is the panel's
        await click(page, "panel-more")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "panel-more-box") and await actions(page) == [], f"escape: Esc did not just close the menu ({await actions(page)})")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-more", "escape: the menu's button did not get the keyboard back")
        await page.keyboard.press("Escape")
        env.report.check(await actions(page) == [{"type": "panel-escape"}], f"escape: the second Esc emitted {await actions(page)}")
        # the research form: Esc in the field, or on its buttons, closes it and nothing else
        await click(page, "research-not-this")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "research-form-company") and await actions(page) == [], f"escape: Esc in the research field emitted {await actions(page)}")
        await click(page, "research-not-this")
        await page.locator('[data-testid="research-go-company"]').focus()
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "research-form-company") and await actions(page) == [], f"escape: Esc on the Research button emitted {await actions(page)}")
        # the cheat sheet closes with it
        await click(page, "panel-help")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(not await is_visible(page, "help-sheet") and "panel-escape" not in str(await actions(page)), f"escape: Esc in the sheet emitted {await actions(page)}")
        # a modified or untrusted Escape is not the person's
        await page.evaluate("rig.ui.focusPanel()")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Shift+Escape")
        await page.evaluate("rig.ui.root.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, composed: true }))")
        env.report.check(await actions(page) == [], f"escape: a modified or synthetic Esc emitted {await actions(page)}")
        # the fill question closes with Esc and says so
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 2, items: [{ key: 'a', label: 'First name', value: 'Alex' }, { key: 'b', label: 'Last name', value: 'Newport' }] } }))")
        await page.locator('[data-testid="fill-confirm"]').focus()
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(await actions(page) == [{"type": "fill-cancel"}], f"escape: Esc on the fill question emitted {await actions(page)}")
    async with env.session() as page:
        # a prompt: the keyboard reaches it with Alt+Shift+M and leaves it with Esc
        await show(page, "ask-save")
        await page.evaluate("rig.ui.focusPanel()")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(await actions(page) == [{"type": "panel-escape"}], f"escape: Esc on a prompt emitted {await actions(page)}")
        # the panel put away to its pill
        await show(page, "panel-collapsed")
        await page.evaluate("rig.ui.focusPanel()")
        await page.evaluate("rig.resetActions()")
        await page.keyboard.press("Escape")
        env.report.check(await actions(page) == [{"type": "panel-escape"}], f"escape: Esc on the pill emitted {await actions(page)}")
    async with env.session() as page:
        # a page's own dialog, with the panel inside it: Esc inside the panel is the panel's, it must not close the dialog
        await show(page, "panel-ready-6")
        await page.evaluate("document.getElementById('modal').showModal()")
        await page.wait_for_timeout(100)
        await page.evaluate("rig.ui.focusPanel()")
        await page.keyboard.press("Escape")
        env.report.check(await page.evaluate("document.getElementById('modal').open"), "escape: Esc in the panel closed the page's dialog")


# ---- UXR-15: the review card shows the whole question, folds, and nothing pushes Fill verified away ---------------------------------------------------------------------
async def test_review_card_and_the_cards_area(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-review-long")
        card = await page.locator('[data-testid="panel-review"]').bounding_box()
        rows = page.locator('[data-testid="review-item"]')
        env.report.check(await rows.count() == 5, f"review: {await rows.count()} rows")
        for i in range(await rows.count()):
            row = rows.nth(i)
            label = row.locator(".p-label")
            full = (await label.get_attribute("title")) or ""
            aria = (await row.get_attribute("aria-label")) or ""
            env.report.check(len(full) > 40 and aria == f"Go to {full}", f"review: row {i} does not carry its whole question (title {full!r}, name {aria!r})")
            box = await label.bounding_box()
            env.report.check(box["width"] >= 0.7 * card["width"], f"review: row {i} is squeezed to {box['width']:.0f} of {card['width']:.0f} px")
            lines = await label.evaluate("(el) => Math.round(el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight))")
            env.report.check(lines <= 2, f"review: row {i} takes {lines} lines")
            env.report.check(lines == 2 or len(full) < 45, f"review: row {i} shows its question on {lines} line(s): a long question is cut at one line")
        # the question words show in the row (two lines), not an ellipsis after a few letters
        shown_text = await rows.nth(1).locator(".p-label").inner_text()
        env.report.check(shown_text.startswith("Will you now or in the future require sponsorship"), f"review: {shown_text!r}")
        # fold: the title is a button, the list goes, the keyboard stays on it
        fold = page.locator('[data-testid="review-fold"]')
        env.report.check(await fold.get_attribute("aria-expanded") == "true", "review: the fold does not say it is open")
        await click(page, "review-fold")
        env.report.check(await fold.get_attribute("aria-expanded") == "false" and not await is_visible(page, "review-item"), "review: the fold did not fold")
        env.report.check(await text_of(page, "review-title") == "7 required questions are still empty", "review: the folded card lost its title")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "review-fold", "review: folding lost the keyboard")
        await click(page, "review-fold")
        env.report.check(await is_visible(page, "review-item"), "review: the fold did not unfold")
        # the list is not a pile of tab stops
        env.report.check((await stops(page)).count("review-item") == 1, f"review: rows are not one stop {await stops(page)}")
    async with env.session() as page:
        # the worst case: the review, every note, the research strip and the whole list. The cards share a capped area and Fill verified stays
        # inside what the person sees of the panel without scrolling it
        await show(page, "panel-review-everything")
        notices = await page.locator('[data-testid="panel-notices"]').bounding_box()
        env.report.check(notices["height"] <= 270, f"cards: the cards' area is {notices['height']:.0f} px tall")
        body = await page.locator('[data-testid="panel-body"]').bounding_box()
        fill = await page.locator('[data-testid="panel-fill-verified"]').bounding_box()
        env.report.check(fill["y"] + fill["height"] <= body["y"] + body["height"] + 1, f"cards: Fill verified is below the fold ({fill} vs the body {body})")
        # pinned to the bottom of the panel's body; not while the open menu leaves the body so little room
        pinned = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=panel-actions]')).position")
        env.report.check(pinned == "sticky", f"cards: Fill verified is not pinned ({pinned})")
        await click(page, "panel-more")
        unpinned = await page.evaluate("getComputedStyle(rig.ui.root.querySelector('[data-testid=panel-actions]')).position")
        env.report.check(unpinned == "static", f"cards: Fill verified stays pinned while the menu is open ({unpinned})")
        # the first-use card folds too, and its title says what it is
        await show(page, "panel-first-use")
        env.report.check(await is_visible(page, "first-steps"), "first use: the steps are missing")
        await click(page, "first-fold")
        env.report.check(not await is_visible(page, "first-steps") and await is_visible(page, "first-title"), "first use: the fold did not fold")
        env.report.check(await text_of(page, "first-title") == "How it works", "first use: the folded card lost its title")
    async with env.session(FRAME) as page:
        for lang in ("en", "es"):
            await shown(page, "panel-review-long", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-review-long", lang])
            env.report.check(not audit["overflow"], f"review: overflow in a 300 px frame ({lang}) {audit['overflow']}")
            await shown(page, "panel-review-folded", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-review-folded", lang])
            env.report.check(not audit["overflow"], f"review (folded): overflow in a 300 px frame ({lang}) {audit['overflow']}")
    async with env.session() as page:
        # progress rows carry the whole label as a tooltip too
        await show(page, "panel-ready-20")
        titles = await page.evaluate("[...rig.ui.root.querySelectorAll('[data-testid=progress-item] .p-label')].map((el) => [el.textContent, el.getAttribute('title')])")
        env.report.check(all(text == title for text, title in titles), f"progress rows: a label without its tooltip {titles[:3]}")


# ---- Help: the sheet is the quick answer, the options page the long one ------------------------------------------------------------------------------------------------------
async def test_help_full_link(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        await page.evaluate("rig.resetActions()")
        await click(page, "panel-help")
        env.report.check(await actions(page) == [{"type": "keys-help"}], f"help: the Help link emitted {await actions(page)}")
        await page.evaluate("rig.resetActions()")
        await click(page, "help-full")
        env.report.check(await actions(page) == [{"type": "open-options", "anchor": "help"}], f"help: Open full help emitted {await actions(page)}")
        env.report.check(await text_of(page, "help-full") == "Open full help", "help: the link's label")
        env.report.check(await is_visible(page, "help-sheet"), "help: opening the full help closed the sheet")
        await shown(page, "panel-help", "es")
        env.report.check(await text_of(page, "help-full") == "Abrir la ayuda completa", "help (es): the link's label")
    async with env.session(FRAME) as page:
        for lang in ("en", "es"):
            await shown(page, "panel-help", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-help", lang])
            env.report.check(not audit["overflow"], f"help: overflow in a 300 px frame ({lang}) {audit['overflow']}")


# ---- UXR-14: labels without the page's required marks ---------------------------------------------------------------------------------------------------------------------------
async def test_labels_without_required_marks(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate(SAID)
        view = "rig.make.chipView(rig.anchorOf('fx-top'), { fieldLabel: 'Email *', questionText: 'Full name✱', key: 'f0:90' })"
        await page.evaluate(f"rig.ui.showChip({view})")
        env.report.check(await text_of(page, "chip-question") == "Full name", f"labels: the question line says {await text_of(page, 'chip-question')!r}")
        env.report.check(await page.locator('[data-testid="chip"]').get_attribute("aria-label") == "Suggestion for Email", "labels: the chip's name has the star")
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(450)
        said = await page.evaluate("window.__said")
        env.report.check(any(line.startswith("Inserted Email: ") for line in said) and not any("*" in line or "✱" in line for line in said), f"labels: announced {said}")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Are you legally authorized to work in the United Kingdom? *', value: 'Yes' })")
        env.report.check((await page.evaluate("window.__said"))[-1] == "Inserted Are you legally authorized to work in the United Kingdom?: Yes", f"labels: {(await page.evaluate('window.__said'))[-1]!r}")
        await page.evaluate("rig.ui.toast({ kind: 'filled', tone: 'ok', params: { label: 'Phone (required)' } })")
        env.report.check("Inserted: Phone" in await page.locator('[data-testid="toasts"]').inner_text() and "required" not in await page.locator('[data-testid="toasts"]').inner_text(), "labels: the toast kept the mark")
        await page.evaluate("rig.ui.askSave({ key: 'f0:7', fieldLabel: 'Notice period *', valuePreview: 'One month', allowReuse: true, edited: false })")
        env.report.check(await text_of(page, "save-field") == "Notice period", "labels: the save prompt kept the mark")
        await page.evaluate("rig.ui.setMarks([{ key: 'm', anchor: rig.anchorOf('fx-mid'), tone: 'inferred', label: 'Years of experience *' }])")
        env.report.check(await page.locator('[data-testid="mark"]').first.get_attribute("aria-label") == "Years of experience: inserted answer, check this", "labels: the marker's name kept the mark")
    async with env.session() as page:
        # the panel row: the page's star goes, the panel's own marker stays, once
        items = "[{ key: 'f0:0', label: 'First Name *', section: 'About you', status: 'pending', required: true, current: true }, { key: 'f0:1', label: 'Resume/CV', section: 'About you', status: 'pending', required: true, current: false }]"
        await show(page, "panel-ready-6")
        await page.evaluate(f"rig.ui.setPanel(rig.make.panelView({{ progress: {{ total: 2, done: 0, items: {items} }}, review: {{ missingRequired: 1, items: [{{ key: 'f0:0', label: 'First Name ✱' }}] }} }}))")
        row = page.locator('[data-testid="progress-item"]').first
        env.report.check((await row.inner_text()).strip().replace("\n", " ") == "First Name *", f"labels: the row reads {(await row.inner_text())!r}")
        env.report.check(await row.get_attribute("aria-label") == "Go to First Name. To do, required", f"labels: the row's name is {await row.get_attribute('aria-label')!r}")
        review = page.locator('[data-testid="review-item"]').first
        env.report.check(await review.get_attribute("aria-label") == "Go to First Name", f"labels: the review row's name is {await review.get_attribute('aria-label')!r}")
        # a label that is nothing but marks is empty, and an asterisk inside the words stays
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: '*', value: 'x' })")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Salary *k', value: '1' })")
        env.report.check((await page.evaluate("document.querySelector('[data-meridian-assistant]') !== null")), "labels: host")


# ---- SEC-01 (UI half): "Fill verified" asks first -------------------------------------------------------------------------------------------------------------------------------------
async def test_fill_confirmation(env: Env) -> None:
    async with env.session() as page:
        env.report.check((await page.evaluate("rig.ui.capabilities"))["fillPreview"] is True, "fill: the UI does not declare the confirmation it renders")
        await show(page, "panel-fill-preview")
        env.report.check(await text_of(page, "fill-preview-title") == "Fill 5 answers?", f"fill: title {await text_of(page, 'fill-preview-title')!r}")
        items = await page.locator('[data-testid="fill-preview-item"]').all_inner_texts()
        env.report.check(len(items) == 5 and "Email" in items[2] and "alex.newport@example.com" in items[2], f"fill: the list is {items}")
        env.report.check(await count(page, "panel-fill-verified") == 0, "fill: the button that opened the question is still on screen")
        env.report.check(await text_of(page, "fill-confirm") == "Fill now", f"fill: confirm label {await text_of(page, 'fill-confirm')!r}")
        await page.evaluate("rig.resetActions()")
        await click(page, "fill-confirm")
        env.report.check(await actions(page) == [{"type": "fill-confirm"}], f"fill: confirm emitted {await actions(page)}")
        await page.evaluate("rig.resetActions()")
        await click(page, "fill-cancel")
        env.report.check(await actions(page) == [{"type": "fill-cancel"}], f"fill: cancel emitted {await actions(page)}")
        # more answers than the list shows: the rest is a count
        await show(page, "panel-fill-preview-more")
        env.report.check(await text_of(page, "fill-preview-more") == "and 12 more", f"fill: {await text_of(page, 'fill-preview-more')!r}")
        await show(page, "panel-fill-preview-one")
        env.report.check(await text_of(page, "fill-preview-title") == "Fill 1 answer?", "fill: singular")
        # hostile text is text
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 1, items: [{ key: 'a', label: '<img src=x onerror=window.__pwned=1>', value: '<script>window.__pwned=2</script>' }] } }))")
        env.report.check(await page.evaluate("rig.pwned()") is None, "fill: page script ran through the confirmation")
        # no list, no question: a count with nothing to show falls back to the button
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 3, items: [] } }))")
        env.report.check(await is_visible(page, "panel-fill-verified") and not await is_visible(page, "panel-fill-preview"), "fill: an empty list stranded the person without the button")
        # the keyboard goes to the answer when the question opens while it was in the panel ...
        await show(page, "panel-ready-20")
        await page.locator('[data-testid="panel-fill-verified"]').focus()
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 2, items: [{ key: 'a', label: 'First name', value: 'Alex' }, { key: 'b', label: 'Last name', value: 'Newport' }] } }))")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "fill-confirm", f"fill: the keyboard did not go to the confirmation ({(await page.evaluate('rig.focusedInfo()'))['testid']})")
        # ... and comes back to Fill verified when the person says no
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({}))")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-fill-verified", f"fill: after Cancel the keyboard went to {(await page.evaluate('rig.focusedInfo()'))['testid']}")
        # ... and from the Cancel button as well
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 2, items: [{ key: 'a', label: 'First name', value: 'Alex' }, { key: 'b', label: 'Last name', value: 'Newport' }] } }))")
        await page.locator('[data-testid="fill-cancel"]').focus()
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({}))")
        env.report.check((await page.evaluate("rig.focusedInfo()"))["testid"] == "panel-fill-verified", f"fill: after Cancel the keyboard went to {(await page.evaluate('rig.focusedInfo()'))['testid']}")
        # ... but a question that opens while the person is in the form does not take the keyboard from it
        await page.focus("#fx-top")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillPreview: { count: 1, items: [{ key: 'a', label: 'First name', value: 'Alex' }] } }))")
        env.report.check(await page.evaluate("document.activeElement && document.activeElement.id") == "fx-top", "fill: the question took the keyboard from the form field")
        # the list scrolls inside a limit; es
        await show(page, "panel-fill-preview", "es")
        env.report.check(await text_of(page, "fill-preview-title") == "¿Rellenar 5 respuestas?" and await text_of(page, "fill-confirm") == "Rellenar ahora", "fill (es)")
    async with env.session(FRAME) as page:
        # a short window: the question opens below the fold and the answer that has the keyboard is brought into view
        await show(page, "panel-ready-20")
        await page.locator('[data-testid="panel-fill-verified"]').focus()
        items = "[" + ", ".join("{ key: 'k%d', label: 'Field %d', value: 'Value %d' }" % (i, i, i) for i in range(5)) + "]"
        await page.evaluate(f"rig.ui.setPanel(rig.make.panelView({{ fillPreview: {{ count: 5, items: {items} }} }}))")
        await page.wait_for_timeout(150)
        body = await page.locator('[data-testid="panel-body"]').bounding_box()
        confirm = await page.locator('[data-testid="fill-confirm"]').bounding_box()
        env.report.check(
            body["y"] - 1 <= confirm["y"] and confirm["y"] + confirm["height"] <= body["y"] + body["height"] + 1,
            f"fill: in a short window the focused confirmation is out of sight {confirm} vs the body {body}",
        )
        for lang in ("en", "es"):
            await shown(page, "panel-fill-preview-more", lang)
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", ["panel-fill-preview-more", lang])
            env.report.check(not audit["overflow"], f"fill: overflow in a 300 px frame ({lang}) {audit['overflow']}")


# ---- UXR-06 (UI half): the keyboard never falls to the page when its control is switched off ------------------------------------------------------------------------------
async def test_keyboard_stays_in_the_panel_when_a_control_turns_off(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-ready-20")
        await page.locator('[data-testid="panel-fill-verified"]').focus()
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillableCount: 0, batchUndo: { count: 4 } }))")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["inHost"] and info["testid"] == "panel-undo-all", f"focus: Fill verified switched off and the keyboard went to {info}")
        await show(page, "panel-ready-20")
        await page.locator('[data-testid="panel-fill-verified"]').focus()
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ fillableCount: 0 }))")
        info = await page.evaluate("rig.focusedInfo()")
        env.report.check(info["inHost"], f"focus: Fill verified switched off and the keyboard fell out of the panel {info}")


# ---- UXR-08 (UI half): the duplicate banner says what became of the application ------------------------------------------------------------------------------------------------
async def test_duplicate_banner_by_status(env: Env) -> None:
    async with env.session() as page:
        wanted = {
            "panel-duplicate-rejected": "and it was rejected. I won't insert anything until you continue.",
            "panel-duplicate-withdrawn": "then withdrew it. I won't insert anything until you continue.",
            "panel-duplicate-ghosted": "Meridian has it recorded as applied, with no answer. I won't insert anything until you continue.",
            "panel-duplicate-interview": "You applied on",
            "panel-duplicate-applied": "You applied on",
        }
        for scene, needle in wanted.items():
            await show(page, scene)
            hint = await text_of(page, "dup-hint")
            env.report.check(needle in hint, f"duplicate: {scene} says {hint!r}")
            env.report.check(await text_of(page, "dup-title") == "You already applied to this job", f"duplicate: {scene} title")
        await show(page, "panel-duplicate-nodate")
        env.report.check("Meridian has it recorded as applied." in await text_of(page, "dup-hint"), "duplicate: no date, no status")
        await show(page, "panel-duplicate-rejected", "es")
        env.report.check("fue rechazada. No inserto nada hasta que continúes." in await text_of(page, "dup-hint"), f"duplicate (es): {await text_of(page, 'dup-hint')!r}")
        await page.evaluate(SAID)
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: null }))")
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: { state: 'applied', status: 'withdrawn' } }))")
        env.report.check(any("Meridian la tiene registrada como retirada" in line for line in await page.evaluate("window.__said")), f"duplicate (es): the announcement says {await page.evaluate('window.__said')}")
        # an unknown status is the plain sentence
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ duplicate: { state: 'applied', status: 'brand_new' } }))")
        env.report.check("registrada como enviada. No inserto" in await text_of(page, "dup-hint"), "duplicate: an unknown status")
    async with env.session(FRAME) as page:
        for scene in ("panel-duplicate-rejected", "panel-duplicate-ghosted"):
            await show(page, scene, "es")
            audit = await page.evaluate("([id, lang]) => rig.audit(id, lang)", [scene, "es"])
            env.report.check(not audit["overflow"], f"duplicate: overflow in a 300 px frame ({scene}) {audit['overflow']}")


async def test_duplicate_blocks_the_chip(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-duplicate-blocked")
        env.report.check(await count(page, "chip-accept") == 0 and await count(page, "chip-replace") == 0, "duplicate chip: an Insert is offered although nothing will be inserted")
        note = await text_of(page, "chip-duplicate")
        env.report.check("already applied" in note and "Continue anyway" in note, f"duplicate chip: the reason is {note!r}")
        await page.wait_for_timeout(450)
        said = await text_of(page, "chip-announce")
        env.report.check("Nothing will be inserted" in said and "Press Tab to insert" not in said, f"duplicate chip: announced {said!r}")
        await show(page, "chip-duplicate-blocked", "es")
        env.report.check("Ya enviaste tu candidatura" in await text_of(page, "chip-duplicate"), "duplicate chip (es)")
        # a view that says "insert with Tab" and "blocked" at once is read as blocked: no Insert, no Replace
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { key: 'f0:44', insert: 'tab', duplicateBlocksInsert: true }))")
        env.report.check(await count(page, "chip-accept") == 0 and await count(page, "chip-duplicate") == 1, "duplicate chip: Insert offered for a blocked posting")
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-top'), { key: 'f0:45', insert: 'tab', conflictsWithCurrent: true, duplicateBlocksInsert: true }))")
        env.report.check(await count(page, "chip-replace") == 0, "duplicate chip: Replace offered for a blocked posting")


# ---- UXR-13 (CTRL3/UX-13): the menu's switch gives way to one calm line ----------------------------------------------------------------------------------------------------
async def test_always_on_waits_for_the_browser(env: Env) -> None:
    async with env.session() as page:
        await shown(page, "panel-more-permission")
        env.report.check(await is_visible(page, "panel-always-on-permission") and await count(page, "panel-always-on") == 0, "permission: the switch stayed beside the calm line")
        env.report.check(await text_of(page, "panel-always-on-permission") == "Allow access to this site in the window that just opened.", f"permission: {await text_of(page, 'panel-always-on-permission')!r}")
        env.report.check(await is_visible(page, "panel-hide-hint"), "permission: the other switch went too")
        # granted: the switch is back, on
        await page.evaluate("rig.ui.setPanel(rig.make.panelView({ site: { host: 'boards.greenhouse.io', alwaysOn: true, hintHidden: false, alwaysOnNeedsPermission: true } }))")
        env.report.check(await is_visible(page, "panel-always-on") and await count(page, "panel-always-on-permission") == 0, "permission: a granted site still shows the line")
        await shown(page, "panel-more-permission", "es")
        env.report.check(await text_of(page, "panel-always-on-permission") == "Permite el acceso a este sitio en la ventana que se acaba de abrir.", "permission (es)")
    async with env.session(FRAME) as page:
        await shown(page, "panel-more-permission", "es")
        audit = await page.evaluate("rig.audit('panel-more-permission', 'es')")
        env.report.check(not audit["overflow"], f"permission: overflow in a 300 px frame {audit['overflow']}")


# ---- UXR-10 (UI half): the logic can remember the first-use card as seen -----------------------------------------------------------------------------------------------------------
async def test_dismiss_note(env: Env) -> None:
    async with env.session() as page:
        await show(page, "panel-first-use")
        env.report.check(await is_visible(page, "panel-first-use"), "dismiss: precondition")
        await page.evaluate("window.__bgLog.length = 0")
        await page.evaluate("rig.ui.dismissNote('first_use')")
        env.report.check(not await is_visible(page, "panel-first-use"), "dismiss: the card stayed")
        await page.wait_for_timeout(200)
        sent = [m for m in await page.evaluate("window.__bgLog") if m.get("t") == "ui:set"]
        env.report.check(bool(sent) and "first_use" in sent[-1]["value"]["dismissed"], f"dismiss: the worker was not told {sent}")
        await show(page, "panel-first-use")
        await page.wait_for_timeout(250)
        env.report.check(not await is_visible(page, "panel-first-use"), "dismiss: the card came back")
    async with env.session() as page:
        # a frame without a panel keeps the same entry
        await page.evaluate("rig.ui.unmount(); rig.ui.mount({ panel: false })")
        await page.evaluate("window.__bgLog.length = 0")
        await page.evaluate("rig.ui.dismissNote('first_use')")
        await page.wait_for_timeout(200)
        sent = [m for m in await page.evaluate("window.__bgLog") if m.get("t") == "ui:set"]
        env.report.check(bool(sent) and "first_use" in sent[-1]["value"]["dismissed"], f"dismiss (no panel): the worker was not told {sent}")


# ---- SEC-13: nothing in either dictionary promises more than it can keep -------------------------------------------------------------------------------------------------------------------
async def test_no_overclaims_anywhere(env: Env) -> None:
    async with env.session() as page:
        strings = await page.evaluate("rig.allStrings()")
        import re

        absolute = {
            "en": [r"\bstays? on this mac\b(?!.{0,80}\bexcept\b)", r"\bnever leaves?\b", r"\bnothing (ever )?leaves\b", r"\bnever (reaches|touches) the internet\b", r"\beverything stays\b", r"\bnever the internet\b"],
            "es": [r"\bse quedan? en este mac\b(?!.{0,120}\bsalvo\b)", r"\bnunca sale\b", r"\bnada sale\b", r"\btodo se queda\b", r"\bnunca con internet\b"],
        }
        for lang, patterns in absolute.items():
            for key, value in strings[lang].items():
                for pattern in patterns:
                    env.report.check(not re.search(pattern, value, flags=re.I), f"overclaim ({lang}): {key} promises more than it keeps: {value!r}")
        env.report.check(strings["en"]["save.local"] == "Saved in Meridian, on this Mac.", f"overclaim: save.local says {strings['en']['save.local']!r}")
        # an error toast cannot promise the page is untouched: it also follows a bulk fill that stopped part way
        for lang, banned in (("en", "nothing was changed"), ("es", "no se ha cambiado nada")):
            env.report.check(banned not in strings[lang]["toast.error"].lower(), f"overclaim ({lang}): the error toast says the page is untouched: {strings[lang]['toast.error']!r}")


async def test_insertions_announced_after_the_focus_moved_on(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate(SAID)
        # a file attached or a combobox picked finishes after the chip moved to another field: the logic says it in its own words
        await page.evaluate("rig.ui.showChip(rig.make.chipView(rig.anchorOf('fx-mid'), { key: 'f0:77', fieldLabel: 'Phone', value: '+44 7700 900123' }))")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Resume / CV', value: 'Alex-Newport-CV.pdf' })")
        said = await page.evaluate("window.__said")
        env.report.check(said == ["Inserted Resume / CV: Alex-Newport-CV.pdf"], f"late: {said}")
        # the flash of that insertion (the field is not the chip's) says nothing of its own
        await page.evaluate("rig.ui.flash(rig.anchorOf('fx-top'), 'verified')")
        await page.wait_for_timeout(450)
        env.report.check(len(await page.evaluate("window.__said")) == 1, f"late: the flash of a field the chip left spoke {await page.evaluate('window.__said')}")
        # nothing without a label; the region draws nothing and never takes focus
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: '   ' })")
        env.report.check(len(await page.evaluate("window.__said")) == 1, "late: an announcement with no label was said")
        # unmounted: a late announcement is a no-op, not an error
        await page.evaluate("rig.ui.unmount()")
        await page.evaluate("rig.ui.announce({ kind: 'inserted', label: 'Phone', value: '1' })")
        await page.evaluate("rig.ui.mount({ panel: false })")


def wave4_tests() -> list:
    return [
        test_options_are_never_covered,
        test_chip_folds_to_one_line_when_no_side_is_clean,
        test_a_label_around_the_field_is_not_an_option,
        test_typing_pill_ends_with_the_insertion,
        test_flash_follows_a_scrolling_pane,
        test_roving_lists,
        test_panel_escape,
        test_review_card_and_the_cards_area,
        test_help_full_link,
        test_labels_without_required_marks,
        test_fill_confirmation,
        test_keyboard_stays_in_the_panel_when_a_control_turns_off,
        test_duplicate_banner_by_status,
        test_duplicate_blocks_the_chip,
        test_always_on_waits_for_the_browser,
        test_dismiss_note,
        test_no_overclaims_anywhere,
        test_insertions_announced_after_the_focus_moved_on,
    ]
