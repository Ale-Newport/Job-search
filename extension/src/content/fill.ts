// Fill engine: the ONLY code that writes to the page. Every operation of design 7.3 reads the value back, reverts on a
// mismatch and reports a failure instead of claiming success. Values are plain text, never HTML. It never throws into
// the page, never submits, never types an empty search text and only attaches the user's own approved CV.

import type { DocumentContent, FillOperation, SuggestionValue } from "../shared/protocol";
import type { AssistField, GroupMember } from "./state";
import { isTextKind } from "./state";
import { base64ToBytes, deepActiveElement, inUiHost, isRendered, normalizeText, sha256Hex, sleep, squash, tokens } from "./util";
import { adapterFor } from "./widgets";

export type FillFailure =
  | "unsupported" | "disconnected" | "disabled" | "no_option" | "ambiguous" | "verify" | "too_long" | "combobox"
  | "file" | "accept" | "document" | "exception" | "orphaned" | "no_value" | "hidden";

export interface ValueReading {
  empty: boolean;
  text: string;
  labels: string[];
  checked: boolean;
}

export interface FillContext {
  fetchDocument(id: string): Promise<DocumentContent | null>;
  isOrphaned(): boolean;
}

export interface FillOutcome {
  ok: boolean;
  failure?: FillFailure;
  /** the page value actually changed */
  changed: boolean;
  /** plain text that was inserted (display form) and the choice labels it selected */
  text: string;
  labels: string[];
  /** restores the previous state; null when the operation cannot be undone safely */
  undo: (() => boolean) | null;
}

export type Relation = "empty" | "equal" | "conflict";

const MAX_DOCUMENT_BYTES = 10 * 1024 * 1024;
const PLACEHOLDER_TEXT = /^(?:|-+|—|select\b.*|choose\b.*|please select.*|pick\b.*|selecciona.*|elige.*|elegir.*|seleccionar.*|--.*--)$/i;
/** Values of a "no choice" option: empty, 0, -1, none ... and Angular's `[ngValue]` ids ("0: null", "0: undefined"). */
const PLACEHOLDER_VALUE = /^(?:|0|-1|none|null|undefined|default|select|choose|\d+:\s*(?:null|undefined)?)$/i;
const OPTION_SELECTOR = '[role="option"],[role="treeitem"],[data-dropdown-selection],.select2-results__option';
/** Zero-width characters widgets use as "empty" content (MUI Select renders U+200B when nothing is selected). */
const INVISIBLE = /[\u200b-\u200d\u2060\ufeff]/g;
/**
 * What a custom combobox shows while nothing is selected ("Select One", "Select an option", "Select level", "Choose...", "-- Select --").
 * Placeholders are sentence-case, so the words after "Select" must be an article plus words, or start in lower case: a proper name
 * such as "Select Group" is a value. (Two expressions: the `i` flag would make \p{Ll} match capitals too.)
 */
const PLACEHOLDER_HEAD = /^[-\u2013\u2014\s]*(?:please\s+)?(?:select|choose|pick|selecciona(?:r)?|seleccione|elige|elegir)(?![\p{L}\p{N}])/iu;
const PLACEHOLDER_TAIL = new RegExp(
  "^(?:\\s+(?:[Oo]ne|[Oo]ption|[Uu]no|(?:[Aa]n?|[Yy]our|[Tt]he|[Aa]ny|[Uu]na?|[Tt]u|[Ee]l|[Ll]a)\\s+\\p{L}[\\p{L}-]*(?:\\s+\\p{L}[\\p{L}-]*){0,2}" +
  "|\\p{Ll}[\\p{L}-]*(?:\\s+\\p{L}[\\p{L}-]*){0,2}))?\\s*(?:\\.{2,3}|\u2026)?[-\u2013\u2014\\s]*$", "u",
);

/**
 * Operations whose result may only be known after the page's framework flushed its updates (React 18 and Vue batch state to the end of
 * the task, a controlled/ARIA toggle re-renders from state), plus the ones that wait for menus and files. The keyboard layer keeps focus
 * where it is and advances when the returned promise settles; the fill itself still runs inside the key event.
 */
export function isAsyncOperation(op: FillOperation): boolean {
  return op === "OPEN_AND_SELECT" || op === "ATTACH_FILE" || op === "CHECK_RADIO" || op === "SET_CHECKBOX" || op === "SET_CHECKBOXES" || op === "SELECT_OPTION";
}

// ---- reading ---------------------------------------------------------------------------------------------------------------

/** Is a radio/checkbox-like control currently on? `fallback` is the scan-time state for widgets with no readable state. */
export function isOn(el: HTMLElement, fallback = false): boolean {
  if (el instanceof HTMLInputElement && (el.type === "checkbox" || el.type === "radio")) return el.checked;
  const checked = el.getAttribute("aria-checked");
  if (checked !== null) return checked === "true";
  const pressed = el.getAttribute("aria-pressed");
  if (pressed !== null) return pressed === "true";
  return fallback;
}

function isPlaceholderOption(option: HTMLOptionElement, index: number): boolean {
  if (option.value === "") return true;
  const text = (option.label || option.text || "").trim();
  return index === 0 && !option.defaultSelected && PLACEHOLDER_VALUE.test(option.value) && PLACEHOLDER_TEXT.test(text);
}

/** What a custom combobox currently displays as its selection (mirrors the observer's `selected_text`), without placeholders. */
export function comboSelection(el: HTMLElement): string {
  const shown = rawComboSelection(el).replace(INVISIBLE, "").replace(/\s+/g, " ").trim();
  // a trigger that marks ITSELF `data-placeholder` shows its placeholder: Radix Select sets the empty attribute while nothing is chosen
  // and removes it afterwards. A text-valued `data-placeholder` is a setting (Chosen, Select2 ...), not a state: it only counts when
  // the widget shows exactly that text.
  const mark = el.tagName === "INPUT" ? null : el.getAttribute("data-placeholder");
  const markedEmpty = mark !== null && (mark.trim() === "" || squash(mark) === squash(shown));
  if (shown !== "" && !markedEmpty && !isComboPlaceholder(shown) && !placeholderMarked(el, shown)) return shown;
  // a widget whose display cannot be read from the combobox itself: what we saw it show right after we filled it still counts
  const seen = SEEN_BY_TEXT.get(el);
  return seen && widgetText(el) === seen.text ? seen.label : "";
}

