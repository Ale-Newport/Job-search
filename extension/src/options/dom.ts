// Tiny DOM helpers for the options page: element builder and a binding list so that a state change re-renders text,
// attributes and visibility in place (focus is never lost because stable elements are updated, not rebuilt).

type Child = Node | string | null | undefined | false;
type Attrs = Record<string, string | boolean | number | null | undefined>;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (name === "class") node.className = String(value);
    else if (value === true) node.setAttribute(name, "");
    else node.setAttribute(name, String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export class Bindings {
  private readonly updates: (() => void)[] = [];

  /** Runs `update` now and on every `refresh()`. Returns the node for chaining. */
  bind<T extends Node>(node: T, update: (node: T) => void): T {
    this.updates.push(() => update(node));
    update(node);
    return node;
  }

  /** Text that follows the state. */
  text(tag: keyof HTMLElementTagNameMap, attrs: Attrs, value: () => string): HTMLElement {
    return this.bind(h(tag, attrs) as HTMLElement, (node) => {
      const next = value();
      if (node.textContent !== next) node.textContent = next;
    });
  }

  refresh(): void {
    for (const update of this.updates) update();
  }
}

/** Rebuilds `container` only when `key` changed, so a refresh does not steal focus from an unchanged list. */
export function keyedList(container: HTMLElement, key: () => string, build: () => Node[]): () => void {
  let last: string | null = null;
  return () => {
    const next = key();
    if (next === last) return;
    last = next;
    container.replaceChildren(...build());
  };
}
