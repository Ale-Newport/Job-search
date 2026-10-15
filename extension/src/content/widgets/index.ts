// Per-widget adapters for the fill engine. fill.ts knows how to drive ANY ARIA combobox from the outside (open, type, click the
// option, read back what the control shows); an adapter only teaches it what a specific widget library shows as its selection
// when that is not readable from the combobox element itself. Adapters never write to the page and never import fill.ts.

import { reactSelect } from "./react-select";

export interface WidgetAdapter {
  readonly name: string;
  /** Cheap structural test that this element is the combobox of that library. No side effects. */
  matches(el: HTMLElement): boolean;
  /** The text the widget currently displays as its selection ("" = nothing selected); null = this adapter cannot tell. */
  selection(el: HTMLElement): string | null;
}

const ADAPTERS: readonly WidgetAdapter[] = [reactSelect];

export function adapterFor(el: HTMLElement): WidgetAdapter | null {
  for (const adapter of ADAPTERS) {
    try {
      if (adapter.matches(el)) return adapter;
    } catch {
      /* a broken adapter must never break filling */
    }
  }
  return null;
}
