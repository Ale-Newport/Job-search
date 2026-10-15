// Open shadow roots of the page (web-component ATS widgets, design-system inputs).
//
// Why a registry: three things never reach a listener on `document` / `window` from inside a shadow tree, so a form that
// lives entirely in an open shadow root would be half blind:
//   - `focusin` / `focusout` that move between two nodes of the SAME shadow tree: the dispatch algorithm skips every
//     ancestor where the event target and its relatedTarget retarget to the same node (the shadow host and above);
//   - `change` and `submit` are not composed at all;
//   - a MutationObserver on the document does not see changes below a shadow root.
// Consumers `bind` once; the registry runs their binder for every known root and every future one, and runs the returned
// disposer when the root leaves the page (or on `stop`), so nothing outlives a deactivation or a removed widget.

import { UI_HOST_ATTRIBUTE } from "./ui-api";

export type RootBinder = (root: ShadowRoot) => () => void;

/** A page made of hundreds of web components must not get hundreds of observers: the first ones in document order are enough. */
export const MAX_ROOTS = 300;

interface Entry { disposers: Map<RootBinder, () => void> }

export function isOpenShadowRoot(node: unknown): node is ShadowRoot {
  return !!node && typeof node === "object" && (node as Node).nodeType === 11 && !!(node as ShadowRoot).host;
}

/** Is the element (or one of the hosts above it) the assistant's own UI host? Cheap: only walks real ancestors. */
function ownedByUi(host: Element): boolean {
  let cur: Node | null = host;
  for (let guard = 0; cur && guard < 200; guard++) {
    if (cur.nodeType === 1 && (cur as Element).hasAttribute(UI_HOST_ATTRIBUTE)) return true;
    cur = cur.parentNode ?? (cur as ShadowRoot).host ?? null;
  }
  return false;
}

export class RootRegistry {
  private entries = new Map<ShadowRoot, Entry>();
  private binders: RootBinder[] = [];
  private stopped = false;

  get size(): number {
    return this.entries.size;
  }

  has(root: ShadowRoot): boolean {
    return this.entries.has(root);
  }

  list(): ShadowRoot[] {
    return [...this.entries.keys()];
  }

  /** Runs `binder` for every current and future root. The returned function unbinds it everywhere. */
  bind(binder: RootBinder): () => void {
    this.binders.push(binder);
    for (const [root, entry] of this.entries) this.attach(root, entry, binder);
    return () => {
      this.binders = this.binders.filter((b) => b !== binder);
      for (const entry of this.entries.values()) this.detach(entry, binder);
    };
  }

  /** A root seen on an event path or while scanning. Closed roots never show up; the assistant's own UI is refused. */
  add(root: ShadowRoot | null | undefined): void {
    if (!root || this.stopped || this.entries.has(root) || !isOpenShadowRoot(root) || this.entries.size >= MAX_ROOTS) return;
    if (ownedByUi(root.host)) return;
    const entry: Entry = { disposers: new Map() };
    this.entries.set(root, entry);
    for (const binder of this.binders) this.attach(root, entry, binder);
  }

  /** Every shadow root on the path of an event (outermost listeners get the whole composed path). */
  addFromPath(event: Event): void {
    if (this.stopped) return;
    let path: EventTarget[] = [];
    try {
      path = event.composedPath?.() ?? [];
    } catch {
      path = [];
    }
    for (const node of path) if (isOpenShadowRoot(node)) this.add(node);
  }

  /** The scan found exactly these roots: forget the ones that left the page. */
  sync(found: ShadowRoot[]): void {
    if (this.stopped) return;
    const keep = new Set(found);
    for (const root of found) this.add(root);
    for (const root of [...this.entries.keys()]) if (!keep.has(root) && !root.host.isConnected) this.remove(root);
  }

  remove(root: ShadowRoot): void {
    const entry = this.entries.get(root);
    if (!entry) return;
    this.entries.delete(root);
    for (const binder of [...entry.disposers.keys()]) this.detach(entry, binder);
  }

  stop(): void {
    this.stopped = true;
    for (const root of [...this.entries.keys()]) this.remove(root);
    this.binders = [];
  }

  private attach(root: ShadowRoot, entry: Entry, binder: RootBinder): void {
    if (entry.disposers.has(binder)) return;
    try {
      entry.disposers.set(binder, binder(root));
    } catch {
      /* a failing binder must never stop the others */
    }
  }

  private detach(entry: Entry, binder: RootBinder): void {
    const dispose = entry.disposers.get(binder);
    entry.disposers.delete(binder);
    try {
      dispose?.();
    } catch {
      /* nothing to clean */
    }
  }
}
