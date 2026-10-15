// Fields the assistant must never touch: one-time codes, card and bank details, government ID numbers, security questions and
// passwords of any kind. They are not assist fields at all: nothing about them is sent to Meridian, no chip is drawn, the Tab walk
// leaves them to the browser (they are plain tab stops) and "Fill verified" never reaches them (design principle 2 and section 8).
//
// The decision reads what the PAGE says about the control (autocomplete token, name, id, placeholder, visible label). A page can
// only make the assistant touch LESS by naming a control like this, never more: the classes are strict denials.

export type SensitiveClass = "otp" | "card" | "bank" | "identity" | "security-answer" | "password";

export interface SensitiveParts {
  label?: string;
  name?: string;
  id?: string;
  placeholder?: string;
  autocomplete?: string;
  type?: string;
}

/** `autocomplete` tokens that name these classes (the standard vocabulary: `cc-number`, `one-time-code`, `new-password`, ...). */
const AUTOCOMPLETE: [RegExp, SensitiveClass][] = [
  [/^cc-/, "card"],
  [/^one-time-code$/, "otp"],
  [/^(?:new|current)-password$/, "password"],
  [/^transaction-(?:amount|currency)$/, "card"],
];

const OTP = /\b(?:otp|one time (?:pass(?:word|code)?|code|pin)|(?:verification|authentication|authenticator|confirmation|activation|login|sms|email) code|security code|2fa|two factor(?: code)?|mfa|pass ?code|codigo (?:de )?(?:verificacion|seguridad|confirmacion|autenticacion)|clave (?:de un solo uso|unica))\b/;
const CARD = /\b(?:card (?:number|no|num|holder|verification|security)|(?:credit|debit|bank) card|cardnumber|cc (?:num|number|exp|expiry|expiration|csc|cvv|cvc)|ccnum|cvv2?|cvc2?|csc|name on (?:the )?card|card expir\w*|expir\w* date of (?:the )?card|numero de tarjeta|tarjeta (?:de )?(?:credito|debito)|titular de la tarjeta)\b/;
const BANK = /\b(?:iban|swift (?:code|bic|number)|swift\/bic|bic\/swift|bic code|routing (?:number|no)|aba (?:number|routing)|sort code|(?:bank )?account (?:number|no|num)|bank account|bank details|bank name|account holder|beneficiary (?:name|account)|numero de cuenta|cuenta bancaria|clabe)\b/;
const IDENTITY = /\b(?:social security (?:number|no|num)|ssn|national insurance (?:number|no|num)|ni number|nino|national id(?:entity)? (?:number|no|num)|id number|identity (?:card )?number|identification number|passport (?:number|no|num)|driv(?:ing|er'?s?) licen[cs]e (?:number|no|num)|tax id|tax identification|tax ?payer (?:id|identification|number)|tax file number|social insurance number|sin number|aadhaar|aadhar|pan number|curp|cpf|cnpj|dni|nie|nif|numero (?:de (?:la )?|del )?(?:documento|identificacion|identidad|seguridad social|pasaporte)|documento (?:de )?identidad|bsn|personnummer|steuer ?id|steueridentifikationsnummer|sozialversicherungsnummer|numero de securite sociale)\b/;
const SECURITY_ANSWER = /\b(?:security (?:question|answer)|secret (?:question|answer)|mother'?s? (?:maiden )?name|memorable (?:word|name|date|information)|pregunta (?:de )?seguridad|respuesta (?:de )?seguridad|first pet|pet'?s name)\b/;
const PASSWORD = /\b(?:password|passwd|passphrase|contrasena|clave de acceso)\b/;

const CLASSES: [RegExp, SensitiveClass][] = [
  [OTP, "otp"], [CARD, "card"], [BANK, "bank"], [IDENTITY, "identity"], [SECURITY_ANSWER, "security-answer"], [PASSWORD, "password"],
];

/** Lower case, accent free, camelCase split, punctuation (`_ - [ ] . /` and the like) turned into spaces; `/` and `'` kept as the patterns need. */
export function normalizeParts(value: string): string {
  return String(value ?? "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9'/]+/g, " ")
    .replace(/'s\b/g, "s")
    .trim();
}

function tokensOf(autocomplete: string): string[] {
  return String(autocomplete ?? "").toLowerCase().split(/\s+/).filter(Boolean);
}

/** A label longer than this is a sentence (a question, an essay prompt), not the name of a field. */
const MAX_LABEL = 100;
/** "Password" in a label is a field only when the label is about nothing else ("Create a password", "Confirm password"). */
const MAX_PASSWORD_LABEL = 40;

/** Which class of never-touch field is this control, or `""` for an ordinary one. */
export function sensitiveClass(parts: SensitiveParts): SensitiveClass | "" {
  for (const token of tokensOf(parts.autocomplete ?? "")) {
    for (const [pattern, kind] of AUTOCOMPLETE) if (pattern.test(token)) return kind;
  }
  const sources: [string | undefined, boolean][] = [[parts.label, true], [parts.name, false], [parts.id, false], [parts.placeholder, false]];
  for (const [text, isLabel] of sources) {
    if (!text || (isLabel && text.length > MAX_LABEL)) continue;
    const normal = normalizeParts(text.length > 160 ? text.slice(0, 160) : text);
    if (!normal) continue;
    for (const [pattern, kind] of CLASSES) {
      if (kind === "password" && isLabel && text.length > MAX_PASSWORD_LABEL) continue;
      if (pattern.test(normal)) return kind;
    }
  }
  return "";
}
