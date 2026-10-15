// REAL intl-tel-input 29 (what "Phone" fields on career sites are made of): flag dropdown + national number, in three setups.
import intlTelInput from "intl-tel-input/intlTelInputWithUtils";
import "intl-tel-input/styles-no-assets";

const setups = [
  { id: "phone_default", label: "Mobile phone", options: { initialCountry: "gb" } },
  { id: "phone_separate", label: "Contact phone (separate dial code)", options: { initialCountry: "es", separateDialCode: true } },
  { id: "phone_international", label: "Phone (international format)", options: { initialCountry: "us", nationalMode: false, formatAsYouType: true } },
  // the page mirrors the chosen country into a hidden "phone_country" field ("United Kingdom (+44)"), which tells the scanner the input is national-only
  { id: "phone_hidden_country", label: "Phone (country kept in a hidden field)", options: { initialCountry: "gb", separateDialCode: true }, hidden: true },
];
const form = document.getElementById("form");
const instances = {};
for (const s of setups) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  wrap.innerHTML = `<label class="label" for="${s.id}">${s.label}</label><input id="${s.id}" name="${s.id}" type="tel" autocomplete="tel">`;
  form.insertBefore(wrap, form.querySelector("button[type=submit]"));
  const input = wrap.querySelector("input");
  instances[s.id] = intlTelInput(input, { ...s.options, strictMode: false });
  if (s.hidden) {
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "phone_country";
    wrap.append(hidden);
    const sync = () => { const c = instances[s.id].getSelectedCountry(); hidden.value = c ? `${c.name} (+${c.dialCode})` : ""; };
    input.addEventListener("countrychange", sync);
    sync();
  }
}
window.__state = () => Object.fromEntries(Object.entries(instances).map(([id, iti]) => [id, {
  hidden: id === "phone_hidden_country" ? document.querySelector("input[name=phone_country]").value : undefined,
  e164: iti.getNumber(), country: iti.getSelectedCountry()?.iso2, dial: iti.getSelectedCountry()?.dialCode,
  shown: document.getElementById(id).value, valid: iti.isValidNumber(),
}]));
