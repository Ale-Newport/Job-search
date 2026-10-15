// Fields the assistant never touches (src/content/sensitive.ts) and the pure helpers of src/content/visibility.ts.
// Run: node --import ./tests/unit/register.mjs --test tests/widgets/unit/*.test.mjs   (from extension/)
import assert from "node:assert/strict";
import { test } from "node:test";
import { normalizeParts, sensitiveClass } from "../../../src/content/sensitive.ts";
import { clipsEverything, colorAlpha, colorRgb, filterOpacity } from "../../../src/content/visibility.ts";

const cls = (label, extra = {}) => sensitiveClass({ label, ...extra });

test("one-time codes, cards, banks, ID numbers, security answers and passwords are never touched", () => {
  const never = {
    otp: ["Verification code", "Enter the verification code", "One-time password", "OTP", "Security code", "2FA code", "Authentication code", "SMS code",
      "Código de verificación", "Código de seguridad", "Two-factor code"],
    card: ["Card number", "Credit card number", "CVV", "CVC", "Name on card", "Card expiry date", "Número de tarjeta", "Titular de la tarjeta"],
    bank: ["IBAN", "Bank account number", "Account number", "Routing number", "Sort code", "SWIFT code", "Bank details", "Número de cuenta", "Account holder name"],
    identity: ["Social Security Number", "SSN", "National Insurance number", "NI number", "National ID number", "ID number", "Passport number", "Tax ID",
      "Tax identification number", "Driving licence number", "DNI", "NIE", "Número de documento de identidad", "Número de la seguridad social",
      "Taxpayer ID", "Aadhaar number", "Social insurance number"],
    "security-answer": ["Mother's maiden name", "Security question", "Security answer", "Secret answer", "Memorable word", "Respuesta de seguridad"],
    password: ["Password", "Create a password", "Confirm password", "Passphrase", "Contraseña"],
  };
  for (const [kind, labels] of Object.entries(never)) for (const label of labels) assert.equal(cls(label), kind, `${label} -> ${kind}`);
});

test("the standard autocomplete tokens name the class whatever the label says", () => {
  assert.equal(sensitiveClass({ label: "Reference", autocomplete: "cc-number" }), "card");
  assert.equal(sensitiveClass({ label: "Reference", autocomplete: "section-billing cc-exp-month" }), "card");
  assert.equal(sensitiveClass({ label: "Code", autocomplete: "one-time-code" }), "otp");
  assert.equal(sensitiveClass({ label: "Create one", autocomplete: "new-password" }), "password");
  assert.equal(sensitiveClass({ label: "Again", autocomplete: "current-password" }), "password");
  assert.equal(sensitiveClass({ label: "Email", autocomplete: "email" }), "");
  assert.equal(sensitiveClass({ label: "Phone", autocomplete: "tel" }), "");
});

test("name, id and placeholder count as much as the label (camelCase and punctuation included)", () => {
  assert.equal(sensitiveClass({ label: "Reference", name: "cardNumber" }), "card");
  assert.equal(sensitiveClass({ label: "Reference", id: "ssn" }), "identity");
  assert.equal(sensitiveClass({ label: "Reference", name: "user[otp]" }), "otp");
  assert.equal(sensitiveClass({ label: "Reference", id: "routing_number" }), "bank");
  assert.equal(sensitiveClass({ label: "Reference", placeholder: "Enter your SSN" }), "identity");
  assert.equal(sensitiveClass({ label: "Reference", name: "cvv" }), "card");
});

test("look-alikes that are ordinary questions stay ordinary", () => {
  for (const label of ["Phone", "Email address", "Full name", "Years of experience with Swift", "PIN code", "Postcode", "Do you hold a valid passport?",
    "Do you currently receive Social Security benefits?", "Why do you want to work here?", "Date of birth", "Current employer", "Expected salary",
    "LinkedIn profile", "How did you hear about us?", "Are you over 18?", "Passport (yes/no)", "Address line 1", "Cover letter", "Notice period",
    "Maiden name", "Referral code", "Describe a time you used a secret to win trust"]) {
    assert.equal(cls(label), "", label);
  }
  assert.equal(sensitiveClass({ label: "Phone", name: "phone", id: "phone", placeholder: "07700 900123", autocomplete: "tel" }), "");
  assert.equal(sensitiveClass({ label: "Expiry date of your work permit", name: "permit_expiry" }), "");
});

test("a long sentence that merely mentions a keyword is a question, not a field name", () => {
  const essay = "Please describe in your own words, in as much detail as you can, how you would handle a situation in which a colleague shared their password with you";
  assert.equal(cls(essay), "");
});

test("normalizeParts splits camelCase, drops accents and punctuation", () => {
  assert.equal(normalizeParts("cardNumber"), "card number");
  assert.equal(normalizeParts("Número_de-Tarjeta"), "numero de tarjeta");
  assert.equal(normalizeParts("Mother's Maiden Name"), "mothers maiden name");
});

test("colour alpha: transparent, rgba, space separated and percentage forms", () => {
  assert.equal(colorAlpha("transparent"), 0);
  assert.equal(colorAlpha("rgba(0, 0, 0, 0)"), 0);
  assert.equal(colorAlpha("rgba(255, 255, 255, 0.5)"), 0.5);
  assert.equal(colorAlpha("rgb(255, 255, 255)"), 1);
  assert.equal(colorAlpha("rgb(0 0 0 / 25%)"), 0.25);
  assert.equal(colorAlpha("color(srgb 1 1 1 / 0)"), 0);
  assert.equal(colorAlpha("oklch(0.5 0.1 200 / 0.4)"), 0.4);
  assert.equal(colorAlpha("red"), 1);
  assert.equal(colorAlpha(""), 0);
  assert.deepEqual(colorRgb("rgb(255, 255, 255)"), [255, 255, 255]);
  assert.deepEqual(colorRgb("rgba(1, 2, 3, 0.5)"), [1, 2, 3]);
  assert.equal(colorRgb("oklch(0.5 0.1 200)"), null);
});

test("filter opacity and cuts that leave nothing", () => {
  assert.equal(filterOpacity("none"), 1);
  assert.equal(filterOpacity("opacity(0)"), 0);
  assert.equal(filterOpacity("blur(2px) opacity(50%)"), 0.5);
  assert.equal(filterOpacity("opacity(0.5) opacity(0.5)"), 0.25);
  assert.ok(clipsEverything("inset(100%)", ""));
  assert.ok(clipsEverything("inset(50%)", ""));
  assert.ok(clipsEverything("circle(0)", ""));
  assert.ok(clipsEverything("polygon(0 0, 0 0, 0 0)", ""));
  assert.ok(clipsEverything("none", "rect(0px, 0px, 0px, 0px)"));
  assert.ok(!clipsEverything("none", "auto"));
  assert.ok(!clipsEverything("inset(10px)", "rect(0px, 100px, 30px, 0px)"));
});
