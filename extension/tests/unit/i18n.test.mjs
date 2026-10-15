import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { STRINGS, languageFromPrefs, resolveLanguage, t } from "../../src/shared/i18n-core.ts";
import { defaultPrefs } from "../../src/shared/storage.ts";

const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

test("Spanish covers every English string with the same placeholders", () => {
  const english = Object.keys(STRINGS.en).sort();
  assert.deepEqual(Object.keys(STRINGS.es).sort(), english);
  for (const key of english) {
    assert.ok(STRINGS.en[key].trim().length > 0, `en ${key}`);
    assert.ok(STRINGS.es[key].trim().length > 0, `es ${key}`);
    assert.deepEqual(placeholders(STRINGS.es[key]), placeholders(STRINGS.en[key]), key);
    assert.doesNotMatch(STRINGS.en[key] + STRINGS.es[key], /\b(TODO|FIXME)\b|[Ll]orem ipsum/, key);
  }
});

test("language resolution: explicit choice wins, auto follows the browser, unknown means English", () => {
  assert.equal(resolveLanguage("es", "en-US"), "es");
  assert.equal(resolveLanguage("en", "es-ES"), "en");
  assert.equal(resolveLanguage("auto", "es-MX"), "es");
  assert.equal(resolveLanguage("auto", "es"), "es");
  assert.equal(resolveLanguage("auto", "fr-FR"), "en");
  assert.equal(resolveLanguage(undefined, undefined), "en");
  assert.equal(resolveLanguage("klingon", "es"), "es");
});

test("the app's explicit language beats the extension's own; auto in the app defers to it", () => {
  const prefs = { ...defaultPrefs(), language: "en" };
  assert.equal(languageFromPrefs(prefs, "es-ES"), "en");
  assert.equal(languageFromPrefs({ ...prefs, settings: { language: "es" } }, "en-US"), "es");
  assert.equal(languageFromPrefs({ ...prefs, settings: { language: "auto" } }, "es-ES"), "en");
  assert.equal(languageFromPrefs(defaultPrefs(), "es-ES"), "es");
});

test("t() fills placeholders, leaves unknown ones alone and falls back to English", () => {
  assert.equal(t("en", "conn.ready.detail", { version: "1.2", port: 47615 }), "Meridian 1.2, port 47615");
  assert.equal(t("es", "conn.ready.detail", { version: "1.2", port: 47615 }), "Meridian 1.2, puerto 47615");
  assert.equal(t("en", "conn.pairing.expires", {}), "Expires in {seconds} s");
  assert.equal(t("en", "conn.pairing.expires"), "Expires in {seconds} s");
  assert.equal(t("xx", "app.name"), "Meridian Assistant");
});

const localeFile = (lang) => JSON.parse(readFileSync(new URL(`../../_locales/${lang}/messages.json`, import.meta.url), "utf8"));

test("the Spanish name of the product is 'Asistente de Meridian' everywhere the worker, the options page and the manifest show it", () => {
  const es = localeFile("es");
  assert.equal(es.extName.message, "Asistente de Meridian");
  assert.equal(es.extDescription.message, "Te ayuda a rellenar tus candidaturas, campo a campo, con tu perfil privado de Meridian. Nunca envía nada.");
  assert.equal(es.actionTitle.message, "Asistente de Meridian: ayuda con esta candidatura");
  assert.equal(STRINGS.es["app.name"], "Asistente de Meridian");
  assert.equal(STRINGS.es["action.title"], es.actionTitle.message, "the toolbar tooltip and the manifest agree");
  for (const [key, text] of Object.entries(STRINGS.es)) assert.doesNotMatch(text, /Asistente Meridian/, key);
  for (const text of Object.values(es).map((entry) => entry.message)) assert.doesNotMatch(text, /Asistente Meridian/);
  const hint = readFileSync(new URL("../../src/hint/index.ts", import.meta.url), "utf8");
  assert.match(hint, /open: "Asistente de Meridian"/);
  assert.doesNotMatch(hint, /Asistente Meridian/);
  const english = localeFile("en");
  assert.deepEqual(Object.keys(es).sort(), Object.keys(english).sort(), "same keys in both locales");
});