/** The widget itself says the text is its placeholder (Select2's .select2-selection__placeholder, data-placeholder, aria-placeholder, ...). */
function placeholderMarked(el: HTMLElement, shown: string): boolean {
  // descendants only: a modifier class on the combobox itself ("has-placeholder") says nothing about the text it currently shows
  return [...el.querySelectorAll('[class*="placeholder" i],[data-placeholder],[aria-placeholder]')].some((m) => squash(m.textContent ?? "") === squash(shown));
}

/** Combobox -> what its surroundings displayed when the fill verified it (only for widgets whose selection is not readable). */
const SEEN_BY_TEXT = new WeakMap<HTMLElement, { text: string; label: string }>();

/** "Select One", "Select an option", "Choose...", "-- Select --": what a combobox shows while nothing is chosen. */
export function isComboPlaceholder(text: string): boolean {
  const shown = text.replace(INVISIBLE, "").trim();
  const head = PLACEHOLDER_HEAD.exec(shown);
  return !!head && PLACEHOLDER_TAIL.test(shown.slice(head[0].length));
}

function rawComboSelection(el: HTMLElement): string {
  const aria = el.getAttribute("aria-valuetext");
  if (aria) return aria;
  const adapted = adapterFor(el)?.selection(el);
  if (adapted !== undefined && adapted !== null) return adapted;
  const control = el.closest(".select__control");
  const chips = control ? [...control.querySelectorAll(".select__multi-value__label")] : [];
  const single = control?.querySelector(".select__single-value") ?? (chips.length === 1 ? chips[0] : null);
  if (single?.textContent) return single.textContent;
  const backing = el.querySelector?.('input[type="hidden"][name]') as HTMLInputElement | null;
  if (backing?.value) return backing.value;
  if (el instanceof HTMLInputElement) {
    return el.readOnly || el.getAttribute("aria-expanded") !== "true" ? el.value : "";
  }
  return withoutDecoration(el, el.innerText ?? "");
}

/** Characters that are neither letters nor digits: a chevron, a check mark, a cross. */
const SYMBOLS_ONLY = /^[^\p{L}\p{N}]+$/u;

/**
 * The text a widget shows, minus the decoration drawn next to it: an `aria-hidden` node at either end whose text is an icon
 * (Radix Select draws its chevron as a character, Angular Material as a ligature) is not part of what is selected. A symbol is always
 * dropped; a word-like icon only when something else is still shown (an `aria-hidden` copy of the whole value must not make a filled
 * widget look empty).
 */
export function withoutDecoration(el: HTMLElement, text: string): string {
  let out = text.replace(INVISIBLE, "").trim();
  for (const icon of el.querySelectorAll('[aria-hidden="true"]')) {
    const mark = (icon.textContent ?? "").replace(INVISIBLE, "").trim();
    if (!mark) continue;
    let next = out;
    if (next.endsWith(mark)) next = next.slice(0, next.length - mark.length).trimEnd();
    else if (next.startsWith(mark)) next = next.slice(mark.length).trimStart();
    else continue;
    if (next !== "" || SYMBOLS_ONLY.test(mark)) out = next;
  }
  return out;
}

export function readField(field: AssistField): ValueReading {
  const el = field.el;
  try {
    switch (field.kind) {
      case "text":
      case "textarea": {
        const text = (el as HTMLInputElement).value ?? "";
        // a native date/time/number input holds no value while the person is half way through typing it: that is not "empty"
        const started = text === "" && el instanceof HTMLInputElement && !!el.validity?.badInput;
        return { empty: text.trim() === "" && !started, text, labels: [], checked: false };
      }
      case "editable": {
        const text = (el.innerText ?? el.textContent ?? "").trim();
        return { empty: text === "", text, labels: [], checked: false };
      }
      case "select": {
        const select = el as HTMLSelectElement;
        const picked = [...select.selectedOptions].filter((o) => !isPlaceholderOption(o, o.index));
        const labels = picked.map((o) => (o.label || o.text || "").trim());
        return { empty: picked.length === 0, text: labels.join(", "), labels, checked: false };
      }
      case "radio":
      case "checkboxes": {
        const on = field.members.filter((m) => isOn(m.el, m.checked));
        const labels = on.map((m) => m.label);
        return { empty: on.length === 0, text: labels.join(", "), labels, checked: on.length > 0 };
      }
      case "checkbox": {
        const on = isOn(el, !!field.element.checked);
        return { empty: !on, text: on ? "yes" : "", labels: on ? [field.label] : [], checked: on };
      }
      case "combobox": {
        const text = comboSelection(el);
        return { empty: text === "", text, labels: text ? [text] : [], checked: false };
      }
      case "file": {
        const files = [...((el as HTMLInputElement).files ?? [])].map((f) => f.name);
        return { empty: files.length === 0, text: files.join(", "), labels: files, checked: false };
      }
      default:
        return { empty: true, text: "", labels: [], checked: false };
    }
  } catch {
    return { empty: true, text: "", labels: [], checked: false };
  }
}

const TRUTHY = new Set(["yes", "true", "1", "checked", "on", "si", "sí"]);

export function desiredChecked(sv: SuggestionValue): boolean {
  if (typeof sv.checked === "boolean") return sv.checked;
  return TRUTHY.has(String(sv.text ?? sv.display ?? "").trim().toLowerCase());
}

function wantedLabels(sv: SuggestionValue): string[] {
  if (sv.labels?.length) return sv.labels.map(String);
  if (sv.option?.label) return [sv.option.label];
  if (sv.label) return [sv.label];
  return sv.display ? [sv.display] : [];
}

/** The text actually typed for TYPE_TEXT: national-only phone inputs lose the dial code, like the production runner. */
export function expectedText(field: AssistField, sv: SuggestionValue): string {
  const text = String(sv.text ?? sv.display ?? "");
  const e = field.element;
  const code = String(e.phone_country_code ?? "").replace(/\D/g, "");
  if (e.phone_national_only && code && text.trim().startsWith("+")) {
    const digits = text.replace(/\D/g, "");
    if (digits.startsWith(code)) return digits.slice(code.length);
  }
  return text;
}

function digitsOf(value: string): string {
  return value.replace(/\D/g, "");
}

