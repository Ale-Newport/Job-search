"""Behaviours added by the security review of the content layer (SEC-01, HIGH-1): the visual layer puts itself back on top of the page before the
logic writes anything, and says so when it cannot."""

from __future__ import annotations

from behaviours import Env, show

# a layer of the page in the top layer, opened AFTER the assistant's UI, that takes no click and lies over the chip or the panel
VEIL = """(sel) => {
  const part = [...rig.ui.root.querySelectorAll(sel)].find((n) => n.getBoundingClientRect().width > 1); const r = part.getBoundingClientRect();
  const v = document.createElement('div'); v.id = 'page-veil'; v.setAttribute('popover', 'manual');
  v.style.cssText = `position:fixed;inset:auto;margin:0;border:0;padding:0;background:#fff;pointer-events:none;left:${r.left - 3}px;top:${r.top - 3}px;width:${r.width + 6}px;height:${r.height + 6}px`;
  document.body.append(v); v.showPopover();
}"""

# what is first at the middle of every drawn part of the UI when pointer-events are forced on, as a person's eye would see it
TOP = """() => {
  const host = document.querySelector('[data-meridian-assistant]'); const root = rig.ui.root;
  const sheet = new CSSStyleSheet(); sheet.replaceSync('@layer probe { *, *::before, *::after { pointer-events: auto !important; } }');
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet]; root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet];
  try {
    return [...root.querySelectorAll('.chip, .panel')].filter((n) => n.getBoundingClientRect().width > 1).map((n) => {
      const r = n.getBoundingClientRect(); const top = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2)[0];
      return [n.classList.contains('panel') ? 'panel' : 'chip', top === host ? 'ui' : (top ? top.id || top.tagName : 'none')];
    });
  } finally { document.adoptedStyleSheets = document.adoptedStyleSheets.filter((s) => s !== sheet); root.adoptedStyleSheets = root.adoptedStyleSheets.filter((s) => s !== sheet); }
}"""


async def test_the_ui_takes_the_top_back_from_a_layer_the_page_opened_after_it(env: Env) -> None:
    for scene, part in (("chip-verified-email", ".chip"), ("panel-ready-6", ".panel")):
        async with env.session() as page:
            await show(page, scene)
            before = await page.evaluate(TOP)
            env.report.check(any(t[0] == part[1:] for t in before) and all(t[1] == "ui" for t in before), f"on top ({scene}): nothing covers the UI to begin with {before}")
            env.report.check(await page.evaluate("rig.ui.ensureOnTop()") is True, f"on top ({scene}): an uncovered UI is on top")
            await page.evaluate(VEIL, part)
            covered = await page.evaluate(TOP)
            env.report.check(any(t[1] == "page-veil" for t in covered), f"on top ({scene}): the premise, the page's own layer lies over the UI {covered}")
            env.report.check(await page.evaluate("rig.ui.ensureOnTop()") is True, f"on top ({scene}): the UI could not take the top back")
            after = await page.evaluate(TOP)
            env.report.check(bool(after) and all(t[1] == "ui" for t in after), f"on top ({scene}): the UI is still under the page's layer {after}")
            # nothing the page does to the host's inline style keeps the UI away either
            await page.evaluate("() => { const h = document.querySelector('[data-meridian-assistant]'); h.style.cssText = 'display:none'; }")
            env.report.check(await page.evaluate("rig.ui.ensureOnTop()") is True, f"on top ({scene}): a host the page hid with its own inline style did not come back")
            again = await page.evaluate(TOP)
            env.report.check(bool(again) and all(t[1] == "ui" for t in again), f"on top ({scene}): the UI stayed hidden after the page rewrote the host's style {again}")


async def test_the_ui_says_when_it_cannot_be_put_back_on_top(env: Env) -> None:
    async with env.session() as page:
        await show(page, "chip-verified-email")
        await page.evaluate(VEIL, ".chip")
        # a page that can keep the host out of the top layer (here: its own methods; a content script's own world would not see them)
        await page.evaluate("() => { const h = document.querySelector('[data-meridian-assistant]'); h.showPopover = () => { throw new Error('no'); }; h.hidePopover = () => {}; }")
        env.report.check(await page.evaluate("rig.ui.ensureOnTop()") is False, "cannot be shown: it said it could be")
        # nothing mounted: nothing can be shown
        await page.evaluate("rig.ui.unmount()")
        env.report.check(await page.evaluate("rig.ui.ensureOnTop()") is False, "cannot be shown: an unmounted UI said it could be")


def wave6_tests() -> list:
    return [
        test_the_ui_takes_the_top_back_from_a_layer_the_page_opened_after_it,
        test_the_ui_says_when_it_cannot_be_put_back_on_top,
    ]