test("Spanish wording of the connection and permission texts (informal tú, Meridian's menu names stay in English)", () => {
  const es = STRINGS.es;
  assert.equal(es["app.tagline"], "Te ayuda a rellenar candidaturas, campo a campo. Nunca envía nada.");
  assert.equal(es["conn.ready.client"], "Conectado como {name}");
  assert.equal(es["conn.disabled"], "El asistente del navegador está desactivado en Meridian (Settings → Browser assistant).");
  assert.match(es["welcome.step3.body"], /\(fíjalo desde el menú con forma de pieza de puzle\)/);
  assert.equal(es["perm.safari"], "En Safari, abre Ajustes › Extensiones › Asistente de Meridian y elige «Permitir siempre en todos los sitios web».");
  assert.equal(es["diag.note"], "Es seguro compartirlo: no contiene tokens, contenido de páginas ni respuestas.");
  assert.equal(es["action.title.on"], "El Asistente de Meridian está activo. Pulsa para desactivarlo.");
  assert.equal(es["action.title.attention"], "El Asistente de Meridian está activo, pero Meridian necesita tu atención. Pulsa para ver los detalles.");
  // what Disconnect really does now (it revokes in Meridian when it can): the text must not claim it only forgets locally
  assert.equal(es["conn.disconnect.note"], "Esto desconecta este navegador y lo quita de la lista de navegadores conectados de Meridian.");
  // the desktop app is English: its menu path is quoted the same way in both languages, never translated
  for (const lang of ["en", "es"]) {
    assert.match(STRINGS[lang]["conn.disabled"], /\(Settings → Browser assistant\)/, lang);
    assert.match(STRINGS[lang]["conn.disconnected.local"], /\(Settings → Browser assistant\)/, lang);
  }
  // register: informal singular everywhere ("tú"), never "usted", "pulse" or "puede" addressing the user
  for (const [key, text] of Object.entries(es)) assert.doesNotMatch(text, /\b(pulse|escriba|introduzca|conecte|abra|usted|su navegador)\b/i, key);
  // the button says Connect, so the state does too (not "pair")
  assert.doesNotMatch(es["conn.ready.client"], /[Ee]mparej/);
  assert.equal(STRINGS.en["conn.ready.client"], "Connected as {name}");
});

