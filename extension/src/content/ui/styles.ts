// Stylesheet of the closed shadow root. Brand: forest #173F36 surface, ivory #F3F3EA text, mint #A8E4CE accent, sand #D6B782.
// Rules: px units only (the page's root font size must not leak in), logical properties, >= 4.5:1 text contrast in every
// tone, one 120 ms fade as the only entrance motion, no motion at all under prefers-reduced-motion.
// The chip, toasts, prompt cards and the panel header are always dark; only the panel body follows prefers-color-scheme.

export const STYLES = `
*, *::before, *::after { box-sizing: border-box; }
/* The host lives in the top layer, so a page that styles every ::backdrop would tint or blur the whole viewport. Important
   declarations of the inner (shadow) context beat the page's, so this holds even against !important. */
:host::backdrop { background: transparent !important; backdrop-filter: none !important; -webkit-backdrop-filter: none !important; display: none !important; }
.ui {
  --forest: #173F36; --ivory: #F3F3EA; --mint: #A8E4CE; --sand: #D6B782;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, system-ui, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --bg: #173F36; --ink: #F3F3EA; --muted: #B7C8BF; --line: rgba(243, 243, 234, 0.2); --raised: rgba(243, 243, 234, 0.09);
  --btn-bg: #A8E4CE; --btn-ink: #173F36; --btn-hover: #BFEBDC; --focus: #A8E4CE; --link: #A8E4CE;
  --c-verified: #86E3AC; --c-inferred: #F4CA6B; --c-draft: #92C6FF; --c-locked: #C4CEC9; --c-neutral: #C4CEC9; --c-busy: #92C6FF; --c-error: #FFA89B;
  --b-verified: rgba(134, 227, 172, 0.14); --b-inferred: rgba(244, 202, 107, 0.14); --b-draft: rgba(146, 198, 255, 0.14);
  --b-locked: rgba(196, 206, 201, 0.12); --b-neutral: rgba(196, 206, 201, 0.12); --b-busy: rgba(146, 198, 255, 0.14); --b-error: rgba(255, 168, 155, 0.14);
  --shadow: 0 10px 30px rgba(0, 0, 0, 0.32), 0 1px 3px rgba(0, 0, 0, 0.28);
  /* custom properties are the one thing \`all: initial\` on the host does not reset: a page that defines --tone must not leak in */
  --tone: initial; --tone-bg: initial;
  color-scheme: dark;
  position: fixed; inset: 0; pointer-events: none;
  font: 400 13px/1.4 var(--font); color: var(--ink); text-align: start; letter-spacing: normal; text-transform: none;
  -webkit-text-size-adjust: 100%; text-size-adjust: 100%; -webkit-font-smoothing: antialiased;
}
/* panel body and footer: the only parts that follow the page's colour scheme */
.p-body, .p-foot, .p-more {
  --bg: #F3F3EA; --ink: #173F36; --muted: #445F57; --line: rgba(23, 63, 54, 0.2); --raised: rgba(23, 63, 54, 0.07);
  --btn-bg: #173F36; --btn-ink: #F3F3EA; --btn-hover: #24574B; --focus: #173F36; --link: #14503F;
  --c-verified: #19623F; --c-inferred: #7A4E00; --c-draft: #1D56A0; --c-locked: #4B5F58; --c-neutral: #4B5F58; --c-busy: #1D56A0; --c-error: #A5382A;
  --b-verified: rgba(25, 98, 63, 0.11); --b-inferred: rgba(122, 78, 0, 0.11); --b-draft: rgba(29, 86, 160, 0.10);
  --b-locked: rgba(75, 95, 88, 0.10); --b-neutral: rgba(75, 95, 88, 0.10); --b-busy: rgba(29, 86, 160, 0.10); --b-error: rgba(165, 56, 42, 0.10);
  background: var(--bg); color: var(--ink); color-scheme: light;
}
.p-foot { --bg: #E8E9DC; }
@media (prefers-color-scheme: dark) {
  .p-body, .p-more {
    --bg: #12342C; --ink: #F3F3EA; --muted: #B7C8BF; --line: rgba(243, 243, 234, 0.18); --raised: rgba(243, 243, 234, 0.08);
    --btn-bg: #A8E4CE; --btn-ink: #173F36; --btn-hover: #BFEBDC; --focus: #A8E4CE; --link: #A8E4CE;
    --c-verified: #86E3AC; --c-inferred: #F4CA6B; --c-draft: #92C6FF; --c-locked: #C4CEC9; --c-neutral: #C4CEC9; --c-busy: #92C6FF; --c-error: #FFA89B;
    --b-verified: rgba(134, 227, 172, 0.14); --b-inferred: rgba(244, 202, 107, 0.14); --b-draft: rgba(146, 198, 255, 0.14);
    --b-locked: rgba(196, 206, 201, 0.12); --b-neutral: rgba(196, 206, 201, 0.12); --b-busy: rgba(146, 198, 255, 0.14); --b-error: rgba(255, 168, 155, 0.14);
    color-scheme: dark;
  }
  .p-foot {
    --bg: #0F2D26; --ink: #F3F3EA; --muted: #B7C8BF; --line: rgba(243, 243, 234, 0.18); --raised: rgba(243, 243, 234, 0.08);
    --btn-bg: #A8E4CE; --btn-ink: #173F36; --btn-hover: #BFEBDC; --focus: #A8E4CE; --link: #A8E4CE;
    color-scheme: dark;
  }
}

.tone-verified { --tone: var(--c-verified); --tone-bg: var(--b-verified); }
.tone-inferred, .tone-warn { --tone: var(--c-inferred); --tone-bg: var(--b-inferred); }
.tone-draft { --tone: var(--c-draft); --tone-bg: var(--b-draft); }
.tone-locked { --tone: var(--c-locked); --tone-bg: var(--b-locked); }
.tone-neutral { --tone: var(--c-neutral); --tone-bg: var(--b-neutral); }
.tone-busy { --tone: var(--c-busy); --tone-bg: var(--b-busy); }
.tone-error { --tone: var(--c-error); --tone-bg: var(--b-error); }
.tone-ok { --tone: var(--c-verified); --tone-bg: var(--b-verified); }
.tone-info { --tone: var(--ink); --tone-bg: var(--raised); }

.is-hidden { display: none !important; }
.scale-probe { position: absolute; left: 0; top: 0; width: 100px; height: 1px; visibility: hidden; pointer-events: none; }
.sr {
  position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; border: 0; overflow: hidden;
  clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap;
}
.muted { color: var(--muted); }
.ic { flex: none; display: block; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer; text-align: inherit; border-radius: 0; }
a { color: inherit; }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
button[disabled] { cursor: default; }

.mark-arc { fill: #A8E4CE; }
.mark-dot { fill: #D6B782; }

/* ---- buttons ---- */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: 7px;
  border: 1px solid transparent; font: 600 12px/1 var(--font); white-space: nowrap; color: var(--ink); text-align: center;
}
.btn.primary { background: var(--btn-bg); color: var(--btn-ink); }
.btn.primary:hover { background: var(--btn-hover); }
.btn.ghost { border-color: var(--line); }
.btn.ghost:hover { background: var(--raised); }
.btn.small { height: 24px; padding: 0 8px; }
.btn.block { display: flex; width: 100%; height: 34px; font-size: 13px; }
.btn[disabled] { color: var(--muted); border-color: var(--line); background: var(--raised); }
.link {
  display: inline-flex; align-items: center; justify-content: flex-start; align-self: flex-start; gap: 4px; padding: 2px 0; font: 600 12px/1.3 var(--font); color: var(--link);
  text-decoration: underline; text-underline-offset: 2px; text-decoration-thickness: 1px;
}
.link:hover { text-decoration-thickness: 2px; }
.link .ic { transition: transform 120ms; }
.link.open .ic { transform: rotate(180deg); }
.x, .ibtn {
  display: inline-grid; place-items: center; flex: none; width: 24px; height: 24px; border-radius: 6px; color: var(--muted);
}
.x { margin-inline-start: auto; }
.x:hover, .ibtn:hover { background: var(--raised); color: var(--ink); }
.ibtn.small { width: 22px; height: 22px; }

/* ---- field chip ---- */
.chip-layer { position: fixed; inset: 0; pointer-events: none; z-index: 4; }
.chip {
  position: absolute; z-index: 1; left: 0; top: 0; display: flex; flex-direction: column; gap: 6px; width: max-content; min-width: 200px; max-width: 380px;
  padding: 8px 8px 8px 10px; background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-inline-start: 3px solid var(--tone, var(--line)); border-radius: 10px; box-shadow: var(--shadow); overflow: hidden;
  /* click-through: the chip never swallows a click meant for the page (often the next field). Only its real controls and
     the expanded draft (a scroller the reader asked for) take the pointer. */
  pointer-events: none;
}
.chip button, .chip a, .chip .value.open { pointer-events: auto; }
.chip.enter { animation: fade-in 120ms ease-out both; }
.chip.quiet { flex-direction: row; align-items: center; gap: 8px; padding: 7px 6px 7px 9px; min-width: 0; }
.chip.tight .sources, .chip.tight .detail, .chip.tight .meta, .chip.tight .regen, .chip.tight .note, .chip.tight .question { display: none; }
/* squeezed into a short viewport (200-400% zoom, a short iframe): the preview shrinks and the footer keeps its main hint
   and its action on ONE line, so Insert is never pushed out of the clipped box */
.chip.tight .value.preview { -webkit-line-clamp: 2; }
.chip.tight .foot { flex-wrap: nowrap; }
.chip.tight .hints { flex-wrap: nowrap; overflow: hidden; flex: 1 1 0; }
.chip.tight .hint:not([data-hint="tab"]):not([data-hint="enter"]) { display: none; }
.chip.tight.stale-draft .hints { display: none; }
.chip.tight .actions { flex: 0 1 auto; flex-wrap: wrap; justify-content: flex-end; min-width: 0; }
.row { display: flex; align-items: center; gap: 6px; min-width: 0; }
.head { min-height: 22px; }
.badge {
  display: inline-flex; align-items: center; gap: 5px; flex: none; padding: 2px 9px 2px 6px; border-radius: 999px;
  background: var(--tone-bg); color: var(--tone); font: 600 12px/16px var(--font); white-space: nowrap;
}
.pill { display: inline-flex; align-items: center; gap: 3px; flex: none; padding: 1px 8px 1px 5px; border-radius: 999px; font: 600 12px/16px var(--font); }
.pill.ok { background: var(--b-verified); color: var(--c-verified); }
.why { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 12px; }
.qicon { display: inline-flex; flex: none; color: var(--tone); }
.qtext { display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; gap: 1px; }
.qlabel { font: 600 12px/1.3 var(--font); color: var(--tone); }
.question { color: var(--muted); font-size: 12px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.qtext .btn { align-self: flex-start; margin-top: 4px; }
/* typing into the field: one line, the neighbours stay in view */
.chip.pill-mode { flex-direction: row; align-items: center; gap: 7px; padding: 5px 8px 5px 9px; min-width: 0; min-height: 30px; border-radius: 999px; }
.pill-text { display: flex; align-items: baseline; gap: 6px; flex: 1 1 auto; min-width: 0; overflow: hidden; white-space: nowrap; font: 600 13px/1.3 var(--font); }
.pill-trust { flex: none; color: var(--tone); }
.pill-value { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.chip.pill-mode .btn { flex: none; }
.chip.pill-mode .pill-key { flex: none; margin-inline-start: 2px; }
.qreason { font-size: 13px; line-height: 1.4; color: var(--ink); overflow-wrap: anywhere; }
.label { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.value { font-size: 14px; line-height: 1.4; font-weight: 600; color: var(--ink); overflow-wrap: anywhere; white-space: pre-line; }
.value.muted { color: var(--muted); font-weight: 500; font-size: 13px; }
.value .op { display: inline-block; margin-inline-end: 6px; font-size: 12px; font-weight: 500; color: var(--muted); }
.value.preview {
  font-size: 13px; line-height: 1.5; font-weight: 400; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 4; overflow: hidden;
}
.value.preview.open { display: block; -webkit-line-clamp: unset; max-height: min(260px, 40vh); overflow: auto; scrollbar-width: thin; }
.value.scrolls { -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 22px), transparent); mask-image: linear-gradient(to bottom, #000 calc(100% - 22px), transparent); }
.detail { font-size: 12px; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail.wrap { white-space: normal; overflow-wrap: anywhere; }
.note { display: flex; align-items: flex-start; gap: 6px; font-size: 12px; line-height: 1.4; color: var(--muted); }
.note .ic { margin-top: 1px; }
.note.warn { color: var(--c-inferred); }
.note.ok { color: var(--c-verified); }
.meta { justify-content: space-between; font-size: 12px; min-height: 18px; }
.link.push { margin-inline-start: auto; }
/* the keyboard action a button carries (Alt+1 ... Alt+7); the chip is never focusable, so these keys are its keyboard path */
kbd.k { min-width: 0; margin-inline-start: 3px; padding: 2px 4px; border-radius: 3px; font: 600 10px/1 var(--mono); text-decoration: none; }
.btn.primary kbd.k { color: var(--btn-ink); background: rgba(23, 63, 54, 0.12); border-color: rgba(23, 63, 54, 0.4); }
.sources { flex-wrap: wrap; gap: 3px 10px; font-size: 12px; }
.src { display: inline-flex; align-items: center; gap: 3px; color: var(--link); text-decoration: underline; text-underline-offset: 2px; text-decoration-thickness: 1px; max-width: 100%; }
.src.plain { color: var(--ink); text-decoration: none; }
.regen { flex-wrap: wrap; gap: 6px; }
/* another document: the link, and (open) one button per document under it */
.docs { flex-wrap: wrap; gap: 4px 10px; align-items: flex-start; }
.doc-list { display: flex; flex-direction: column; align-items: stretch; gap: 4px; flex: 1 1 100%; min-width: 0; }
.btn.doc { justify-content: flex-start; width: 100%; min-width: 0; }
.btn.doc .doc-name { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; text-align: start; }
.btn.doc .doc-ver { flex: none; color: var(--muted); font-weight: 500; }
.foot { flex-wrap: wrap; justify-content: space-between; gap: 6px 10px; }
.hints { display: flex; flex-wrap: wrap; gap: 4px 12px; min-width: 0; }
.hint { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; line-height: 1.3; color: var(--muted); white-space: nowrap; }
.hint[data-hint="tab"], .hint[data-hint="enter"] { color: var(--ink); }
.hint.dim { color: var(--muted); }
.hint.dim kbd { border-style: dashed; color: var(--muted); }
kbd {
  display: inline-block; min-width: 20px; padding: 3px 5px; border-radius: 4px; border: 1px solid var(--line); background: var(--raised);
  color: var(--ink); font: 600 11px/1 var(--mono); text-align: center;
}
kbd.glyph { padding: 1px 5px; font: 600 14px/1.15 var(--font); }
.actions { display: flex; align-items: center; gap: 6px; margin-inline-start: auto; }
.dwell { position: absolute; inset-inline: 0; bottom: 0; height: 2px; background: var(--raised); }
.dwell-bar { display: block; height: 100%; background: var(--tone); transform: scaleX(0); transform-origin: left; animation: dwell 500ms linear forwards; }
.pulse { display: inline-block; flex: none; width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 1.2s ease-in-out infinite; }

/* ---- the recommended option of a choice control ---- */
.opt-layer { position: absolute; inset: 0; z-index: 0; pointer-events: none; }
.opt-mark {
  position: absolute; border: 2px solid var(--mint); border-radius: 7px; pointer-events: none;
  box-shadow: 0 0 0 1px rgba(23, 63, 54, 0.75), inset 0 0 0 1px rgba(23, 63, 54, 0.75); animation: fade-in 120ms ease-out both;
}
.opt-check {
  position: absolute; top: -9px; inset-inline-end: -8px; display: grid; place-items: center; width: 16px; height: 16px; border-radius: 50%;
  background: #173F36; color: var(--mint); border: 1px solid rgba(243, 243, 234, 0.5);
}
.opt-mark.rtl .opt-check { inset-inline-end: auto; inset-inline-start: -8px; }

/* ---- persistent field markers ---- */
.marks-layer { position: fixed; inset: 0; pointer-events: none; z-index: 0; }
.field-mark {
  position: absolute; left: 0; top: 0; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%;
  background: #173F36; color: var(--tone); border: 1px solid rgba(243, 243, 234, 0.6); box-shadow: 0 1px 4px rgba(0, 0, 0, 0.4); pointer-events: none;
}
.field-mark.enter { animation: fade-in 120ms ease-out both; }

/* ---- dock: prompts above the panel ---- */
.dock {
  position: fixed; bottom: 16px; display: flex; flex-direction: column; gap: 8px; width: 340px; max-width: calc(100vw - 16px); max-height: calc(100vh - 32px);
  pointer-events: none; z-index: 1;
}
.dock-right { right: 16px; }
.dock-left { left: 16px; }
.prompts { display: flex; flex: none; flex-direction: column; gap: 8px; }
.panel-slot { display: flex; flex: 0 1 auto; flex-direction: column; min-height: 0; }
.dock-right .is-pill { align-self: flex-end; }
.dock-left .is-pill { align-self: flex-start; }
.card {
  display: flex; flex-direction: column; gap: 8px; padding: 12px; background: var(--bg); color: var(--ink); border: 1px solid var(--line);
  border-radius: 12px; box-shadow: var(--shadow); pointer-events: auto;
}
.card-title { font: 600 14px/1.3 var(--font); }
.ask-field { display: flex; flex-direction: column; gap: 2px; padding: 7px 9px; border-radius: 8px; background: var(--raised); }
.ask-label { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ask-value { font: 600 13px/1.4 var(--font); overflow-wrap: anywhere; }
.card-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.card-actions.spread { justify-content: space-between; }
.card-note { display: flex; align-items: flex-start; gap: 5px; font-size: 12px; line-height: 1.4; color: var(--muted); overflow-wrap: anywhere; }
.card-note.quote { font-style: italic; }
.card > .link { align-self: flex-start; }

/* ---- panel ---- */
.panel-host { display: flex; flex: 1 1 auto; flex-direction: column; min-height: 0; pointer-events: none; }
.panel {
  display: flex; flex: 0 1 auto; flex-direction: column; width: 340px; max-width: 100%; min-height: 0; max-height: min(640px, calc(100vh - 32px)); overflow: hidden;
  border: 1px solid rgba(243, 243, 234, 0.2); border-radius: 14px; box-shadow: var(--shadow); pointer-events: auto; background: #173F36; color: #F3F3EA;
}
.p-head { display: flex; align-items: center; gap: 8px; padding: 10px 8px 10px 12px; background: var(--bg); color: var(--ink); flex: none; }
.p-mark { display: inline-flex; flex: none; }
.p-title { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.p-job { font: 600 14px/1.3 var(--font); overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; }
.p-sub { font-size: 12px; line-height: 1.3; color: var(--muted); overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; }
.p-sub.strong { color: var(--c-inferred); font-weight: 600; }
.conn { display: inline-block; flex: none; width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--muted); background: transparent; }
.conn-ready { background: var(--c-verified); border-color: var(--c-verified); }
.conn-pairing, .conn-restoring, .conn-connecting { background: var(--c-inferred); border-color: var(--c-inferred); animation: pulse 1.4s ease-in-out infinite; }
.spinner {
  display: inline-block; flex: none; width: 16px; height: 16px; border-radius: 50%; border: 2px solid var(--line); border-top-color: var(--ink);
  animation: spin 900ms linear infinite;
}
.conn-proof_failed, .conn-error, .conn-ambiguous { background: var(--c-error); border-color: var(--c-error); }
.p-more { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--line); flex: none; }
.dock-row { display: flex; gap: 6px; }
.dock-row { flex-wrap: wrap; }
.dock-row .btn { flex: 1 1 120px; height: auto; min-height: 26px; padding-block: 4px; white-space: normal; }
.dock-row .btn[aria-pressed="true"] { background: var(--raised); border-color: var(--ink); }
/* no padding at the bottom of the scrolling body: a pinned block (Fill verified) must sit flush with it, and engines differ in whether sticky offsets
   count the scroller's padding. The last card brings the 12 px itself. */
.p-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 10px 12px 0; display: flex; flex-direction: column; gap: 9px; scrollbar-width: thin; }
.p-body > :last-child:not(.p-actions) { margin-bottom: 12px; }
.srow { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.stext { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.slabel { font: 500 13px/1.35 var(--font); }
.shint { font-size: 12px; line-height: 1.35; color: var(--muted); }
.switch {
  position: relative; flex: none; width: 36px; height: 20px; border-radius: 999px; border: 1px solid var(--muted); background: transparent;
}
.knob { position: absolute; top: 2px; inset-inline-start: 2px; width: 14px; height: 14px; border-radius: 50%; background: var(--muted); transition: transform 120ms; }
.switch[aria-checked="true"] { background: var(--btn-bg); border-color: var(--btn-bg); }
.switch[aria-checked="true"] .knob { background: var(--btn-ink); transform: translateX(16px); }
.switch[aria-checked="true"] .knob:dir(rtl) { transform: translateX(-16px); }

.calm { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: 10px; background: var(--raised); border: 1px solid var(--line); }
.calm-head, .note-card { display: flex; gap: 9px; align-items: flex-start; }
.calm-icon { display: inline-flex; flex: none; margin-top: 1px; color: var(--tone, var(--ink)); }
.calm-text, .note-text { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1 1 auto; align-items: flex-start; }
.calm-title { font: 600 14px/1.35 var(--font); overflow-wrap: anywhere; }
.calm-hint { font-size: 12px; line-height: 1.4; color: var(--muted); overflow-wrap: anywhere; }
.calm-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.pair-extra { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.pair-notes { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.code-box { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 4px 0 2px; }
.code {
  padding: 8px 16px 6px; border-radius: 10px; background: var(--bg); border: 1px solid var(--line); border-bottom: 3px solid #D6B782;
  font: 700 30px/1.15 var(--mono); letter-spacing: 0.16em; color: var(--ink); font-variant-numeric: tabular-nums; user-select: all;
}
.countdown { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.note-card { padding: 9px 6px 9px 10px; border-radius: 10px; background: var(--raised); border: 1px solid var(--line); }
.note-card .calm-actions { align-items: center; gap: 4px 12px; margin-top: 2px; }
/* the posting is already recorded: the one banner that has to be seen */
.dup { display: flex; flex-direction: column; gap: 10px; padding: 12px; border-radius: 10px; background: var(--tone-bg); border: 1px solid var(--tone); border-inline-start-width: 4px; }
.dup .calm-icon { color: var(--tone); }
.btn.menu { width: 100%; height: auto; min-height: 26px; padding: 4px 8px; justify-content: flex-start; white-space: normal; text-align: start; }
.steps { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
.steps li { display: flex; align-items: flex-start; gap: 8px; font-size: 13px; line-height: 1.4; }
.step-n { display: inline-grid; place-items: center; flex: none; width: 18px; height: 18px; margin-top: 1px; border-radius: 50%; background: var(--btn-bg); color: var(--btn-ink); font: 700 11px/1 var(--font); }
.step-t { min-width: 0; overflow-wrap: anywhere; }
/* a card that folds: its title is the button, the body goes away with it */
.note-card.review, .note-card.first { flex-direction: column; align-items: stretch; gap: 4px; padding: 8px 6px 9px 10px; }
.nc-head { display: flex; align-items: flex-start; gap: 9px; min-width: 0; }
.nc-head .calm-icon { margin-top: 3px; }
.nc-body { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; min-width: 0; padding-inline-end: 4px; }
.fold { display: flex; flex: 1 1 auto; align-items: flex-start; justify-content: space-between; gap: 6px; min-width: 0; padding: 2px 0; font: 500 13px/1.4 var(--font); color: var(--ink); text-align: start; }
.fold .ic { margin-top: 4px; transition: transform 120ms; }
.fold.open .ic { transform: rotate(180deg); }
.fold-t { min-width: 0; overflow-wrap: anywhere; }
/* the cards share one area of limited height: a pile of them scrolls inside it instead of pushing the form's list and Fill verified away */
.p-notices { display: flex; flex-direction: column; gap: 9px; flex: none; max-height: min(264px, 44vh); overflow-y: auto; margin: -2px; padding: 2px; scrollbar-width: thin; }
.p-notices > * { flex: none; }
/* the whole question is two lines of the row, and all of it in the tooltip and the row's name */
.review-list { display: flex; flex-direction: column; gap: 1px; align-self: stretch; }
.review-list .p-item { align-items: flex-start; }
.review-list .p-status { margin-top: 1px; }
.p-label.wrap { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; white-space: normal; overflow-wrap: anywhere; }
.review-more { padding: 2px 8px; }
.srow.perm { justify-content: flex-start; align-items: flex-start; gap: 9px; }
.srow.perm .calm-icon { color: var(--muted); }
/* "fill these answers?": what goes where, before anything is written */
.fill-preview { display: flex; flex-direction: column; gap: 8px; padding: 10px; border-radius: 10px; background: var(--raised); border: 1px solid var(--line); }
.fp-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; max-height: 184px; overflow-y: auto; scrollbar-width: thin; }
.fp-item { display: flex; flex-direction: column; min-width: 0; }
.fp-label { font-size: 12px; line-height: 1.3; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-value { font: 600 13px/1.35 var(--font); overflow: hidden; overflow-wrap: anywhere; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.help { display: flex; flex-direction: column; align-items: flex-start; gap: 9px; }
.keys { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; align-self: stretch; }
.keys li { display: grid; grid-template-columns: minmax(48px, max-content) 1fr; align-items: baseline; gap: 2px 10px; font-size: 12px; line-height: 1.4; }
.keys kbd { justify-self: start; white-space: nowrap; }
.key-t { min-width: 0; overflow-wrap: anywhere; }
.r-need { padding-inline-start: 22px; font-size: 12px; line-height: 1.4; overflow-wrap: anywhere; }
.note-card .calm-icon { color: var(--muted); }
.note-title { font: 500 13px/1.4 var(--font); overflow-wrap: anywhere; }

.p-research { display: flex; flex-direction: column; gap: 6px; padding: 9px 10px; border-radius: 10px; background: var(--raised); border: 1px solid var(--line); }
.r-head { display: flex; align-items: flex-start; gap: 8px; }
.r-head .pulse { margin-top: 5px; color: var(--c-draft); }
.r-lead { display: inline-flex; margin-top: 2px; color: var(--c-verified); flex: none; }
.p-research.st-failed .r-lead { color: var(--c-error); }
.p-research.st-needs_input .r-lead { color: var(--c-inferred); }
.r-text { font: 500 13px/1.4 var(--font); overflow-wrap: anywhere; }
.r-facts { display: flex; flex-wrap: wrap; gap: 4px 6px; padding-inline-start: 22px; }
.tag { display: inline-flex; padding: 1px 8px; border-radius: 999px; font: 600 12px/18px var(--font); background: var(--b-verified); color: var(--c-verified); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag.soft { background: var(--raised); color: var(--muted); font-weight: 500; }
.r-actions { display: flex; flex-wrap: wrap; gap: 0 14px; padding-inline-start: 22px; }
.r-sources { display: flex; flex-direction: column; gap: 6px; padding-inline-start: 22px; }
.r-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.r-item { display: flex; align-items: center; gap: 6px; font-size: 12px; line-height: 1.35; min-width: 0; }
.r-ic { display: inline-flex; flex: none; color: var(--muted); }
.r-item.st-ok .r-ic { color: var(--c-verified); }
.r-item.st-failed .r-ic { color: var(--c-error); }
.r-link { color: var(--link); text-decoration: underline; text-underline-offset: 2px; text-decoration-thickness: 1px; display: inline-flex; align-items: center; gap: 4px; min-width: 0; max-width: 100%; }
.r-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.r-link.plain { color: var(--muted); text-decoration: none; }
.small-text { font-size: 12px; }
.p-form { display: flex; flex-direction: column; gap: 6px; padding-inline-start: 22px; }
.input {
  width: 100%; height: 30px; padding: 0 9px; border-radius: 7px; border: 1px solid var(--muted); background: var(--bg); color: var(--ink); font: 400 13px/1 var(--font);
}
.input::placeholder { color: var(--muted); opacity: 1; }
.input[aria-invalid="true"] { border-color: var(--c-error); }
.form-actions { display: flex; gap: 6px; }
.err { font-size: 12px; line-height: 1.35; color: var(--c-error); }

.p-progress { display: flex; flex-direction: column; gap: 6px; }
.p-sum { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.sum-main { font: 600 13px/1.3 var(--font); font-variant-numeric: tabular-nums; }
.sum-sub { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.bar { height: 6px; border-radius: 999px; background: var(--raised); overflow: hidden; border: 1px solid var(--line); }
.bar-fill { display: block; height: 100%; width: 0; background: var(--c-verified); border-radius: 999px; }
.p-list { max-height: 168px; overflow-y: auto; margin: 2px -4px 0; padding: 2px 4px; scrollbar-width: thin; border-block: 1px solid var(--line); }
.p-sec { padding: 8px 8px 3px; font: 700 11px/1.2 var(--font); letter-spacing: 0.05em; text-transform: uppercase; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.p-item {
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 8px; border-radius: 7px; border-inline-start: 3px solid transparent;
  font-size: 13px; line-height: 1.3; color: var(--ink); text-align: start;
}
.p-item:hover { background: var(--raised); }
.p-item.current { background: var(--raised); border-inline-start-color: var(--ink); font-weight: 600; }
.p-status {
  display: inline-grid; place-items: center; flex: none; width: 16px; height: 16px; border-radius: 50%; border: 1.5px solid var(--muted); color: var(--muted);
}
.p-item.st-inserted .p-status { border-color: var(--tone, var(--c-verified)); color: var(--tone, var(--c-verified)); background: var(--tone-bg, var(--b-verified)); }
.p-item.st-edited .p-status, .p-item.st-filled .p-status, .p-item.st-skipped .p-status, .p-item.st-locked .p-status, .p-item.st-needs_you .p-status { border-color: transparent; }
.p-item.st-needs_you .p-status { color: var(--c-inferred); }
.p-label { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.p-item.st-filled .p-label, .p-item.st-skipped .p-label { color: var(--muted); }
.req { color: var(--muted); font-weight: 700; }
.p-actions { display: flex; flex-direction: column; gap: 8px; padding-bottom: 12px; }
/* Fill verified stays on screen however much is above it: the actions are pinned to the bottom of the scrolling body (a short window has no
   room to give away, and the question "fill these answers?" is a card of its own that takes the keyboard) */
@media (min-height: 560px) {
  .panel:not(.has-menu) .p-actions { position: sticky; bottom: 0; z-index: 1; margin: 0 -12px; padding: 9px 12px 12px; background: var(--bg); border-top: 1px solid var(--line); }
}
.p-actions > .shint { margin-top: -2px; }
.p-drafts { display: flex; align-items: center; gap: 8px; font: 500 13px/1.3 var(--font); color: var(--c-draft); }
.p-foot { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; padding: 8px 12px; border-top: 1px solid var(--line); flex: none; }
.links { display: flex; gap: 14px; }
.spend { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.pill-btn {
  display: inline-flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px 0 10px; border-radius: 999px; background: var(--bg); color: var(--ink);
  border: 1px solid var(--line); box-shadow: var(--shadow); pointer-events: auto;
}
.pill-btn:hover { background: #1F4E43; }
.pill-count { font: 600 13px/1 var(--font); font-variant-numeric: tabular-nums; }
.pill-dup { display: inline-grid; place-items: center; flex: none; color: var(--c-inferred); }

/* ---- toasts ---- */
.toasts { position: fixed; inset-inline: 0; bottom: 20px; display: flex; flex-direction: column; align-items: center; gap: 6px; pointer-events: none; z-index: 6; }
.toast-region { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.toast {
  display: flex; align-items: center; gap: 8px; max-width: min(380px, calc(100vw - 24px)); padding: 8px 14px 8px 10px; border-radius: 10px; background: var(--bg);
  color: var(--ink); border: 1px solid var(--line); box-shadow: var(--shadow); font: 500 13px/1.35 var(--font); animation: fade-in 120ms ease-out both;
}
.toast-ic { display: inline-flex; flex: none; color: var(--tone); }
.toast.has-action { padding-inline-end: 8px; }
.toast .btn { flex: none; pointer-events: auto; }
.p-batch { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; padding: 6px 8px 6px 10px; border-radius: 8px; background: var(--b-verified); color: var(--ink); font: 500 13px/1.3 var(--font); }
.toast-text { overflow-wrap: anywhere; }

/* ---- flash on the field ---- */
.flash-layer { position: fixed; inset: 0; pointer-events: none; z-index: 3; }
.flash {
  position: absolute; border: 2px solid var(--tone); border-radius: 7px; pointer-events: none;
  box-shadow: 0 0 0 1px rgba(23, 63, 54, 0.7), inset 0 0 0 1px rgba(23, 63, 54, 0.7), 0 0 14px 2px var(--tone-bg);
  animation: flash-run 900ms ease-out both;
}
.flash.rtl .flash-spark { inset-inline-end: auto; inset-inline-start: -8px; }
.flash-spark {
  position: absolute; top: -10px; inset-inline-end: -8px; display: grid; place-items: center; width: 20px; height: 20px; border-radius: 50%;
  background: #173F36; color: var(--tone); border: 1px solid rgba(243, 243, 234, 0.35);
}

@keyframes spin { to { transform: rotate(360deg); } }
@keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes pulse { 0%, 100% { opacity: 0.35; transform: scale(0.8); } 50% { opacity: 1; transform: scale(1); } }
@keyframes dwell { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes flash-run { 0% { opacity: 0; transform: scale(1.06); } 14% { opacity: 1; transform: scale(1); } 70% { opacity: 1; } 100% { opacity: 0; } }

@media (max-width: 420px) {
  .dock.dock-left, .dock.dock-right { left: 8px; right: 8px; bottom: 8px; width: auto; }
  .panel { width: 100%; }
  .toasts { bottom: auto; top: 12px; }
}
@media (max-height: 520px) {
  .p-list { max-height: 120px; }
}
/* 200-400% browser zoom leaves a window a few hundred pixels tall: keep the controls, trim the chrome around them */
@media (max-height: 400px) {
  .p-head { padding: 6px 8px 6px 12px; }
  .p-job { -webkit-line-clamp: 1; }
  .p-body { padding: 8px 12px 0; gap: 7px; }
  .p-foot { padding: 4px 12px; gap: 0 12px; }
  .p-list { max-height: 96px; }
  /* no room for a scroller inside a scroller: the cards take their height and the panel's body scrolls */
  .p-notices { max-height: none; overflow: visible; }
}
/* Windows high contrast / forced colours: the browser replaces our palette with the user's system colours, which flattens
   everything drawn only with background colours. Shapes get borders, fills that carry state use system colours. */
@media (forced-colors: active) {
  .ui { --shadow: none; }
  .chip, .card, .panel, .toast, .pill-btn, .calm, .note-card, .dup, .p-research, .code, .input, .p-more, .ask-field, .bar, .fill-preview { border: 1px solid CanvasText; }
  .chip { border-inline-start-width: 4px; }
  .btn:not(.link) { border: 1px solid ButtonText; }
  .btn[disabled] { border-color: GrayText; color: GrayText; }
  .badge, .pill, .tag, kbd { border: 1px solid CanvasText; }
  .bar-fill { forced-color-adjust: none; background: Highlight; }
  .dwell { forced-color-adjust: none; background: Canvas; border-block-start: 1px solid CanvasText; }
  .dwell-bar { forced-color-adjust: none; background: Highlight; opacity: 1; }
  .knob { forced-color-adjust: none; background: ButtonText; }
  .switch[aria-checked="true"] { forced-color-adjust: none; background: Highlight; border-color: Highlight; }
  .switch[aria-checked="true"] .knob { background: HighlightText; }
  .conn { forced-color-adjust: none; background: Canvas; border-color: CanvasText; }
  .conn-ready { background: Highlight; border-color: Highlight; }
  .pulse { forced-color-adjust: none; background: CanvasText; }
  .spinner { forced-color-adjust: none; border-color: GrayText; border-top-color: CanvasText; }
  .field-mark { forced-color-adjust: none; background: Canvas; color: CanvasText; border: 2px solid Highlight; box-shadow: none; }
  .flash { forced-color-adjust: none; border-color: Highlight; box-shadow: none; }
  .flash-spark { forced-color-adjust: none; background: Canvas; color: CanvasText; border: 2px solid Highlight; }
  .opt-mark { forced-color-adjust: none; border-color: Highlight; box-shadow: none; }
  .opt-check { forced-color-adjust: none; background: Canvas; color: CanvasText; border: 2px solid Highlight; }
  .step-n { forced-color-adjust: none; background: Canvas; color: CanvasText; border: 1px solid CanvasText; }
  .pill-dup { forced-color-adjust: none; color: CanvasText; }
  .p-item { border-inline-start-color: Canvas; }
  .p-item.current { border-inline-start-color: Highlight; }
  .mark-arc { fill: CanvasText; }
  .mark-dot { fill: Highlight; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
  .dwell-bar { transform: scaleX(1); opacity: 0.55; }
  .pulse, .conn-pairing, .conn-restoring, .conn-connecting { opacity: 1; transform: none; }
}
@media print { .ui { display: none !important; } }
`;