/** Length of the ITU country calling code at the start of an E.164 digit string (0 when it cannot start one). */
function callingCodeLength(digits: string): number {
  const first = digits[0];
  const two = digits.slice(0, 2);
  switch (first) {
    case "1":
    case "7": return 1;
    case "2": return two === "20" || two === "27" ? 2 : 3;
    case "3": return ["30", "31", "32", "33", "34", "36", "39"].includes(two) ? 2 : 3;
    case "4": return two === "42" ? 3 : 2;
    case "5": return two === "50" || two === "59" ? 3 : 2;
    case "6": return ["60", "61", "62", "63", "64", "65", "66"].includes(two) ? 2 : 3;
    case "8": return ["81", "82", "84", "86"].includes(two) ? 2 : 3;
    case "9": return ["90", "91", "92", "93", "94", "95", "98"].includes(two) ? 2 : 3;
    default: return 0;
  }
}

/**
 * Same phone number, written differently by a widget: intl-tel-input and friends move the dial code into the flag and show the
 * national number (with or without the trunk 0), or the reverse. Everything but exactly the country calling code must be preserved.
 */
export function samePhone(expected: string, actual: string): boolean {
  const e = digitsOf(expected);
  const a = digitsOf(actual);
  if (e === "" || a === "") return false;
  if (e === a) return true;
  const strip = (digits: string) => digits.replace(/^0+/, "");
  const [longer, shorter] = e.length >= a.length ? [e, a] : [a, e];
  const national = strip(shorter);
  if (national.length < 6 || !longer.endsWith(national)) return false;
  return longer.length - national.length === callingCodeLength(longer);
}

const PHONE_WORDS = /phone|mobile|telephone|tel[eé]fono|m[oó]vil|celular|whatsapp|\btel\b/i;

/** Only phone fields may show a number without its dial code: any other digits that lose a prefix were cut by the page. */
function looksLikePhone(field: AssistField): boolean {
  const e = field.element;
  return e.type === "tel" || /^tel/.test(e.autocomplete) || !!e.phone_country_code || e.phone_national_only
    || PHONE_WORDS.test(`${field.label} ${e.name} ${e.id}`);
}

function textMatches(expected: string, actual: string, field: AssistField): boolean {
  if (expected === actual) return true;
  if (expected.trim() === actual.trim()) return true;
  const a = squash(expected);
  const b = squash(actual);
  if (a !== "" && a === b) return true;
  const type = field.element.type;
  if (type === "number" && expected.trim() !== "" && Number(expected) === Number(actual)) return true;
  if (/^[+\d\s().-]+$/.test(expected) && digitsOf(expected) !== "") {
    if (digitsOf(expected) === digitsOf(actual)) return true;
    if (/^[+\d\s().-]+$/.test(actual) && looksLikePhone(field) && samePhone(expected, actual)) return true;
  }
  return false;
}

function labelMatches(a: string, b: string): boolean {
  const x = normalizeText(a);
  const y = normalizeText(b);
  return x !== "" && x === y;
}

function sameSet(a: string[], b: string[]): boolean {
  const x = new Set(a.map(normalizeText));
  const y = new Set(b.map(normalizeText));
  if (x.size !== y.size) return false;
  for (const v of x) if (!y.has(v)) return false;
  return true;
}

function selectionMatches(selected: string, wanted: string): boolean {
  const a = tokens(selected);
  const b = tokens(wanted);
  if (!a.length || !b.length) return false;
  if (a.join(" ") === b.join(" ")) return true;
  const sa = new Set(a);
  const sb = new Set(b);
  return b.every((t) => sa.has(t)) || a.every((t) => sb.has(t));
}

/** Does the page currently hold what the suggestion would insert? */
export function matches(field: AssistField, sv: SuggestionValue, reading: ValueReading = readField(field)): boolean {
  switch (field.kind) {
    case "text":
    case "textarea":
    case "editable":
      return !reading.empty && textMatches(expectedText(field, sv), reading.text, field);
    case "select": {
      if (reading.empty) return false;
      const select = field.el as HTMLSelectElement;
      if (select.multiple) return sameSet(reading.labels, wantedLabels(sv));
      const chosen = select.selectedOptions[0];
      const label = sv.option?.label ?? sv.label ?? sv.display;
      return !!chosen && ((!!label && labelMatches(chosen.label || chosen.text, label)) || (!!sv.option?.value && chosen.value === sv.option.value));
    }
    case "radio": {
      const on = field.members.find((m) => isOn(m.el, m.checked));
      const wanted = wantedLabels(sv)[0];
      return !!on && ((!!wanted && labelMatches(on.label, wanted)) || (!!sv.option?.value && (on.value === sv.option.value)));
    }
    case "checkboxes":
      return !reading.empty && sameSet(reading.labels, wantedLabels(sv));
    case "checkbox":
      return reading.checked === desiredChecked(sv);
    case "combobox": {
      const wanted = wantedLabels(sv)[0];
      return !reading.empty && !!wanted && selectionMatches(reading.text, wanted);
    }
    case "file": {
      const name = sv.document?.filename;
      return !reading.empty && !!name && reading.labels.includes(name);
    }
    default:
      return false;
  }
}

/** empty = nothing there (Tab may insert); equal = already what we would insert; conflict = a different value is there. */
export function relationOf(field: AssistField, sv: SuggestionValue | null | undefined): Relation {
  if (!sv) return "conflict";
  const reading = readField(field);
  if (field.kind === "checkbox") {
    const want = desiredChecked(sv);
    if (reading.checked === want) return "equal";
    return reading.checked ? "conflict" : "empty";
  }
  if (reading.empty) return "empty";
  return matches(field, sv, reading) ? "equal" : "conflict";
}

// ---- low level writers -----------------------------------------------------------------------------------------------------

function nativeSetValue(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  // The prototype setter bypasses React/Vue value trackers, so the following input event is seen as a real change.
  if (setter) setter.call(el, value);
  else el.value = value;
}

function fireInputEvents(el: Element, data: string | null, inputType = "insertText"): void {
  let input: Event;
  try {
    input = new InputEvent("input", { bubbles: true, composed: true, inputType, data });
  } catch {
    input = new Event("input", { bubbles: true, composed: true });
  }
  el.dispatchEvent(input);
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

function setNativeText(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  nativeSetValue(el, value);
  fireInputEvents(el, value);
}

function clickElement(el: HTMLElement): void {
  el.click();
}

/** A full mouse gesture for widgets that open on mousedown/pointerdown (React Select, Headless UI, ...). */
function pointerClick(el: HTMLElement): void {
  const r = el.getBoundingClientRect();
  const init = { bubbles: true, cancelable: true, composed: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0 };
  const fire = (type: string, buttons: number) => {
    if (type.startsWith("pointer")) {
      try {
        el.dispatchEvent(new PointerEvent(type, { ...init, buttons, pointerId: 1, pointerType: "mouse", isPrimary: true }));
        return;
      } catch {
        /* no PointerEvent: mouse events below are enough */
      }
      return;
    }
    el.dispatchEvent(new MouseEvent(type, { ...init, buttons }));
  };
  fire("pointerdown", 1);
  fire("mousedown", 1);
  fire("pointerup", 0);
  fire("mouseup", 0);
  fire("click", 0);
}

function pressKey(el: Element, key: string): void {
  const init = { key, code: key, bubbles: true, cancelable: true, composed: true };
  el.dispatchEvent(new KeyboardEvent("keydown", init));
  el.dispatchEvent(new KeyboardEvent("keyup", init));
}

async function waitFor(predicate: () => boolean, timeoutMs: number, stepMs = 50): Promise<boolean> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    try {
      if (predicate()) return true;
    } catch {
      /* keep polling */
    }
    if (Date.now() >= end) return false;
    await sleep(stepMs);
  }
}