test("SEC-13: the privacy copy says what stays on this Mac and exactly what can leave it", () => {
  for (const lang of ["en", "es"]) {
    const copy = STRINGS[lang];
    const all = [1, 2, 3, 4, 5, 6, 7].map((n) => copy[`privacy.${n}`]).concat(copy["welcome.step1.body"], copy["welcome.tip"]).join("\n");
    // the old absolute claims: false, because Meridian reads employer sites, may search the web and may call the writing model
    assert.doesNotMatch(all, /everything stays on this mac|never to the internet|nunca con internet|todo se queda en este mac|never contacts? the internet/i, lang);
    assert.doesNotMatch(all, /\bonly fills the field you are on\b|\bsolo rellena el campo en el que estás\b/i, `${lang}: Fill verified fills many fields`);
    // the five facts, in each language: profile stays here; the extension talks to Meridian only; Meridian reads public pages and may
    // search by company name; the writing model is the part of the information that can leave; typed text goes only on a save
    assert.match(all, /127\.0\.0\.1/, lang);
    assert.match(all, /Settings → Browser assistant/, `${lang}: where the search is controlled`);
    assert.match(all, /Settings → AI & providers/, `${lang}: where the writing model is chosen`);
  }
  const en = STRINGS.en;
  assert.match(en["privacy.1"], /profile, your answers and the corrections you save stay on this Mac/);
  assert.match(en["privacy.1"], /talks only to Meridian on this Mac \(127\.0\.0\.1\) and never contacts a website itself/);
  assert.match(en["privacy.2"], /Fill verified/);
  assert.match(en["privacy.4"], /sent to Meridian only when you choose to save it/);
  assert.match(en["privacy.6"], /Meridian itself \(not the extension\) reads public pages of the employer's own website/);
  assert.match(en["privacy.6"], /only the company name to a public search engine/);
  assert.doesNotMatch(en["privacy.6"], /if you allow/i, "the search is on unless switched off: the copy must not call it opt-in");
  assert.match(en["privacy.7"], /one part of your information that can leave this Mac/);
  assert.match(en["privacy.7"], /writing model you chose/);
  assert.match(en["privacy.7"], /the question, the job description and the verified facts needed to answer/);
  assert.match(en["privacy.7"], /runs on this Mac sends nothing out/);
  const es = STRINGS.es;
  assert.match(es["privacy.1"], /se quedan en este Mac/);
  assert.match(es["privacy.2"], /Rellenar los verificados/);
  assert.match(es["privacy.4"], /solo cuando decides guardarlo/);
  assert.match(es["privacy.6"], /Meridian mismo \(no la extensión\) quien lee páginas públicas/);
  assert.match(es["privacy.6"], /solo el nombre de la empresa a un buscador público/);
  assert.match(es["privacy.7"], /Lo único de tu información que puede salir de este Mac/);
  assert.match(es["privacy.7"], /la pregunta, la descripción del puesto y los datos verificados necesarios para responder/);
  assert.match(es["privacy.7"], /se ejecuta en este Mac no envía nada fuera/);
});

test("DEV-01 and LS-01: the diagnostics page says how to resolve two listeners and two copies of Meridian", () => {
  for (const lang of ["en", "es"]) {
    assert.match(STRINGS[lang]["diag.ambiguous"], /MERIDIAN_BRIDGE=0 make backend/, lang);
    assert.match(STRINGS[lang]["diag.ambiguous"], /make backend/, lang);
    assert.match(STRINGS[lang]["diag.copies"], /meridian:\/\//, lang);
    assert.match(STRINGS[lang]["diag.copies"], /Applications|Aplicaciones/, lang);
  }
  assert.match(STRINGS.en["conn.ambiguous"], /development copy/);
  assert.match(STRINGS.es["conn.ambiguous"], /copia de desarrollo/);
});

test("UX-12 and CPL-18: the options page lists every key and the colours, and the first-use text says where to click", () => {
  const en = STRINGS.en;
  for (const [key, label] of [["tab", "Tab"], ["shifttab", "Shift + Tab"], ["enter", "Enter"], ["esc", "Esc"], ["undo", "{alt} + Z"], ["rewrite", "{alt} + R"], ["panel", "{alt} + Shift + M"]]) {
    assert.equal(en[`help.k.${key}`], label);
    assert.ok(en[`help.d.${key}`].length > 20, key);
  }
  for (const tone of ["verified", "inferred", "draft", "grey"]) assert.ok(en[`help.c.${tone}`] && en[`help.d.${tone}`], tone);
  assert.match(en["help.d.start"], /chrome:\/\/extensions\/shortcuts/);
  assert.match(en["welcome.step3.body"], /click the first field/);
  assert.match(en["welcome.step3.shortcut"], /\{keys\}/);
  assert.match(STRINGS.es["welcome.step3.body"], /haz clic en el primer campo/);
  // what each label says on the chip (content/i18n.ts) is what the colour legend says
  const chip = readFileSync(new URL("../../src/content/i18n.ts", import.meta.url), "utf8");
  assert.match(chip, /"chip\.trust\.verified": "Verified"/);
  assert.match(chip, /"chip\.trust\.inferred": "Check this"/);
  assert.match(chip, /"key\.undo": "Undo"/);
  assert.equal(en["help.c.verified"], "Verified");
  assert.equal(en["help.c.inferred"], "Check this");
});

test("CPL-18/UX-02: the chip's numbered actions are listed with the chord that types nothing, and the start key says what Safari lacks", () => {
  for (const lang of ["en", "es"]) {
    const copy = STRINGS[lang];
    // Option+digit types a character on a Mac, so the chord is Control+Option there and Alt+Shift elsewhere (content/ui/ctx.ts chipKeyLabel)
    assert.match(copy["help.k.chip.mac"], /^Control \+ (Option|Opción) \+ 1 (to|a) 7$/, lang);
    assert.match(copy["help.k.chip.other"], /^Alt \+ (Shift|Mayús) \+ 1 (to|a) 7$/, lang);
    assert.ok(copy["help.d.chip"].length > 40, lang);
    assert.match(copy["help.d.start.unset"], /\{default\}/, `${lang}: says what the default shortcut was`);
    assert.match(copy["help.d.start.unset"], /chrome:\/\/extensions\/shortcuts/, lang);
    assert.doesNotMatch(copy["help.d.start.safari"], /chrome:\/\//, `${lang}: Safari has no such page`);
    assert.match(copy["help.d.start.safari"], /Safari/, lang);
  }
  const chipCode = readFileSync(new URL("../../src/content/ui/ctx.ts", import.meta.url), "utf8");
  assert.match(chipCode, /chipKeyLabel[\s\S]{0,160}⌃⌥[\s\S]{0,80}Alt\+Shift\+/, "the options page describes the chord the content script really uses");
  // the other keys the content script really handles (keyboard.ts): Alt+Shift+M switches into and out of the panel, Alt+Shift+Z is Undo all
  const keyboard = readFileSync(new URL("../../src/content/keyboard.ts", import.meta.url), "utf8");
  assert.match(keyboard, /altShift\(e\)[^\n]*altLetter\(e, "m"\)/, "the panel switch is Alt+Shift+M");
  assert.match(keyboard, /altShift\(e\)[^\n]*altLetter\(e, "z"\)[\s\S]{0,240}undoBatch/, "Undo all is Alt+Shift+Z");
  for (const lang of ["en", "es"]) {
    assert.match(STRINGS[lang]["help.k.undoall"], /^\{alt\} \+ (Shift|Mayús) \+ Z$/, lang);
    assert.match(STRINGS[lang]["help.k.panel"], /^\{alt\} \+ (Shift|Mayús) \+ M$/, lang);
    assert.ok(STRINGS[lang]["help.d.undoall"].length > 40 && STRINGS[lang]["help.d.arrows"].length > 40, lang);
  }
  // the key rows follow the platform: Option on a Mac, Alt elsewhere, in both languages
  assert.equal(t("en", "help.k.undo", { alt: "Option" }), "Option + Z");
  assert.equal(t("en", "help.k.panel", { alt: "Alt" }), "Alt + Shift + M");
  assert.equal(t("es", "help.k.rewrite", { alt: "Option" }), "Option + R");
});

test("CPL-20: the CV copy names the order the assistant really picks in, and where each choice is made", () => {
  for (const lang of ["en", "es"]) {
    const copy = STRINGS[lang];
    assert.match(copy["docs.body"], /Settings → Automation → Default CV/, lang);
    assert.doesNotMatch(copy["docs.body"], /Settings → Default CV/, `${lang}: that menu does not exist`);
    assert.ok(copy["docs.list"].length > 10, lang);
  }
  const body = STRINGS.en["docs.body"];
  // backend/jobagent/extension/documents_pick.py order_cvs: attached here, written for this job, the Settings choice, the newest base CV
  const order = [/you chose for that application/, /Meridian wrote for that job/, /your default CV/, /newest approved CV/].map((pattern) => body.search(pattern));
  assert.ok(order.every((at) => at >= 0), "every step of the order is named");
  assert.deepEqual([...order].sort((a, b) => a - b), order, "in the order the backend uses");
  assert.match(body, /cover letter only when Meridian wrote one for that job and you approved it/);
});

test("CPL-18/UX-02: the desktop app's 'How it works' lists the chords the content script really implements, in both languages", () => {
  const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
  const ctx = source("../../src/content/ui/ctx.ts");
  const util = source("../../src/content/util.ts");
  const keyboard = source("../../src/content/keyboard.ts");
  const copy = source("../../../frontend/src/extension-copy.ts");
  const pick = (pattern, where, what) => {
    const found = pattern.exec(where);
    assert.ok(found, `cannot read ${what} from the content script: this test must follow a change of its shape`);
    return found;
  };
  // what the code does (the glyphs Mac users read, the words everyone else reads, the letters, the chip count)
  const alt = pick(/export function altLabel\(mac: boolean\): string \{\s*return mac \? "([^"]+)"/, ctx, "the Option glyph")[1];
  const shift = pick(/export function shiftLabel\([^)]*\): string \{\s*return mac \? "([^"]+)"/, ctx, "the Shift glyph")[1];
  const chip = pick(/export function chipKeyLabel\(mac: boolean, n: number\): string \{\s*return mac \? `([^`$]+)\$\{n\}` : `([^`$]+)\$\{n\}`;/, ctx, "the chip chord");
  const start = pick(/export function activationShortcut\(mac: boolean\): string \| null \{[\s\S]*?return mac \? "([^"]+)" : "([^"]+)"/, ctx, "the start shortcut");
  const count = Number(pick(/export function chipActionNumber\([^)]*\bmax = (\d+)\)/, util, "the number of chip actions")[1]);
  const shiftLetters = [...keyboard.matchAll(/altShift\(e\) && altLetter\(e, "(\w)"\)/g)].map((match) => match[1].toUpperCase()).sort();
  assert.deepEqual(shiftLetters, ["M", "Z"], "Alt+Shift+M (panel) and Alt+Shift+Z (undo all) are the Alt+Shift chords");
  pick(/altLetter\(e, "r"\)\) return this\.onRegenerate/, keyboard, "Alt+R (write the long answer again)");
  pick(/altLetter\(e, "z"\) && this\.host\.hasUndo\(\)/, keyboard, "Alt+Z (undo the last insertion)");
  assert.equal(count, 7);
  // what the desktop app says: each language's rows of the howTo block, key label (spaces removed) -> sentence
  const blocks = copy.split("howTo: {").slice(1).map((block) => block.slice(0, block.indexOf("more:")));
  assert.equal(blocks.length, 2, "an English and a Spanish block");
  const rows = (block) => new Map([...block.matchAll(/\["((?:[^"\\]|\\.)+)", "((?:[^"\\]|\\.)+)"\]/g)].map((row) => [row[1].replace(/\s+/g, ""), row[2]]));
  for (const [lang, block] of [["en", blocks[0]], ["es", blocks[1]]]) {
    const keys = rows(block);
    for (const wanted of [`${alt}Z`, `${alt}R`, `${alt}${shift}M`, `${alt}${shift}Z`, start[1]]) {
      assert.ok(keys.has(wanted), `${lang}: a row for ${wanted} (have ${[...keys.keys()].join(" ")})`);
    }
    const chipRow = [...keys].find(([label]) => label.startsWith(chip[1]));
    assert.ok(chipRow, `${lang}: a row for the chip chord ${chip[1]}`);
    assert.match(chipRow[0], new RegExp(`^${chip[1]}1(to|a)${count}$`), lang);
    // the other systems' chord is named in the same row, with its own word for Shift
    assert.match(chipRow[1].replace(/\s+/g, ""), new RegExp(`Alt\\+(Shift|Mayús)\\+1(to|a)${count}`), `${lang}: ${chip[2]}1 to ${count} is named for other systems`);
    assert.equal(chip[2], "Alt+Shift+", "Alt+Shift+N is the chord everywhere but on a Mac");
    // the start key is the browser's default and says so (Chrome only: Safari has no such shortcut)
    assert.match(keys.get(start[1]), lang === "en" ? /Chrome only, and only the default/ : /Solo en Chrome, y solo el atajo predeterminado/, lang);
  }
  // the manifest's suggested key is the one the text calls the default
  const manifest = JSON.parse(source("../../manifest.template.json"));
  const suggested = manifest.commands._execute_action.suggested_key;
  assert.equal(suggested.mac.replace("Command", "⌘").replace("Shift", "⇧").replaceAll("+", ""), start[1], "the Mac default");
  assert.equal(suggested.default, start[2], "the default elsewhere");
});

test("every connection phase the options page can show has a headline in both languages (a new phase cannot ship without one)", () => {
  const source = readFileSync(new URL("../../src/shared/messages.ts", import.meta.url), "utf8");
  const union = /export type ConnectionPhase =([\s\S]*?);\n/.exec(source)[1];
  const phases = [...union.matchAll(/\|\s*"([a-z_]+)"/g)].map((match) => match[1]).filter((phase) => phase !== "pairing"); // pairing says "waiting for you"
  assert.ok(phases.includes("unknown_listener") && phases.length >= 9, phases.join(","));
  for (const phase of phases) for (const lang of ["en", "es"]) assert.ok(STRINGS[lang][`conn.${phase}`], `${lang} conn.${phase}`);
  for (const key of ["conn.unknown_listener.hint", "conn.unknown_listener.confirm", "btn.connect_unknown", "btn.confirm_unknown", "btn.cancel_unknown"]) {
    for (const lang of ["en", "es"]) assert.ok(STRINGS[lang][key], `${lang} ${key}`);
  }
  assert.match(STRINGS.en["conn.unknown_listener.hint"], /\{port\}/);
  assert.doesNotMatch(STRINGS.es["conn.unknown_listener"], /revocad|desconectad/, "silence is not described as a revocation");
});
