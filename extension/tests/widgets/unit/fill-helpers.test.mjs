// Pure helpers of the fill engine that real widgets forced us to write: phone numbers a widget re-writes, and what a combobox shows
// while nothing is chosen. Run: node --import ../../unit/register.mjs --test tests/widgets/unit/*.test.mjs   (from extension/)
import assert from "node:assert/strict";
import { test } from "node:test";
import { isComboPlaceholder, samePhone } from "../../../src/content/fill.ts";

test("a widget that moves the dial code into the flag shows the same number", () => {
  assert.ok(samePhone("+44 7700 900123", "7700 900123"));
  assert.ok(samePhone("+44 7700 900123", "07700 900123"), "national trunk 0");
  assert.ok(samePhone("+34 612 345 678", "612 345 678"));
  assert.ok(samePhone("+1 202 555 0123", "(202) 555-0123"), "one digit country code");
  assert.ok(samePhone("+353 87 123 4567", "087 123 4567"), "three digit country code (Ireland) with the trunk 0");
  assert.ok(samePhone("+44 7700 900123", "+44 7700 900123"));
  assert.ok(samePhone("07700 900123", "+44 7700 900123"), "the reverse: national in, international shown");
});

test("anything but exactly the country calling code lost is a different number", () => {
  assert.ok(!samePhone("+44 7700 900123", "700 900123"), "the widget swallowed a digit of the number");
  assert.ok(!samePhone("+44 7700 900123", "00 900123"), "far too short");
  assert.ok(!samePhone("+44 7700 900123", "7700 900124"), "last digit differs");
  assert.ok(!samePhone("+44 7700 900123", "4 7700 900123"), "half of the dial code left");
  assert.ok(!samePhone("+44 7700 900123", "+34 7700 900123"), "another country");
  assert.ok(!samePhone("+44 7700 900123", ""));
  assert.ok(!samePhone("", "7700 900123"));
});

test("placeholders of custom comboboxes are empty, real choices are not", () => {
  for (const text of ["Select One", "Select...", "Select", "Select an option", "Select a country", "Choose...", "Choose an option", "Please select", "-- Select --",
    "— Select —", "Pick one", "Selecciona", "Selecciona una opción", "Elige...", "Select your country", "Select level", "Select a Country", "Choose country", "Select An Option", "  Select One  ", "​Select One"]) {
    assert.ok(isComboPlaceholder(text), JSON.stringify(text));
  }
  for (const text of ["Select Group", "Select Group Ltd", "Select Committee", "Chosen Ltd", "United Kingdom", "Yes", "Selected", "Pick-up truck", "Mobile", ""]) {
    assert.ok(!isComboPlaceholder(text), JSON.stringify(text));
  }
});