function outcome(partial: Partial<FillOutcome> & { ok: boolean }): FillOutcome {
  return { changed: false, text: "", labels: [], undo: null, ...partial };
}

function fail(failure: FillFailure): FillOutcome {
  return outcome({ ok: false, failure });
}

// ---- operations --------------------------------------------------------------------------------------------------------------

function fillText(field: AssistField, sv: SuggestionValue): FillOutcome {
  const el = field.el;
  const text = expectedText(field, sv);
  if (text === "") return fail("no_value");
  if (field.kind === "editable") return fillEditable(field, text);
  const input = el as HTMLInputElement | HTMLTextAreaElement;
  if (input.disabled || input.readOnly) return fail("disabled");
  const max = field.element.max_length ?? (input.maxLength > 0 ? input.maxLength : null);
  if (max && text.length > max) return fail("too_long");
  const previous = input.value;
  setNativeText(input, text);
  if (!textMatches(text, input.value, field)) {
    // the page rejected or rewrote it: put things back exactly as they were
    setNativeText(input, previous);
    return fail("verify");
  }
  return outcome({
    ok: true, changed: previous !== input.value, text, undo: () => {
      setNativeText(input, previous);
      return input.value === previous;
    },
  });
}

function currentEditableText(el: HTMLElement): string {
  return (el.innerText ?? el.textContent ?? "").replace(/ /g, " ");
}

