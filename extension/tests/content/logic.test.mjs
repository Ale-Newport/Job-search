// Pure-logic tests of the content script (no browser): the eligibility rules of state.ts and the key rules of util.ts.
//   cd extension && node --import ./tests/unit/register.mjs --test tests/content/logic.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";
import { duplicateStatus, evaluate } from "../../src/content/state.ts";
import { altLetter, altShift, chipActionNumber, ruleOf, stripRequiredMarks } from "../../src/content/util.ts";

function input(over = {}) {
  const field = { key: "top:1", kind: "text", label: "First name", element: { context: "" }, el: { isConnected: true }, anchorEl: { isConnected: true } };
  const suggestion = {
    state: "VERIFIED", insert: "tab", request_id: "r1", suggestion: { operation: "TYPE_TEXT", display: "Alex", text: "Alex" },
    flags: {}, needs: null, generation: null,
  };
  const entry = { suggestion, digest: "d", knowledgeVersion: "kv1", receivedAt: 0, shownAt: null, ...(over.entry ?? {}) };
  return {
    field, entry, stale: false, fs: undefined, settings: { tab_inserts_protected: false }, now: 1000, relation: "empty", pageBlocked: false,
    connected: true, via: "tab", ...over,
  };
}

test("an eligible suggestion inserts", () => {
  assert.deepEqual(evaluate(input()), { ok: true, tier: "tab" });
});

test("CPL-13 / UXR-08: an application already sent keeps EVERYTHING from inserting: Tab, Enter and a click alike", () => {
  // (UXR-08 changed the second line: the banner promises "I won't insert anything until you continue", so an explicit click is refused too)
  assert.deepEqual(evaluate(input({ duplicateBlock: true })), { ok: false, reason: "duplicate" });
  assert.deepEqual(evaluate(input({ duplicateBlock: true, via: "explicit" })), { ok: false, reason: "duplicate" });
  assert.deepEqual(evaluate(input({ duplicateBlock: true, via: "explicit", replace: true, relation: "conflict" })), { ok: false, reason: "duplicate" });
  assert.deepEqual(evaluate(input({ duplicateBlock: false, via: "explicit" })), { ok: true, tier: "tab" });
});

test("UXR-08: the application's own status is reduced to the words the banner needs", () => {
  const cases = [
    ["APPLIED", "applied"], ["CONFIRMED", "applied"], ["REJECTED", "rejected"], ["rejected", "rejected"], ["WITHDRAWN", "withdrawn"], ["GHOSTED", "ghosted"],
    ["OFFER", "offer"], ["INTERVIEW", "interview"], ["FINAL_INTERVIEW", "interview"], ["RECRUITER_SCREEN", "interview"], ["TECHNICAL_TEST", "interview"],
    [null, "applied"], [undefined, "applied"], ["", "applied"], ["SOMETHING_NEW", "applied"],
  ];
  for (const [raw, want] of cases) assert.equal(duplicateStatus(raw), want, String(raw));
});

test("CPL-09: a draft written for another company or Profile is inserted by neither Tab nor a click", () => {
  assert.deepEqual(evaluate(input({ staleDraft: true })), { ok: false, reason: "stale_draft" });
  assert.deepEqual(evaluate(input({ staleDraft: true, via: "explicit" })), { ok: false, reason: "stale_draft" });
});

test("SEC-01: nothing is inserted in a frame nobody can see", () => {
  assert.deepEqual(evaluate(input({ frameHidden: true })), { ok: false, reason: "frame_hidden" });
  assert.deepEqual(evaluate(input({ frameHidden: true, via: "explicit" })), { ok: false, reason: "frame_hidden" });
});

test("UX-11: the letter printed on the key decides, the physical key only when the key prints no ASCII letter", () => {
  const cases = [
    [{ key: "z", code: "KeyZ" }, "z", true], // QWERTY
    [{ key: "z", code: "KeyY" }, "z", true], // QWERTZ: the key printed Z
    [{ key: "y", code: "KeyZ" }, "z", false], // QWERTZ: the key printed Y is not Alt+Z
    [{ key: "z", code: "KeyW" }, "z", true], // AZERTY
    [{ key: "Z", code: "KeyZ" }, "z", true], // caps lock / shift state
    [{ key: "Ω", code: "KeyZ" }, "z", true], // Mac Option+Z prints a symbol: the physical key
    [{ key: "Dead", code: "KeyZ" }, "z", true],
    [{ key: "я", code: "KeyZ" }, "z", true], // a Cyrillic layout: no ASCII letter, the physical key
    [{ key: "r", code: "KeyP" }, "r", true], // Dvorak: the key printed R
    [{ key: "p", code: "KeyR" }, "r", false], // Dvorak: the physical R key prints P
    [{ key: "Tab", code: "Tab" }, "r", false],
    [{ key: undefined, code: "KeyR" }, "r", true],
  ];
  for (const [event, letter, want] of cases) assert.equal(altLetter(event, letter), want, JSON.stringify([event, letter]));
});

