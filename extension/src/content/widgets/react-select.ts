// react-select (v5) renders the same structure whatever its class names are (classNamePrefix="select" on Greenhouse job boards, hashed
// emotion classes otherwise):
//   <div container><span live-region/><div control><div value-container>
//        [single-value | multi-value chips | placeholder]  <div data-value="typed text"><input role="combobox" aria-autocomplete="list"></div>
//   </div><div indicators/></div><input type="hidden" name value="code"></div>
// The displayed selection is therefore whatever sits next to the input's container inside the value container, minus the placeholder.

import type { WidgetAdapter } from "./index";

function inputContainer(el: HTMLElement): HTMLElement | null {
  const parent = el.parentElement;
  return parent && parent.hasAttribute("data-value") ? parent : null;
}

export const reactSelect: WidgetAdapter = {
  name: "react-select",
  matches(el) {
    return el instanceof HTMLInputElement && el.getAttribute("role") === "combobox" && el.getAttribute("aria-autocomplete") === "list"
      && inputContainer(el) !== null && inputContainer(el)?.parentElement != null;
  },
  selection(el) {
    const box = inputContainer(el);
    const values = box?.parentElement;
    if (!box || !values) return null;
    const shown: string[] = [];
    for (const child of values.children) {
      if (child === box) continue;
      // the placeholder is referenced by the input while nothing is selected
      if (child.id && el.getAttribute("aria-describedby")?.split(/\s+/).includes(child.id)) continue;
      if (child.id.endsWith("-placeholder")) continue;
      const text = (child.textContent ?? "").trim();
      if (text) shown.push(text);
    }
    return shown.join(", ");
  },
};