function fillEditable(field: AssistField, text: string): FillOutcome {
  const el = field.el;
  if (!el.isContentEditable) return fail("disabled");
  const previous = currentEditableText(el);
  /** true = the page's editor took the edit (verified by the delayed re-check when it renders asynchronously) */
  const write = (value: string): boolean => {
    el.focus({ preventScroll: true });
    const selection = el.ownerDocument.defaultView?.getSelection();
    if (selection) {
      const range = el.ownerDocument.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    // 1. rich editors (Lexical, Draft, Slate, ProseMirror) own the document model and act on `beforeinput`: let them
    let handled = false;
    try {
      const before = new InputEvent("beforeinput", {
        bubbles: true, cancelable: true, composed: true, inputType: value === "" ? "deleteContentBackward" : "insertText",
        data: value === "" ? null : value,
      });
      handled = !el.dispatchEvent(before);
    } catch {
      handled = false;
    }
    if (!handled) {
      // 2. plain contenteditable: the browser's own insertText, then 3. a direct text write as the last resort
      let done = false;
      try {
        done = value === "" ? el.ownerDocument.execCommand("delete") : el.ownerDocument.execCommand("insertText", false, value);
      } catch {
        done = false;
      }
      if (!done || squash(currentEditableText(el)) !== squash(value)) {
        el.textContent = value;
        fireInputEvents(el, value);
      }
    }
    selection?.collapseToEnd();
    return handled || squash(currentEditableText(el)) === squash(value);
  };
  if (!write(text)) {
    write(previous.trim());
    return fail("verify");
  }
  return outcome({ ok: true, changed: squash(previous) !== squash(text), text, undo: () => write(previous.trim()) });
}

function pickByLabel<T extends { label: string; value: string }>(items: T[], label: string, value?: string): T | "ambiguous" | null {
  const byLabel = items.filter((o) => labelMatches(o.label, label));
  if (byLabel.length === 1) return byLabel[0];
  if (byLabel.length > 1) {
    if (value) {
      const exact = byLabel.filter((o) => o.value === value);
      if (exact.length === 1) return exact[0];
    }
    return "ambiguous";
  }
  if (value) {
    const byValue = items.filter((o) => o.value === value && value !== "");
    if (byValue.length === 1) return byValue[0];
  }
  return null;
}

/** The options that are real choices (not the "Select..." placeholder). */
function choiceOptions(select: HTMLSelectElement): HTMLOptionElement[] {
  return [...select.options].filter((o, i) => !o.disabled && !isPlaceholderOption(o, i) && !o.closest("optgroup[disabled]"));
}

function fillSelect(field: AssistField, sv: SuggestionValue): FillOutcome | Promise<FillOutcome> {
  const select = field.el as HTMLSelectElement;
  if (select.disabled) return fail("disabled");
  const first = selectOption(field, sv);
  if (first.failure !== "no_option" || choiceOptions(select).length > 0) return first;
  // The list is still empty: dependent or lazily loaded options (a country picks the regions, the list loads on focus). Give the
  // page a moment to provide it instead of failing for good.
  return waitFor(() => choiceOptions(select).length > 0, 1500, 50).then((arrived) => (arrived ? selectOption(field, sv) : first));
}

function selectOption(field: AssistField, sv: SuggestionValue): FillOutcome {
  const select = field.el as HTMLSelectElement;
  const label = sv.option?.label ?? sv.label ?? sv.display ?? "";
  const options = [...select.options].filter((o) => !o.disabled && !o.closest("optgroup[disabled]"));
  const items = options.map((o) => ({ label: o.label || o.text, value: o.value, el: o }));
  const picked = pickByLabel(items, label, sv.option?.value);
  if (picked === "ambiguous") return fail("ambiguous");
  if (!picked) return fail("no_option");
  const previousIndex = select.selectedIndex;
  const target = picked.el;
  const wasSame = select.selectedIndex === target.index;
  target.selected = true;
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
  if (setter && select.selectedOptions[0] !== target) setter.call(select, target.value);
  select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  if (select.selectedOptions[0] !== target) {
    select.selectedIndex = previousIndex;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return fail("verify");
  }
  return outcome({
    ok: true, changed: !wasSame, text: picked.label, labels: [picked.label], undo: () => {
      select.selectedIndex = previousIndex;
      select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return select.selectedIndex === previousIndex;
    },
  });
}

function fillMultiSelect(field: AssistField, sv: SuggestionValue): FillOutcome {
  const select = field.el as HTMLSelectElement;
  if (select.disabled) return fail("disabled");
  const wanted = wantedLabels(sv);
  if (!wanted.length) return fail("no_value");
  const options = [...select.options];
  const chosen = new Set<HTMLOptionElement>();
  for (const label of wanted) {
    const items = options.filter((o) => !o.disabled).map((o) => ({ label: o.label || o.text, value: o.value, el: o }));
    const picked = pickByLabel(items, label);
    if (picked === "ambiguous") return fail("ambiguous");
    if (!picked) return fail("no_option");
    chosen.add(picked.el);
  }
  const before = options.map((o) => o.selected);
  options.forEach((o) => { o.selected = chosen.has(o); });
  select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  select.dispatchEvent(new Event("change", { bubbles: true }));
  const ok = options.every((o) => o.selected === chosen.has(o));
  const restore = () => {
    options.forEach((o, i) => { o.selected = before[i]; });
    select.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return options.every((o, i) => o.selected === before[i]);
  };
  if (!ok) {
    restore();
    return fail("verify");
  }
  return outcome({ ok: true, changed: before.some((b, i) => b !== options[i].selected), text: wanted.join(", "), labels: wanted, undo: restore });
}

/**
 * Let the page's framework apply what the event it just received asked for. React 18 and Vue render at the end of the current task
 * (a microtask), so two microtask turns see the DOM they produced; widgets that settle later (zones, debounced state) are polled.
 */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

async function settled(predicate: () => boolean, timeoutMs = 600): Promise<boolean> {
  await flush();
  if (predicate()) return true;
  return waitFor(predicate, timeoutMs, 25);
}

/** The click that should turn one group member on/off: the control itself, or its visible label proxy. False when no click is possible. */
function clickMember(m: GroupMember, on: boolean, radio: boolean, useProxy = false): boolean {
  if (isOn(m.el, m.checked) === on) return false;
  if (!m.enabled || (radio && !on)) return false; // a radio cannot be switched off by clicking it
  const viaInput = m.el instanceof HTMLInputElement;
  clickElement(useProxy || (!viaInput && m.proxy) ? (m.proxy ?? m.el) : m.el);
  return true;
}

/** Turn one group member on/off with a real click and wait until the page shows it. */
async function setMember(m: GroupMember, on: boolean, radio: boolean): Promise<boolean> {
  const state = () => isOn(m.el, !on) === on;
  if (isOn(m.el, m.checked) === on) return true;
  if (!clickMember(m, on, radio)) return false;
  if (await settled(state)) return true;
  if (m.proxy && m.el instanceof HTMLInputElement) {
    clickMember(m, on, radio, true);
    return settled(state);
  }
  return false;
}

async function fillRadio(field: AssistField, sv: SuggestionValue): Promise<FillOutcome> {
  const label = wantedLabels(sv)[0] ?? "";
  const items = field.members.map((m) => ({ label: m.label, value: m.value, m }));
  const picked = pickByLabel(items, label, sv.option?.value);
  if (picked === "ambiguous") return fail("ambiguous");
  if (!picked) return fail("no_option");
  const member = picked.m;
  if (!member.enabled) return fail("disabled");
  const previous = field.members.find((m) => isOn(m.el, m.checked)) ?? null;
  const wasOn = isOn(member.el, member.checked);
  if (!wasOn && !(await setMember(member, true, true))) return fail("verify");
  return outcome({
    ok: true, changed: !wasOn, text: member.label, labels: [member.label], undo: () => {
      if (previous) {
        void setMember(previous, true, true);
        return isOn(previous.el, false);
      }
      // a radio group has no "off": reset the native state the way a form reset would
      for (const m of field.members) {
        if (m.el instanceof HTMLInputElement && m.el.checked) {
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked")?.set;
          if (setter) setter.call(m.el, false);
          m.el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
          m.el.dispatchEvent(new Event("change", { bubbles: true }));
        }
      }
      return field.members.every((m) => !isOn(m.el, false));
    },
  });
}

async function fillCheckbox(field: AssistField, sv: SuggestionValue): Promise<FillOutcome> {
  const el = field.el;
  if ((el as HTMLInputElement).disabled || el.getAttribute("aria-disabled") === "true") return fail("disabled");
  const want = desiredChecked(sv);
  const was = isOn(el, !!field.element.checked);
  if (was === want) return outcome({ ok: true, changed: false, text: want ? "yes" : "no", labels: [] });
  const click = () => clickElement(el instanceof HTMLInputElement || !field.links.proxy ? el : field.links.proxy);
  click();
  if (!(await settled(() => isOn(el, was) === want))) return fail("verify");
  return outcome({
    ok: true, changed: true, text: want ? "yes" : "no", labels: [], undo: () => {
      if (isOn(el, want) === was) return true;
      click();
      return isOn(el, want) === was;
    },
  });
}

async function fillCheckboxes(field: AssistField, sv: SuggestionValue): Promise<FillOutcome> {
  const wanted = wantedLabels(sv);
  if (!wanted.length) return fail("no_value");
  const chosen = new Set<GroupMember>();
  const items = field.members.map((m) => ({ label: m.label, value: m.value, m }));
  for (const label of wanted) {
    const picked = pickByLabel(items, label);
    if (picked === "ambiguous") return fail("ambiguous");
    if (!picked) return fail("no_option");
    chosen.add(picked.m);
  }
  const before = field.members.map((m) => isOn(m.el, m.checked));
  const changes = field.members.filter((m) => isOn(m.el, m.checked) !== chosen.has(m));
  if (changes.some((m) => !m.enabled)) return fail("disabled");
  // One member at a time and only after the page applied the previous click: frameworks that bind the group to an array (Vue v-model)
  // compute the next array from the one they rendered last, so two clicks in the same turn lose the first.
  for (const m of changes) await setMember(m, chosen.has(m), false);
  const reached = () => field.members.every((m) => isOn(m.el, m.checked) === chosen.has(m));
  const ok = await settled(reached, 400);
  const restore = (): boolean => {
    // what the keyboard layer can verify right away: one pass of clicks ...
    for (const [i, m] of field.members.entries()) clickMember(m, before[i], false);
    // ... and then the page catches up member by member (an array model built from stale state may need another pass)
    void (async () => {
      for (let pass = 0; pass < 3; pass++) {
        await flush();
        const wrong = field.members.map((m, i) => ({ m, want: before[i] })).filter(({ m, want }) => isOn(m.el, m.checked) !== want);
        if (!wrong.length) return;
        for (const { m, want } of wrong) await setMember(m, want, false);
      }
    })();
    return field.members.every((m, i) => isOn(m.el, m.checked) === before[i]);
  };
  if (!ok) {
    for (const [i, m] of field.members.entries()) if (isOn(m.el, m.checked) !== before[i]) await setMember(m, before[i], false);
    return fail("verify");
  }
  return outcome({ ok: true, changed: changes.length > 0, text: wanted.join(", "), labels: wanted, undo: restore });
}

// ---- custom combobox --------------------------------------------------------------------------------------------------------

function isExpanded(field: AssistField): boolean {
  return field.el.getAttribute("aria-expanded") === "true" || field.links.trigger?.getAttribute("aria-expanded") === "true";
}

function optionText(el: Element): string {
  return (el.getAttribute("data-dropdown-label") || el.textContent || "").trim();
}

/** Ids of the listbox the combobox controls: as scanned, and as the element says NOW (many widgets only set it while open). */
function listboxIds(field: AssistField): string[] {
  const live = `${field.el.getAttribute("aria-controls") ?? ""} ${field.el.getAttribute("aria-owns") ?? ""} ${field.links.trigger?.getAttribute("aria-controls") ?? ""}`;
  return [...new Set([...field.links.listboxIds, ...live.split(/\s+/)].filter(Boolean))];
}

/**
 * The options currently on screen for this combobox. A listbox the combobox points to wins; without one, any rendered option of the
 * page counts EXCEPT those in `ignore` (options that were already open before this fill started: they belong to some other widget).
 */
function visibleOptions(field: AssistField, ignore?: ReadonlySet<Element>): HTMLElement[] {
  const root = field.el.getRootNode() as Document | ShadowRoot;
  const scopes: Element[] = [];
  for (const id of listboxIds(field)) {
    const box = (root as Document).getElementById?.(id) ?? document.getElementById(id);
    if (box) scopes.push(box);
  }
  const found: HTMLElement[] = [];
  for (const scope of scopes) {
    if (scope.matches(OPTION_SELECTOR)) found.push(scope as HTMLElement);
    found.push(...(scope.querySelectorAll(OPTION_SELECTOR) as NodeListOf<HTMLElement>));
  }
  // a listbox this combobox points to and that is on screen is its popup even while it is still empty (options loading): never look elsewhere
  const scoped = found.length > 0 || scopes.some((box) => isRendered(box));
  if (!scoped) {
    const roots: (Document | ShadowRoot)[] = [document];
    while (roots.length) {
      const r = roots.shift() as Document | ShadowRoot;
      found.push(...(r.querySelectorAll(OPTION_SELECTOR) as NodeListOf<HTMLElement>));
      for (const n of r.querySelectorAll("*")) if (n.shadowRoot) roots.push(n.shadowRoot);
    }
  }
  return found.filter((o) => !inUiHost(o) && isRendered(o) && o.getAttribute("aria-disabled") !== "true" && (scoped || !ignore?.has(o)));
}

type Pick = { el: HTMLElement } | "ambiguous" | null;

/** Exact normalised label first; otherwise the single option whose words include every word of the answer. */
export function pickOption(options: HTMLElement[], label: string): Pick {
  const want = tokens(label);
  if (!want.length) return null;
  const entries = options.map((el) => ({ el, text: optionText(el) })).filter((e) => e.text !== "");
  const exact = entries.filter((e) => normalizeText(e.text) === normalizeText(label));
  if (exact.length) return { el: exact[0].el };
  const superset = entries.filter((e) => {
    const set = new Set(tokens(e.text));
    return want.every((t) => set.has(t));
  });
  if (superset.length === 1) return { el: superset[0].el };
  if (superset.length > 1) {
    const sizes = superset.map((e) => tokens(e.text).length);
    const min = Math.min(...sizes);
    const smallest = superset.filter((_, i) => sizes[i] === min);
    return smallest.length === 1 ? { el: smallest[0].el } : "ambiguous";
  }
  return null;
}

const NO_RESULTS_TEXT = /^(?:no (?:options|results|matches|items|entries)(?: found)?\b|nothing (?:found|to show)|no se (?:han )?encontr|sin (?:resultados|opciones|coincidencias)|no hay (?:resultados|opciones))/i;
const NO_RESULTS_MARK = '[class*="NoOptions" i],[class*="no-options" i],[class*="no_options" i],[class*="no-results" i],[class*="noresults" i],[class*="no_results" i]';

/** Has the popup said that nothing matches ("No options", "No results found")? Then waiting for options is pointless. */
function emptyMessageShown(field: AssistField): boolean {
  const root = field.el.getRootNode() as Document | ShadowRoot;
  for (const id of listboxIds(field)) {
    const box = (root as Document).getElementById?.(id) ?? document.getElementById(id);
    if (box && isRendered(box) && NO_RESULTS_TEXT.test((box.textContent ?? "").trim())) return true;
  }
  return [...document.querySelectorAll(NO_RESULTS_MARK)].some((n) => isRendered(n) && !inUiHost(n) && NO_RESULTS_TEXT.test((n.textContent ?? "").trim()));
}

function typingTarget(field: AssistField): HTMLInputElement | HTMLTextAreaElement | null {
  const el = field.el;
  if ((el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) && !el.readOnly && !el.disabled) return el;
  const search = field.links.search;
  if ((search instanceof HTMLInputElement) && !search.readOnly && isRendered(search)) return search;
  return null;
}

function renderedInputs(): HTMLInputElement[] {
  return [...document.querySelectorAll<HTMLInputElement>("input")].filter((i) => isRendered(i) && !inUiHost(i));
}

/** A text box that appeared because the popup opened (and is not the combobox itself): the place to type a search text. */
async function popupSearchBox(field: AssistField, before: ReadonlySet<Element>): Promise<HTMLInputElement | null> {
  // search-like inputs are recognised by what they are; a plain text box only when it sits in something that looks like a popup
  const POPUP = '[role="dialog"],[role="listbox"],[role="menu"],[class*="drop" i],[class*="menu" i],[class*="popup" i],[class*="popover" i],[class*="select" i],[class*="search" i]';
  const find = () => renderedInputs().find((i) => i !== field.el && !before.has(i) && !i.readOnly && !i.disabled && i.getAttribute("aria-hidden") !== "true"
    && (i.type === "search" || ["searchbox", "combobox"].includes(i.getAttribute("role") ?? "") || (["text", ""].includes(i.type) && i.closest(POPUP) !== null))) ?? null;
  const found = find();
  if (found) return found;
  await waitFor(() => find() !== null, 100, 25);
  return find();
}

const IGNORED_TEXT = "[aria-live],[role=log],[role=status],[role=alert],[role=listbox],[role=option],[role=menu],label,script,style,svg,[aria-hidden='true'],[hidden]";

/** The text a widget shows around its combobox (chosen value, chips) without labels, live regions, menus and icons. */
function widgetText(el: HTMLElement): string {
  let scope: HTMLElement = el;
  for (let i = 0; i < 4 && scope.parentElement; i++) {
    const parent = scope.parentElement;
    const others = [...parent.querySelectorAll('input:not([type="hidden"]),select,textarea,[role="combobox"],[contenteditable="true"]')].filter((c) => c !== el && !el.contains(c) && !c.contains(el));
    if (others.some((c) => c.getAttribute("aria-hidden") !== "true" && (c as HTMLElement).tabIndex !== -1)) break;
    scope = parent;
  }
  const out: string[] = [];
  const walk = (node: Node) => {
    if (node.nodeType === 3) {
      out.push(node.textContent ?? "");
      return;
    }
    if (node.nodeType !== 1) return;
    const e = node as Element;
    if (e !== el && e.matches(IGNORED_TEXT)) return;
    if (e instanceof HTMLInputElement || e instanceof HTMLTextAreaElement || e instanceof HTMLSelectElement) return;
    for (const child of e.childNodes) walk(child);
  };
  walk(scope);
  return out.join(" ").replace(INVISIBLE, "").replace(/\s+/g, " ").trim();
}

function scrollParentOf(option: HTMLElement, combobox: HTMLElement): HTMLElement | null {
  let cur = option.parentElement;
  for (let depth = 0; cur && depth < 8; cur = cur.parentElement, depth++) {
    if (cur.scrollHeight <= cur.clientHeight + 8 || cur.contains(combobox)) continue; // a scroller that holds the combobox is the page, not the list
    const overflow = getComputedStyle(cur).overflowY;
    if (overflow === "auto" || overflow === "scroll" || overflow === "overlay") return cur;
  }
  return null;
}

/** Virtualised menus render only the options in view: scroll through the list until the one we want is rendered. */
async function scrollFind(field: AssistField, label: string, ignore: ReadonlySet<Element>): Promise<Pick> {
  const first = visibleOptions(field, ignore)[0];
  const list = first ? scrollParentOf(first, field.el) : null;
  if (!first || !list) return null;
  const origin = list.scrollTop;
  const row = first.getBoundingClientRect().height || 32;
  const step = Math.max(row * 3, list.clientHeight - row);
  const end = Date.now() + 5000;
  list.scrollTop = 0;
  await sleep(40);
  let last = -1;
  while (Date.now() < end) {
    const pick = pickOption(visibleOptions(field, ignore), label);
    if (pick) return pick;
    if (list.scrollTop === last) break; // the end of the list
    last = list.scrollTop;
    list.scrollTop = last + step;
    await sleep(30);
  }
  list.scrollTop = origin;
  return null;
}

async function fillCombobox(field: AssistField, sv: SuggestionValue): Promise<FillOutcome> {
  const el = field.el;
  const label = wantedLabels(sv)[0];
  if (!label) return fail("no_option");
  if (el.getAttribute("aria-disabled") === "true" || (el as HTMLInputElement).disabled) return fail("disabled");
  const search = (sv.search_text ?? "").trim();
  const opener = (field.links.trigger && isRendered(field.links.trigger) ? field.links.trigger : null) ?? field.links.proxy ?? el;
  let box = search ? typingTarget(field) : null;
  let previousBox = box ? box.value : "";
  let typed = false;
  const inputsBefore = new Set(renderedInputs());
  // options that are open now while this combobox is not belong to another widget: never pick from them
  const ignore: ReadonlySet<Element> = isExpanded(field) ? new Set() : new Set(visibleOptions(field));

  const cleanup = async () => {
    if (typed && box) setNativeText(box, previousBox);
    if (isExpanded(field)) {
      pressKey(el, "Escape");
      await waitFor(() => !isExpanded(field), 400);
    }
  };
  const isOpen = () => isExpanded(field) || visibleOptions(field, ignore).length > 0;
  const closed = () => !isExpanded(field) || visibleOptions(field, ignore).length === 0;

  try {
    el.focus({ preventScroll: true });
    if (!isOpen()) {
      pointerClick(opener);
      let opened = await waitFor(isOpen, box ? 400 : 700);
      // some widgets open only once text is typed: with a search text that is the next step, not a failure
      if (!opened && !box) {
        if (opener !== el) {
          pointerClick(el);
          opened = await waitFor(isOpen, 700);
        }
        if (!opened) {
          pressKey(el, "ArrowDown");
          opened = await waitFor(isOpen, 700);
        }
        if (!opened) return fail("combobox");
      }
    }
    // a search box that exists only inside the opened popup (Select2, Tom Select ...) is found now, never typed into before it is there
    if (search && !box) {
      box = await popupSearchBox(field, inputsBefore);
      previousBox = box ? box.value : "";
    }
    // never type an empty search text; with no text box the menu is expected to list the options already
    if (box) {
      setNativeText(box, search);
      typed = true;
    }
    let chosen: Pick = null;
    let lastSignature = "";
    let stable = 0;
    let emptyFor = 0;
    const found = await waitFor(() => {
      const options = visibleOptions(field, ignore);
      chosen = pickOption(options, label);
      const signature = options.map(optionText).join("\u241f");
      stable = signature === lastSignature ? stable + 1 : 0;
      lastSignature = signature;
      if (chosen === "ambiguous") return stable >= 2;
      if (chosen === null) {
        // the popup says "No options" and keeps saying it (a list that is still loading says "Loading..." first): nothing will match
        emptyFor = options.length === 0 && emptyMessageShown(field) ? emptyFor + 1 : 0;
        if (emptyFor >= 8) return true;
        // a populated list that stopped changing and still has no match: give up early instead of waiting out the timeout
        return options.length > 0 && stable >= 12;
      }
      return stable >= 1;
    }, 4000, 60);
    if (found && chosen === null) chosen = await scrollFind(field, label, ignore);
    if (!found || chosen === null) {
      await cleanup();
      return fail("no_option");
    }
    if (chosen === "ambiguous") {
      await cleanup();
      return fail("ambiguous");
    }
    const previousSelection = comboSelection(el);
    const textBefore = widgetText(el);
    const targets = [label];
    /** Is the page showing the selection now? Reads the widget's own display; blind widgets are judged by their visible text changing. */
    const applied = () => {
      const shown = comboSelection(el);
      const hit = shown !== ""
        ? targets.some((t) => selectionMatches(shown, t))
        : (() => {
          const text = widgetText(el);
          return text !== textBefore && targets.some((t) => selectionMatches(text, t));
        })();
      return hit && closed();
    };
    let verified = false;
    // the menu may re-render between finding the option and clicking it (stale node): resolve again right before every click
    for (let attempt = 0; attempt < 3 && !verified; attempt++) {
      const pick = pickOption(visibleOptions(field, ignore), label);
      if (pick === "ambiguous") {
        await cleanup();
        return fail("ambiguous");
      }
      if (!pick) {
        verified = await waitFor(applied, 250);
        if (!verified) await sleep(80);
        continue;
      }
      // a repeat click only when the option is not already marked chosen: in a multi-select it would switch the choice off again
      if (attempt > 0 && pick.el.getAttribute("aria-selected") === "true") {
        verified = await waitFor(applied, 600);
        continue;
      }
      const clickedText = optionText(pick.el);
      if (clickedText && !targets.includes(clickedText)) targets.push(clickedText);
      pointerClick(pick.el);
      verified = await waitFor(applied, attempt === 0 ? 900 : 1200);
      // selected, but the menu did not close by itself: close it politely
      if (!verified && (selectionMatches(comboSelection(el), label) || targets.some((t) => selectionMatches(comboSelection(el), t)))) {
        pressKey(el, "Escape");
        verified = await waitFor(applied, 600);
      }
    }
    if (!verified) {
      await cleanup();
      return fail("combobox");
    }
    // Some widgets blur their input once an option is chosen (react-select does when it believes the device is a touch screen, which
    // Safari's TouchEvent constructor suggests): focus then falls to <body> and the next Tab would start from the top of the page.
    // Focus that moved to another control is the person's choice and is left alone.
    const active = deepActiveElement();
    if (!active || active === document.body || active === document.documentElement) {
      try {
        el.focus({ preventScroll: true });
      } catch {
        /* focus is a courtesy */
      }
    }
    if (comboSelection(el) === "") SEEN_BY_TEXT.set(el, { text: widgetText(el), label });
    return outcome({ ok: true, changed: !selectionMatches(previousSelection, label), text: label, labels: [label], undo: null });
  } catch {
    await cleanup();
    return fail("combobox");
  }
}

// ---- files ------------------------------------------------------------------------------------------------------------------

function acceptable(accept: string, filename: string, mime: string): boolean {
  const rules = accept.split(",").map((r) => r.trim().toLowerCase()).filter(Boolean);
  if (!rules.length) return true;
  const name = filename.toLowerCase();
  const type = mime.toLowerCase();
  return rules.some((rule) => {
    if (rule.startsWith(".")) return name.endsWith(rule);
    if (rule.endsWith("/*")) return type.startsWith(rule.slice(0, -1));
    return type === rule;
  });
}

async function fillFile(field: AssistField, sv: SuggestionValue, ctx: FillContext): Promise<FillOutcome> {
  const input = field.el as HTMLInputElement;
  const doc = sv.document;
  if (!doc?.id) return fail("document");
  if (input.disabled) return fail("disabled");
  const content = await ctx.fetchDocument(doc.id);
  if (!content) return ctx.isOrphaned() ? fail("orphaned") : fail("document");
  const bytes = base64ToBytes(content.data_b64);
  if (bytes.length === 0 || bytes.length > MAX_DOCUMENT_BYTES || bytes.length !== content.size) return fail("file");
  const digest = await sha256Hex(bytes);
  const want = String(content.sha256 || doc.sha256 || "").toLowerCase();
  if (!want || digest !== want || (doc.sha256 && String(doc.sha256).toLowerCase() !== digest)) return fail("file");
  if (!acceptable(input.accept || "", content.filename, content.mime)) return fail("accept");
  if (!input.isConnected) return fail("disconnected");
  const file = new File([bytes as unknown as BlobPart], content.filename, { type: content.mime });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  const previous = new DataTransfer();
  for (const f of input.files ?? []) previous.items.add(f);
  try {
    input.files = transfer.files;
  } catch {
    return fail("file");
  }
  input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  if (!(input.files && input.files.length === 1 && input.files[0].name === content.filename)) return fail("verify");
  return outcome({
    ok: true, changed: true, text: content.filename, labels: [content.filename], undo: () => {
      input.files = previous.files;
      input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return (input.files?.length ?? 0) === previous.files.length;
    },
  });
}

// ---- entry point -------------------------------------------------------------------------------------------------------------

/** Performs one suggestion on one field. Synchronous for everything except custom comboboxes and file attachment. */
export function fillField(field: AssistField, sv: SuggestionValue, ctx: FillContext): FillOutcome | Promise<FillOutcome> {
  try {
    if (!field.el.isConnected) return fail("disconnected");
    if (ctx.isOrphaned()) return fail("orphaned");
    switch (sv.operation) {
      case "TYPE_TEXT": return isTextKind(field.kind) ? fillText(field, sv) : fail("unsupported");
      case "SELECT_OPTION": {
        if (field.kind !== "select") return fail("unsupported");
        const out = fillSelect(field, sv);
        return out instanceof Promise ? out.catch(() => fail("exception")) : out;
      }
      case "CHECK_RADIO": return field.kind === "radio" ? fillRadio(field, sv).catch(() => fail("exception")) : fail("unsupported");
      case "SET_CHECKBOX": return field.kind === "checkbox" ? fillCheckbox(field, sv).catch(() => fail("exception")) : fail("unsupported");
      case "SET_CHECKBOXES":
        if (field.kind === "checkboxes") return fillCheckboxes(field, sv).catch(() => fail("exception"));
        return field.kind === "select" && (field.el as HTMLSelectElement).multiple ? fillMultiSelect(field, sv) : fail("unsupported");
      case "OPEN_AND_SELECT": return field.kind === "combobox" ? fillCombobox(field, sv).catch(() => fail("exception")) : fail("unsupported");
      case "ATTACH_FILE": return field.kind === "file" ? fillFile(field, sv, ctx).catch(() => fail("exception")) : fail("unsupported");
      default: return fail("unsupported");
    }
  } catch {
    return fail("exception");
  }
}