test("UXR-11: the printed letter decides, an ASCII key of another layout never fires the shortcut, a non-ASCII key falls back to the physical one", () => {
  const cases = [
    [{ key: "M", code: "KeyM" }, "m", true], // QWERTY, Shift held
    [{ key: "m", code: "Semicolon" }, "m", true], // AZERTY: the key printed M
    [{ key: "?", code: "KeyM" }, "m", false], // AZERTY: the key at the US M position prints a question mark: not Alt+M
    [{ key: ",", code: "KeyM" }, "m", false],
    [{ key: "Â", code: "KeyM" }, "m", true], // a Mac prints a symbol for Option+Shift+M
    [{ key: "Ь", code: "KeyM" }, "m", true], // Cyrillic
    [{ key: "Dead", code: "KeyM" }, "m", true],
    [{ key: "n", code: "KeyN" }, "m", false],
    [{ key: "Z", code: "KeyZ" }, "z", true],
  ];
  for (const [event, letter, want] of cases) assert.equal(altLetter(event, letter), want, JSON.stringify([event, letter]));
});

test("UXR-03: the chip's numbered actions are reached by chords that type no character", () => {
  const press = (over) => ({ key: "1", code: "Digit1", altKey: false, ctrlKey: false, shiftKey: false, metaKey: false, ...over });
  // not a Mac: Alt+Shift+digit
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true }), false), 1);
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, key: "&", code: "Digit7" }), false), 7);
  assert.equal(chipActionNumber(press({ altKey: true }), false), 0, "Alt+digit alone is not a chord");
  assert.equal(chipActionNumber(press({ altKey: true, ctrlKey: true }), false), 0, "Ctrl+Alt is AltGr on many layouts");
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, ctrlKey: true }), false), 0);
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, key: "8", code: "Digit8" }), false), 0, "only 1 to 7 are numbered");
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, key: "1", code: "Numpad1" }), false), 1, "a numpad digit by the digit it prints");
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, key: "x", code: "KeyX" }), false), 0);
  // a Mac: Control+Option+digit; Option+digit prints a character ("¡" ...) and so does Option+Shift+digit
  assert.equal(chipActionNumber(press({ altKey: true, ctrlKey: true, key: "¡" }), true), 1);
  assert.equal(chipActionNumber(press({ altKey: true, ctrlKey: true, key: "™", code: "Digit2" }), true), 2);
  assert.equal(chipActionNumber(press({ altKey: true, key: "¡" }), true), 0);
  assert.equal(chipActionNumber(press({ altKey: true, shiftKey: true, key: "⁄", code: "Digit1" }), true), 0);
  assert.equal(chipActionNumber(press({ altKey: true, ctrlKey: true, metaKey: true }), true), 0);
  assert.equal(chipActionNumber(press({ altKey: true, ctrlKey: true, shiftKey: true }), true), 0);
});

test("UXR-06: Alt+Shift (and nothing else) is the chord of the panel and of the batch undo", () => {
  assert.equal(altShift({ altKey: true, shiftKey: true, ctrlKey: false, metaKey: false }), true);
  assert.equal(altShift({ altKey: true, shiftKey: true, ctrlKey: true, metaKey: false }), false);
  assert.equal(altShift({ altKey: true, shiftKey: false, ctrlKey: false, metaKey: false }), false);
  assert.equal(altShift({ altKey: true, shiftKey: true, ctrlKey: false, metaKey: true }), false);
});

test("UXR-14: the required-marker glyphs of a page are not part of a label a person reads or hears", () => {
  const cases = [
    ["First Name *", "First Name"], ["Email ✱", "Email"], ["Full name✱", "Full name"], ["* Email", "Email"], ["Phone (required)", "Phone"],
    ["Phone (Required)", "Phone"], ["Teléfono (obligatorio)", "Teléfono"], ["Are you legally authorized to work in the United Kingdom? *", "Are you legally authorized to work in the United Kingdom?"],
    ["Name* (as on your passport)", "Name (as on your passport)"], ["City (*)", "City"], ["Salary †", "Salary"], ["Email *:", "Email:"], ["Reference ✳ ✳", "Reference"],
    ["Plain label", "Plain label"], ["C++ experience", "C++ experience"], ["5*3 grid", "5*3 grid"], ["*", "*"], ["  Spaced   out *  ", "Spaced out"], ["", ""],
    ["Rate us ★★★★★", "Rate us ★★★★★"], ["Optional notes (optional)", "Optional notes (optional)"],
  ];
  for (const [raw, want] of cases) assert.equal(stripRequiredMarks(raw), want, JSON.stringify(raw));
});

test("UX-13: the rule the worker names is read out of its sentence, and a sentence that names none gives none", () => {
  const sentence = (rule) => `The assistant still starts by itself here because of the rule for ${rule}. Remove it on the extension's options page or in Meridian (Settings → Browser assistant).`;
  assert.equal(ruleOf(sentence("jobs.example.com")), "jobs.example.com");
  assert.equal(ruleOf(sentence("*.example.com")), "*.example.com", "a rule with dots of its own");
  assert.equal(ruleOf(sentence("boards.greenhouse.io")), "boards.greenhouse.io");
  assert.equal(ruleOf("Meridian needs your permission to start automatically on this site."), null);
  assert.equal(ruleOf(""), null);
  assert.equal(ruleOf(undefined), null);
  assert.equal(ruleOf(sentence("x".repeat(400))), null, "an absurd name is not shown");
});
