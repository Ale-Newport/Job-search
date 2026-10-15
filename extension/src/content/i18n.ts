// Every user-visible string of the assistant (labels, hints, ARIA names, toasts) lives here, in English and Spanish.
// The Spanish dictionary is typed against the English one, so a missing key fails `tsc --noEmit`.
// Register: informal "tú", same vocabulary as docs/INICIO_RAPIDO.es.md ("candidatura", "Pulsa", "Rellena").
// Labels of the desktop app (it stays English) are quoted as they appear there, e.g. "Settings → AI" and the "Tracker".
// `{enter}` is the name of the Enter key, filled in from the platform ("Retorno" on a Mac, "Intro" elsewhere; see enterKeyName).
// Employer text, profile values and server messages are DATA: they are rendered as text nodes and never looked up here.

import type { Language } from "../shared/protocol";
import { detectMac } from "./ui/ctx";

export type Locale = "en" | "es";
export type Params = Record<string, string | number>;

const en = {
  // ---- field chip: trust labels ----
  "chip.trust.verified": "Verified",
  "chip.trust.inferred": "Check this",
  "chip.trust.draft": "AI draft — review it",
  "chip.trust.protected": "Protected answer",
  "chip.trust.consent": "Yours to decide",
  "chip.trust.sensitive": "Left blank",
  "chip.trust.busy": "Writing a draft",
  "chip.trust.busyLocal": "Checking",
  "chip.trust.needs": "Needs you",
  "chip.trust.notApplicable": "Not needed",
  "chip.trust.error": "Check this one",
  "chip.inserted": "Inserted",
  "chip.aria": "Suggestion for {label}",
  "chip.ariaNoLabel": "Suggestion for this field",
  "chip.announce.blocked": "No suggestion to insert. {reason}",
  "chip.announce.tab": "Press Tab to insert it and move on.",
  "chip.announce.enter": "Press {enter} to use it.",
  "chip.announce.conflict": "This field already has other text, so Tab will not overwrite it.",
  "chip.announce.busy": "Writing a draft for this question. Tab moves on and it will be ready when you come back.",
  "chip.announce.lead": "{trust}: {value}.",
  "chip.op.attach": "Attach file",
  "chip.op.select": "Choose",
  "chip.op.check": "Tick",
  "chip.review": "Double-check this before you send the form.",
  "chip.conflict": "This field already has other text. I won't overwrite it.",
  "chip.basedOn": "Sources",
  "chip.sourceLink": "{label} (opens in a new tab)",
  "chip.moreSources": "+{n}",
  "chip.lessSources": "Fewer",
  "mark.region": "Answers to double-check",
  "mark.inferred": "Inserted answer: check this",
  "mark.inferred.label": "{label}: inserted answer, check this",
  "mark.draft": "AI draft inserted: review it",
  "mark.draft.label": "{label}: AI draft inserted, review it",
  "mark.generic": "Inserted answer",
  "mark.generic.label": "{label}: inserted answer",

  // ---- field chip: why (server why.code) and reasons (needs.code and friends) ----
  "why.profile_fact": "From your verified profile",
  "why.derived": "Worked out from your profile",
  "why.policy": "Follows your saved preferences",
  "why.saved_answer": "From an answer you saved",
  "why.generated": "Drafted from your profile and this company",
  "why.consent": "Legal confirmations are always your call",
  "why.sensitive_blank": "Optional personal question",
  "why.not_applicable": "Doesn't apply after your earlier answer",
  "why.missing_fact": "Not in your profile yet",
  "why.format_error": "Doesn't fit this field's format",
  "why.no_matching_option": "No option matches your profile",
  "why.setup_required": "Needs a writing model",
  "why.budget": "Monthly AI budget reached",
  "why.model_error": "The writing model didn't answer",
  "why.validation_failed": "No draft I'd trust",
  "why.cv_attach": "Your approved CV",
  "reason.setup_required": "Long answers need a writing model. Set one up in Meridian (Settings → AI).",
  "reason.budget": "Monthly AI budget reached. Short answers still work.",
  "reason.model_error": "The writing model didn't respond. Try again in a moment.",
  "reason.validation_failed": "I couldn't write a draft I'm confident in. Answer this one yourself or try again.",
  "reason.missing_candidate_fact": "Not in your profile yet. Type it and I'll offer to remember it.",
  "reason.missing_fact": "Not in your profile yet. Type it and I'll offer to remember it.",
  "reason.format_error": "Your saved answer doesn't fit this field's format. Type it yourself.",
  "reason.no_matching_option": "None of the options matches your profile. Choose one yourself.",
  "reason.fill_failed": "I couldn't set this field safely, so I left it as it was. Check it yourself.",
  "reason.protected": "Personal question. I only fill it when you press {enter}.",
  "reason.protectedReady": "Personal answer. Tab won't insert it; press {enter} to use it.",
  "reason.readInReady": "No fact of your profile says this. Tab won't insert it; press {enter} to use it.",
  "reason.consent": "Consent and legal boxes are always yours to tick.",
  "reason.policy": "Your preferences say to leave this one to you.",
  "reason.sensitive_blank": "Optional personal question. I leave it blank unless you decide.",
  "reason.not_applicable": "Doesn't apply after your earlier answer.",
  "reason.generic": "I don't have a suggestion for this one.",

  // ---- field chip: generation status, keys, buttons ----
  "gen.QUEUED": "Waiting its turn. I draft long answers one at a time.",
  "gen.GENERATING": "Writing a draft from your profile and this company…",
  "gen.local": "Checking your profile with local AI on this Mac…",
  "gen.REGENERATING": "Writing a new version…",
  "gen.VALIDATING": "Checking the draft against your profile…",
  "gen.DONE": "Draft ready.",
  "gen.FAILED": "I couldn't write this draft.",
  "gen.CANCELLED": "Draft cancelled.",
  "gen.attempt": "Attempt {n}",
  "key.tab.insert": "Insert & next",
  "key.tab.next": "Next",
  "key.tab.keep": "Next (keeps your text)",
  "key.tab.drafting": "Next (draft keeps writing)",
  "key.shiftTab": "Back",
  "key.shift": "Shift",
  "key.enter.use": "Use this",
  "key.esc": "Hide",
  "key.regenerate": "Rewrite",
  "key.undo": "Undo",
  "btn.insert": "Insert",
  "btn.replace": "Replace",
  "btn.use": "Use this answer",
  "btn.undo": "Undo",
  "btn.draft": "Draft with AI",
  "btn.dismiss": "Hide suggestion (Esc)",
  "btn.showMore": "Show more",
  "btn.showLess": "Show less",
  "regen.label": "Rewrite this draft",
  "regen.again": "Again",
  "regen.shorter": "Shorter",
  "regen.longer": "Longer",
  "regen.different": "Different angle",

  // ---- toasts ----
  "toast.filled": "Inserted",
  "toast.filled.label": "Inserted: {label}",
  "toast.undone": "Undone",
  "toast.undone.label": "Undone: {label}",
  "act.undoAll": "Undo all",
  "toast.saved": "Saved in Meridian",
  "toast.saved.application": "Saved for this application",
  "toast.saved.reuse": "Saved for future applications",
  "toast.draft_ready": "Draft ready",
  "toast.draft_ready.label": "Draft ready: {label}",
  "toast.regenerating": "Writing a new version…",
  "toast.copied": "Copied",
  "toast.no_suggestion": "No suggestion for this field",
  "toast.paused": "Tab is back to normal",
  "toast.resumed": "Tab inserts suggestions again",
  "toast.connection_lost": "Lost contact with Meridian. I'll keep trying.",
  "toast.connection_back": "Back in touch with Meridian",
  "toast.error": "Something went wrong. Check the page before you carry on.",
  "toast.region": "Notifications",

  // ---- prompts ----
  "save.title": "Save this answer in Meridian?",
  "save.title.edited": "Save your change in Meridian?",
  "save.application": "This application",
  "save.reuse": "Reuse in future applications",
  "save.no": "No",
  "save.more": "More choices",
  "save.always": "Always save my answers",
  "save.never": "Never ask me",
  "save.onlyHere": "This kind of answer is only ever saved for this application.",
  "save.local": "Saved in Meridian, on this Mac.",
  "save.group": "Save this answer",
  "track.title": "Record this application in Meridian?",
  "track.body": "{title} at {company}",
  "track.evidence": "The page says: “{text}”",
  "track.note": "I never assume it went through. This is your call.",
  "track.record": "Record application",
  "track.dismiss": "Not now",
  "track.group": "Record this application",

  // ---- panel: chrome ----
  "panel.region": "Meridian Assistant",
  "panel.untitled": "This application",
  "panel.titleSeparator": "{company} · {title}",
  "panel.pill": "Meridian Assistant, {done} of {total} fields",
  "panel.pillNoForm": "Meridian Assistant",
  "panel.expand": "Open the Meridian panel",
  "panel.collapse": "Minimise the panel",
  "panel.more": "More options",
  "panel.job.applied": "Already recorded as applied",
  "panel.job.preparing": "In your Meridian Tracker",
  "panel.job.prospective": "Saved in Meridian",
  "conn.ready": "Connected to Meridian",
  "conn.no_bridge": "Meridian isn't open",
  "conn.unpaired": "Not connected yet",
  "conn.pairing": "Waiting for your approval",
  "conn.revoked": "Disconnected",
  "conn.proof_failed": "Connection refused for your safety",
  "conn.disabled": "Switched off in Meridian",
  "conn.restoring": "Meridian is restoring a backup",
  "conn.connecting": "Connecting to Meridian…",
  "conn.error": "Connection problem",
  "conn.ambiguous": "Connection paused for your safety",
  "conn.orphaned": "Needs a page reload",

  // ---- panel: calm states ----
  "calm.connecting": "Connecting to Meridian…",
  "calm.no_bridge": "Meridian isn't open.",
  "calm.no_bridge.hint": "Open it and I'll pick up where we left off. I also try again by myself every few seconds.",
  "calm.openMeridian": "Open Meridian",
  "calm.retryNow": "Try now",
  "calm.unpaired": "Connect this browser to Meridian.",
  "calm.unpaired.hint": "You type a short code once. Your profile and answers stay on this Mac, except the text Meridian sends to the writing model you chose.",
  "calm.unpaired.hint.port": "Meridian answered on port {port}. After you press Connect, it opens a window asking for a short code. Your profile and answers stay on this Mac, except the text Meridian sends to the writing model you chose.",
  "calm.connect": "Connect",
  "calm.pairing": "Type this code in the Meridian window",
  "calm.pairing.hint": "Meridian itself must open a small window asking for this code. Only continue if you just pressed Connect.",
  "calm.pairing.port": "Meridian answered on port {port}.",
  "calm.pairing.nowindow": "No Meridian window? Open Meridian from the menu bar, then try again. If none appears, press Cancel: something else on this Mac may be answering.",
  "calm.pairing.codeLabel": "Pairing code {digits}",
  "calm.pairing.expires": "Expires in {time}",
  "calm.pairing.expired": "That code expired.",
  "calm.pairing.new": "Get a new code",
  "calm.cancel": "Cancel",
  "calm.proof_failed": "Something on this Mac answered, but it isn't Meridian.",
  "calm.proof_failed.hint": "For your safety I won't send anything to it.",
  "calm.retry": "Try again",
  "calm.retrying": "Checking…",
  "calm.ambiguous": "More than one program on this Mac says it is Meridian.",
  "calm.ambiguous.hint": "To be safe, I send nothing to any of them (ports {ports}). Quit the extra one, then try again.",
  "calm.revoked": "Meridian disconnected this browser.",
  "calm.revoked.hint": "Connect again whenever you like.",
  "calm.connectAgain": "Connect again",
  "calm.disabled": "The browser assistant is switched off in Meridian.",
  "calm.disabled.hint": "Turn it on in Meridian under Settings → Browser assistant.",
  "calm.restoring": "Meridian is restoring a backup.",
  "calm.restoring.hint": "I'll carry on as soon as it's done.",
  "calm.error": "I couldn't talk to Meridian just now.",
  "calm.error.hint": "Nothing was changed on the page.",
  "calm.noForm": "Waiting for the application form…",
  "calm.noForm.hint": "Open the form and I'll pick it up.",
  "calm.profileEmpty": "Your Meridian profile has no verified facts yet.",
  "calm.profileEmpty.hint": "Add your details in Meridian and I'll use them here.",
  "calm.orphaned": "Meridian Assistant was updated.",
  "calm.orphaned.hint": "Reload this page to keep going. Save anything you typed first.",
  "calm.reload": "Reload page",
  "calm.update": "A newer Meridian Assistant is ready.",
  "calm.update.open": "Update in settings",
  "note.drafts": "Long answers need a writing model (Settings → AI).",
  "note.budget": "Monthly AI budget reached; short answers still work.",
  "note.crossOrigin": "This form lives in another site's frame.",
  "note.crossOrigin.hint": "Allow Meridian on all sites so I can see inside it.",
  "note.allowAll": "Allow all sites",
  "note.dismiss": "Dismiss this note",

  // ---- panel: research strip ----
  "research.region": "Company research",
  "research.queued": "Getting ready to research {company}…",
  "research.queued.noCompany": "Getting ready to research this company…",
  "research.running": "Researching {company} and the role…",
  "research.running.noCompany": "Researching the company and the role…",
  "research.done.full": "Researched {company} and the role",
  "research.done.job_only": "Read the job posting; no company site confirmed",
  "research.done.company_only": "Read the {company} website; the posting itself was thin",
  "research.done.minimal": "Little public information found; drafts will lean on your profile",
  "research.partial": "Found some context on {company}",
  "research.partial.noCompany": "Found some context on the company",
  "research.failed": "Couldn't research {company}; drafts use your profile and the job text",
  "research.failed.noCompany": "Couldn't research this company; drafts use your profile and the job text",
  "research.needs.company": "I'm not sure which company this is.",
  "research.needs.site": "I couldn't confirm {company}'s website. Add it?",
  "research.needs.site.noCompany": "I couldn't confirm the company's website. Add it?",
  "research.cancelled": "Research stopped.",
  "research.quality.full": "Role + company site",
  "research.quality.job_only": "Job posting only",
  "research.quality.company_only": "Company site only",
  "research.quality.minimal": "Thin context",
  "research.show": "Show sources",
  "research.hide": "Hide sources",
  "research.noSources": "No sources yet.",
  "research.status.ok": "Used",
  "research.status.failed": "Couldn't load",
  "research.status.blocked": "Blocked by the site",
  "research.status.skipped": "Skipped",
  "research.status.unverified": "Not used: couldn't confirm it's the right company",
  "research.site": "Using {site}",
  "research.notThis": "Not this company?",
  "research.addSite": "Add the company website",
  "research.refresh": "Research again",
  "research.form.company": "Company name",
  "research.form.site": "Company website, e.g. acme.com",
  "research.form.go": "Research",
  "research.form.cancel": "Cancel",
  "research.form.siteInvalid": "That doesn't look like a website address.",

  // ---- panel: progress, actions, footer ----
  "progress.label": "Form progress",
  "progress.summary": "{done} of {total}",
  "progress.fields": "Fields",
  "progress.status.pending": "To do",
  "progress.status.inserted": "Inserted",
  "progress.status.edited": "Edited by you",
  "progress.status.filled": "Already filled",
  "progress.status.needs_you": "Needs you",
  "progress.status.locked": "Left for you",
  "progress.status.skipped": "Skipped",
  "progress.required": "required",
  "progress.section.other": "Other",
  "progress.goto": "Go to {label}",
  "act.fillVerified": "Fill verified ({n})",
  "act.fillVerified.hint": "Only verified answers, never personal or legal ones.",
  "act.fillVerified.none": "Nothing verified left to fill.",
  "act.keys.label": "Use Tab to insert & move on",
  "act.keys.hint.off": "Tab works the usual way. Switch on to insert with it.",
  "more.dock": "Panel position",
  "more.dock.left": "Dock left",
  "more.dock.right": "Dock right",
  "more.alwaysOn": "Start automatically on {site}",
  "more.alwaysOn.generic": "Start automatically on this site",
  "more.hideHint": "Hide the hint on {site}",
  "more.hideHint.generic": "Hide the hint on this site",
  "more.deactivate": "Turn the assistant off for this page",
  "footer.settings": "Settings",
  "footer.help": "Help",
  "footer.spend": "AI this month: {spent}",
  "footer.spendOf": "AI this month: {spent} of {limit}",
  "footer.nav": "Panel links",

  // ---- field chip: stale drafts, quiet file chips, already-inserted line (additive, wave 3) ----
  "chip.stale.research": "Written before the latest research. A rewrite will use what I found since.",
  "chip.stale.profile": "Your profile changed after this was written, so I won't insert it. Rewrite it first.",
  "chip.stale.company_rejected": "You said this isn't the right company, so I won't insert this draft. Rewrite it with the new research.",
  "chip.announce.stale": "This draft is out of date.",
  "btn.rewriteNew": "Rewrite with new research",
  "btn.rewriteProfile": "Rewrite with my updated profile",
  "chip.skip.label.file_unapproved": "CV not approved yet",
  "chip.skip.reason.file_unapproved": "Approve your CV in Meridian (Documents) and I'll attach it here.",
  "chip.skip.label.file_cover_letter": "Yours to upload",
  "chip.skip.reason.file_cover_letter": "I only attach your approved CV. Upload a cover letter yourself.",
  "chip.skip.label.file_none": "No CV in Meridian yet",
  "chip.skip.reason.file_none": "Import your CV in Meridian (Documents) and approve it, and I'll attach it here.",
  "chip.skip.label.generic": "Yours to upload",
  "chip.skip.reason.generic": "I only attach your approved CV, and only to a CV upload. Upload this one yourself.",
  "chip.alreadyInserted": "Already inserted",
  "chip.announce.already": "Already inserted.",

  // ---- screen-reader announcements (nothing is drawn) ----
  "announce.inserted": "Inserted {label}: {value}",
  "announce.inserted.novalue": "Inserted {label}",
  "announce.save": "Meridian asks: save this answer? Press {keys} to answer.",
  "announce.track": "Meridian asks: record this application? Press {keys} to answer.",

  // ---- panel: tracker menu items, duplicate banner, first use, review, key cheat sheet ----
  "more.track.save": "Save to tracker",
  "more.track.applied": "I applied: record this",
  "dup.applied.title": "You already applied to this job",
  "dup.applied.hint.since": "You applied on {date}. I won't insert anything until you continue.",
  "dup.applied.hint": "Meridian has it recorded as applied. I won't insert anything until you continue.",
  "dup.rejected.hint.since": "You applied on {date} and it was rejected. I won't insert anything until you continue.",
  "dup.rejected.hint": "Meridian has it recorded as rejected. I won't insert anything until you continue.",
  "dup.withdrawn.hint.since": "You applied on {date}, then withdrew it. I won't insert anything until you continue.",
  "dup.withdrawn.hint": "Meridian has it recorded as withdrawn. I won't insert anything until you continue.",
  "dup.ghosted.hint.since": "You applied on {date} and no answer came. I won't insert anything until you continue.",
  "dup.ghosted.hint": "Meridian has it recorded as applied, with no answer. I won't insert anything until you continue.",
  "dup.preparing.title": "This job is already in your Tracker",
  "dup.preparing.hint.since": "You started it on {date}. You can carry on from here.",
  "dup.preparing.hint": "You started an application for it in Meridian. You can carry on from here.",
  "dup.continue": "Continue anyway",
  "dup.close": "Turn off",
  "dup.pill.applied": "Already applied to this job",
  "dup.pill.preparing": "Already in your Tracker",
  "first.title": "How it works",
  "first.step1": "Click the first field of the form.",
  "first.step2": "Press Tab. I insert the suggestion and move you to the next question.",
  "first.step3": "Esc hides a suggestion; Shift+Tab goes back one field.",
  "first.shortcut": "Turn the assistant on or off with {keys} (default shortcut).",
  "first.keys": "See all keys",
  "first.dismiss": "Got it",
  "review.hint": "Take a look before you submit. Click one to jump to it.",
  "review.more": "+{n} more",
  "review.dismiss": "Dismiss this reminder",
  "review.region": "Before you submit",
  "help.region": "Keyboard shortcuts",
  "help.title": "Keyboard shortcuts",
  "help.back": "Back to the panel",
  "help.row.tab": "Insert the suggestion and move to the next field",
  "help.row.shiftTab": "Go back one field",
  "help.row.enter": "Use a personal answer (those never go in with Tab)",
  "help.row.esc": "Hide the suggestion for this field",
  "help.row.rewrite": "Rewrite a draft",
  "help.row.undo": "Undo the last insertion",
  "help.row.chip": "Suggestion actions, printed on its buttons: Replace, Shorter, Longer, Different angle, Show more, sources",
  "help.row.panel": "Move the keyboard into this panel",
  "help.row.toggle": "Turn the assistant on or off (default shortcut)",
  "research.privacy": "Meridian itself reads these public pages, from this Mac. Your profile and answers are never sent to them.",

  // ---- wave 4: rows of links, the full help, the saved-answer line ----
  "research.actions": "Research actions",
  "help.full": "Open full help",
  "help.row.arrows": "Move inside the field list and the rows of links",
  "help.row.undoAll": "Take back everything the last Fill verified inserted",
  "reason.duplicate": "You already applied to this job, so I'm not inserting anything. Choose Continue anyway in the panel to change that.",
  "chip.announce.duplicate": "Nothing will be inserted: you already applied to this job. Choose Continue anyway in the panel to change that.",
  "more.alwaysOn.permission": "Allow access to this site in the window that just opened.",
  "fill.preview.region": "Answers to fill",
  "fill.preview.more": "and {n} more",
  "act.fillConfirm": "Fill now",

  // ---- wave 5: Save to tracker on a sent application, interview / offer wording, the rule note, paused drafts, another CV ----
  "toast.already_recorded": "Already recorded in Meridian as applied",
  "dup.interview.hint.since": "You applied on {date} and it reached the interview stage. I won't insert anything until you continue.",
  "dup.interview.hint": "Meridian has it at the interview stage. I won't insert anything until you continue.",
  "dup.offer.hint.since": "You applied on {date} and received an offer. I won't insert anything until you continue.",
  "dup.offer.hint": "Meridian has an offer recorded for it. I won't insert anything until you continue.",
  "more.alwaysOn.rule": "A rule for {rule} still starts the assistant here. Remove it on the options page or in Meridian (Settings → Browser assistant).",
  "more.alwaysOn.rule.generic": "Another rule still starts the assistant here. Remove it on the options page or in Meridian (Settings → Browser assistant).",
  "note.draftsPaused": "Meridian paused automatic drafts on this page: ask for one with Draft with AI.",
  "cv.use": "Use another CV",
  "cv.use.letter": "Use another cover letter",
  "cv.menu": "Other documents Meridian can attach here",
  "cv.version": "version {n}",
  "cv.option": "Attach {name}",
} as const satisfies Record<string, string>;

const enPlural = {
  "chip.words": { one: "{n} word", other: "{n} words" },
  "chip.announce.draft": { one: "{n} word. Take a moment to read it, then press Tab to insert it.", other: "{n} words. Take a moment to read it, then press Tab to insert it." },
  "research.sources": { one: "{n} source", other: "{n} sources" },
  "progress.inserted": { one: "{n} inserted", other: "{n} inserted" },
  "act.drafts": { one: "{n} draft on its way", other: "{n} drafts on their way" },
  "toast.filled.count": { one: "{n} answer inserted", other: "{n} answers inserted" },
  "review.title": { one: "{n} required question is still empty", other: "{n} required questions are still empty" },
  "toast.undone.count": { one: "{n} answer taken back", other: "{n} answers taken back" },
  "fill.preview.title": { one: "Fill {n} answer?", other: "Fill {n} answers?" },
} as const satisfies Record<string, { one: string; other: string }>;

export type StringKey = keyof typeof en;
export type PluralKey = keyof typeof enPlural;
type Plural = { one: string; other: string };

const es: Record<StringKey, string> = {
  "chip.trust.verified": "Verificado",
  "chip.trust.inferred": "Revísalo",
  "chip.trust.draft": "Borrador con IA: revísalo",
  "chip.trust.protected": "Respuesta protegida",
  "chip.trust.consent": "La decides tú",
  "chip.trust.sensitive": "Lo dejo en blanco",
  "chip.trust.busy": "Escribiendo un borrador",
  "chip.trust.busyLocal": "Comprobando",
  "chip.trust.needs": "Te toca a ti",
  "chip.trust.notApplicable": "No hace falta",
  "chip.trust.error": "Compruébalo",
  "chip.inserted": "Insertado",
  "chip.aria": "Sugerencia para {label}",
  "chip.ariaNoLabel": "Sugerencia para este campo",
  "chip.announce.blocked": "No hay sugerencia para insertar. {reason}",
  "chip.announce.tab": "Pulsa Tab para insertarla y pasar al siguiente.",
  "chip.announce.enter": "Pulsa {enter} para usarla.",
  "chip.announce.conflict": "Este campo ya tiene otro texto, así que Tab no lo sobrescribirá.",
  "chip.announce.busy": "Estoy escribiendo un borrador para esta pregunta. Tab avanza y estará listo cuando vuelvas.",
  "chip.announce.lead": "{trust}: {value}.",
  "chip.op.attach": "Adjuntar archivo",
  "chip.op.select": "Elegir",
  "chip.op.check": "Marcar",
  "chip.review": "Revísalo bien antes de enviar el formulario.",
  "chip.conflict": "Este campo ya tiene otro texto. No lo sobrescribo.",
  "chip.basedOn": "Fuentes",
  "chip.sourceLink": "{label} (se abre en una pestaña nueva)",
  "chip.moreSources": "+{n}",
  "chip.lessSources": "Menos",
  "mark.region": "Respuestas por revisar",
  "mark.inferred": "Respuesta insertada: revísala",
  "mark.inferred.label": "{label}: respuesta insertada, revísala",
  "mark.draft": "Borrador con IA insertado: revísalo",
  "mark.draft.label": "{label}: borrador con IA insertado, revísalo",
  "mark.generic": "Respuesta insertada",
  "mark.generic.label": "{label}: respuesta insertada",

  "why.profile_fact": "De tu perfil verificado",
  "why.derived": "Deducido de tu perfil",
  "why.policy": "Según tus preferencias guardadas",
  "why.saved_answer": "De una respuesta que guardaste",
  "why.generated": "Redactado con tu perfil y esta empresa",
  "why.consent": "Las confirmaciones legales las decides tú",
  "why.sensitive_blank": "Pregunta personal opcional",
  "why.not_applicable": "No corresponde según tu respuesta anterior",
  "why.missing_fact": "Aún no está en tu perfil",
  "why.format_error": "No encaja con el formato de este campo",
  "why.no_matching_option": "Ninguna opción coincide con tu perfil",
  "why.setup_required": "Necesita un modelo de IA",
  "why.budget": "Límite mensual de IA alcanzado",
  "why.model_error": "El modelo de IA no respondió",
  "why.validation_failed": "No hay un borrador de fiar",
  "why.cv_attach": "Tu CV aprobado",
  "reason.setup_required": "Las respuestas largas necesitan un modelo de IA. Configúralo en Meridian (Settings → AI).",
  "reason.budget": "Has llegado al límite mensual de IA. Las respuestas cortas siguen funcionando.",
  "reason.model_error": "El modelo de IA no respondió. Inténtalo de nuevo en un momento.",
  "reason.validation_failed": "No he podido escribir un borrador del que me fíe. Responde tú o vuelve a intentarlo.",
  "reason.missing_candidate_fact": "Aún no está en tu perfil. Escríbelo y te ofreceré recordarlo.",
  "reason.missing_fact": "Aún no está en tu perfil. Escríbelo y te ofreceré recordarlo.",
  "reason.format_error": "Tu respuesta guardada no encaja con el formato de este campo. Escríbela tú.",
  "reason.no_matching_option": "Ninguna opción coincide con tu perfil. Elige una tú.",
  "reason.fill_failed": "No he podido rellenar este campo con seguridad, así que lo he dejado como estaba. Compruébalo tú.",
  "reason.protected": "Pregunta personal. Solo la relleno cuando pulsas {enter}.",
  "reason.protectedReady": "Respuesta personal. Tab no la inserta; pulsa {enter} para usarla.",
  "reason.readInReady": "Ningún dato de tu perfil lo dice. Tab no la inserta; pulsa {enter} para usarla.",
  "reason.consent": "Las casillas de consentimiento y legales las marcas siempre tú.",
  "reason.policy": "Según tus preferencias, esta pregunta la respondes tú.",
  "reason.sensitive_blank": "Pregunta personal opcional. La dejo en blanco salvo que tú decidas otra cosa.",
  "reason.not_applicable": "No corresponde según tu respuesta anterior.",
  "reason.generic": "No tengo ninguna sugerencia para este campo.",

  "gen.QUEUED": "Esperando su turno. Redacto las respuestas largas una a una.",
  "gen.GENERATING": "Escribiendo un borrador con tu perfil y esta empresa…",
  "gen.local": "Consultando tu perfil con IA local en este Mac…",
  "gen.REGENERATING": "Escribiendo una versión nueva…",
  "gen.VALIDATING": "Comprobando el borrador con tu perfil…",
  "gen.DONE": "Borrador listo.",
  "gen.FAILED": "No he podido escribir este borrador.",
  "gen.CANCELLED": "Borrador cancelado.",
  "gen.attempt": "Intento {n}",
  "key.tab.insert": "Insertar y siguiente",
  "key.tab.next": "Siguiente",
  "key.tab.keep": "Siguiente (respeta tu texto)",
  "key.tab.drafting": "Siguiente (sigo con el borrador)",
  "key.shiftTab": "Atrás",
  "key.shift": "Mayús",
  "key.enter.use": "Usar",
  "key.esc": "Ocultar",
  "key.regenerate": "Reescribir",
  "key.undo": "Deshacer",
  "btn.insert": "Insertar",
  "btn.replace": "Reemplazar",
  "btn.use": "Usar esta respuesta",
  "btn.undo": "Deshacer",
  "btn.draft": "Redactar con IA",
  "btn.dismiss": "Ocultar sugerencia (Esc)",
  "btn.showMore": "Ver más",
  "btn.showLess": "Ver menos",
  "regen.label": "Reescribir este borrador",
  "regen.again": "Otra vez",
  "regen.shorter": "Más corto",
  "regen.longer": "Más largo",
  "regen.different": "Otro enfoque",

  "toast.filled": "Insertado",
  "toast.filled.label": "Insertado: {label}",
  "toast.undone": "Deshecho",
  "toast.undone.label": "Deshecho: {label}",
  "act.undoAll": "Deshacer todo",
  "toast.saved": "Guardado en Meridian",
  "toast.saved.application": "Guardado para esta candidatura",
  "toast.saved.reuse": "Guardado para futuras candidaturas",
  "toast.draft_ready": "Borrador listo",
  "toast.draft_ready.label": "Borrador listo: {label}",
  "toast.regenerating": "Escribiendo una versión nueva…",
  "toast.copied": "Copiado",
  "toast.no_suggestion": "No hay sugerencia para este campo",
  "toast.paused": "Tab vuelve a funcionar como siempre",
  "toast.resumed": "Tab vuelve a insertar sugerencias",
  "toast.connection_lost": "He perdido el contacto con Meridian. Sigo intentándolo.",
  "toast.connection_back": "De nuevo en contacto con Meridian",
  "toast.error": "Algo ha fallado. Revisa la página antes de seguir.",
  "toast.region": "Avisos",

  "save.title": "¿Guardar esta respuesta en Meridian?",
  "save.title.edited": "¿Guardar tu cambio en Meridian?",
  "save.application": "Solo esta candidatura",
  "save.reuse": "Reutilizar en futuras candidaturas",
  "save.no": "No",
  "save.more": "Más opciones",
  "save.always": "Guardar siempre mis respuestas",
  "save.never": "No volver a preguntarme",
  "save.onlyHere": "Este tipo de respuesta solo se guarda para esta candidatura.",
  "save.local": "Se guarda en Meridian, en este Mac.",
  "save.group": "Guardar esta respuesta",
  "track.title": "¿Registrar esta candidatura en Meridian?",
  "track.body": "{title} en {company}",
  "track.evidence": "La página dice: «{text}»",
  "track.note": "Nunca doy por hecho que se haya enviado. Lo decides tú.",
  "track.record": "Registrar candidatura",
  "track.dismiss": "Ahora no",
  "track.group": "Registrar esta candidatura",

  "panel.region": "Asistente de Meridian",
  "panel.untitled": "Esta candidatura",
  "panel.titleSeparator": "{company} · {title}",
  "panel.pill": "Asistente de Meridian, {done} de {total} campos",
  "panel.pillNoForm": "Asistente de Meridian",
  "panel.expand": "Abrir el panel de Meridian",
  "panel.collapse": "Minimizar el panel",
  "panel.more": "Más opciones",
  "panel.job.applied": "Ya registrada como enviada",
  "panel.job.preparing": "En el Tracker de Meridian",
  "panel.job.prospective": "Guardada en Meridian",
  "conn.ready": "Conectado con Meridian",
  "conn.no_bridge": "Meridian no está abierto",
  "conn.unpaired": "Aún sin conectar",
  "conn.pairing": "Esperando tu aprobación",
  "conn.revoked": "Desconectado",
  "conn.proof_failed": "Conexión rechazada por tu seguridad",
  "conn.disabled": "Desactivado en Meridian",
  "conn.restoring": "Meridian está restaurando una copia",
  "conn.connecting": "Conectando con Meridian…",
  "conn.error": "Problema de conexión",
  "conn.ambiguous": "Conexión en pausa por tu seguridad",
  "conn.orphaned": "Hay que recargar la página",

  "calm.connecting": "Conectando con Meridian…",
  "calm.no_bridge": "Meridian no está abierto.",
  "calm.no_bridge.hint": "Ábrelo y continúo donde lo dejamos. También vuelvo a intentarlo por mi cuenta cada pocos segundos.",
  "calm.openMeridian": "Abrir Meridian",
  "calm.retryNow": "Reintentar ahora",
  "calm.unpaired": "Conecta este navegador con Meridian.",
  "calm.unpaired.hint": "Escribes un código corto una sola vez. Tu perfil y tus respuestas se quedan en este Mac, salvo el texto que Meridian envía al modelo de IA que has elegido.",
  "calm.unpaired.hint.port": "Meridian ha respondido en el puerto {port}. Cuando pulses Conectar, abrirá una ventana que te pedirá un código corto. Tu perfil y tus respuestas se quedan en este Mac, salvo el texto que Meridian envía al modelo de IA que has elegido.",
  "calm.connect": "Conectar",
  "calm.pairing": "Escribe este código en la ventana de Meridian",
  "calm.pairing.hint": "Meridian debe abrir por sí mismo una ventana pequeña que te pida este código. Continúa solo si acabas de pulsar Conectar.",
  "calm.pairing.port": "Meridian ha respondido en el puerto {port}.",
  "calm.pairing.nowindow": "¿No aparece ninguna ventana de Meridian? Abre Meridian desde la barra de menús y vuelve a intentarlo. Si sigue sin aparecer, pulsa Cancelar: puede que otro programa de este Mac esté respondiendo.",
  "calm.pairing.codeLabel": "Código de conexión {digits}",
  "calm.pairing.expires": "Caduca en {time}",
  "calm.pairing.expired": "Ese código ha caducado.",
  "calm.pairing.new": "Pedir un código nuevo",
  "calm.cancel": "Cancelar",
  "calm.proof_failed": "Algo en este Mac ha respondido, pero no es Meridian.",
  "calm.proof_failed.hint": "Por tu seguridad no le envío nada.",
  "calm.retry": "Reintentar",
  "calm.retrying": "Comprobando…",
  "calm.ambiguous": "Más de un programa de este Mac dice ser Meridian.",
  "calm.ambiguous.hint": "Por seguridad, no envío nada a ninguno (puertos {ports}). Cierra el que sobre y vuelve a intentarlo.",
  "calm.revoked": "Meridian ha desconectado este navegador.",
  "calm.revoked.hint": "Vuelve a conectarlo cuando quieras.",
  "calm.connectAgain": "Conectar de nuevo",
  "calm.disabled": "El asistente del navegador está desactivado en Meridian.",
  "calm.disabled.hint": "Actívalo en Meridian, en Settings → Browser assistant.",
  "calm.restoring": "Meridian está restaurando una copia de seguridad.",
  "calm.restoring.hint": "Continúo en cuanto termine.",
  "calm.error": "No he podido comunicarme con Meridian ahora mismo.",
  "calm.error.hint": "No se ha cambiado nada en la página.",
  "calm.noForm": "Esperando el formulario de la candidatura…",
  "calm.noForm.hint": "Abre el formulario y lo detecto.",
  "calm.profileEmpty": "Tu perfil de Meridian aún no tiene datos verificados.",
  "calm.profileEmpty.hint": "Añade tus datos en Meridian y los usaré aquí.",
  "calm.orphaned": "Se ha actualizado el Asistente de Meridian.",
  "calm.orphaned.hint": "Recarga esta página para seguir. Antes, guarda lo que hayas escrito.",
  "calm.reload": "Recargar página",
  "calm.update": "Hay una versión más reciente del Asistente de Meridian.",
  "calm.update.open": "Actualizar en los ajustes",
  "note.drafts": "Las respuestas largas necesitan un modelo de IA (Settings → AI).",
  "note.budget": "Límite mensual de IA alcanzado; las respuestas cortas siguen funcionando.",
  "note.crossOrigin": "Este formulario está incrustado en otro sitio web.",
  "note.crossOrigin.hint": "Permite Meridian en todos los sitios web para que pueda verlo.",
  "note.allowAll": "Permitir en todos los sitios",
  "note.dismiss": "Cerrar este aviso",

  "research.region": "Investigación de la empresa",
  "research.queued": "Preparándome para investigar {company}…",
  "research.queued.noCompany": "Preparándome para investigar esta empresa…",
  "research.running": "Investigando {company} y el puesto…",
  "research.running.noCompany": "Investigando la empresa y el puesto…",
  "research.done.full": "He investigado {company} y el puesto",
  "research.done.job_only": "He leído la oferta; no he confirmado la web de la empresa",
  "research.done.company_only": "He leído la web de {company}; la oferta daba pocos detalles",
  "research.done.minimal": "Poca información pública; los borradores se apoyarán en tu perfil",
  "research.partial": "He encontrado algo de contexto sobre {company}",
  "research.partial.noCompany": "He encontrado algo de contexto sobre la empresa",
  "research.failed": "No he podido investigar {company}; los borradores usarán tu perfil y la oferta",
  "research.failed.noCompany": "No he podido investigar esta empresa; los borradores usarán tu perfil y la oferta",
  "research.needs.company": "No sé con certeza de qué empresa se trata.",
  "research.needs.site": "No he podido confirmar la web de {company}. ¿La añades?",
  "research.needs.site.noCompany": "No he podido confirmar la web de la empresa. ¿La añades?",
  "research.cancelled": "Investigación detenida.",
  "research.quality.full": "Puesto + web de la empresa",
  "research.quality.job_only": "Solo la oferta",
  "research.quality.company_only": "Solo la web de la empresa",
  "research.quality.minimal": "Poco contexto",
  "research.show": "Ver fuentes",
  "research.hide": "Ocultar fuentes",
  "research.noSources": "Aún no hay fuentes.",
  "research.status.ok": "Usada",
  "research.status.failed": "No se pudo cargar",
  "research.status.blocked": "Bloqueada por el sitio",
  "research.status.skipped": "Omitida",
  "research.status.unverified": "No usada: no he podido confirmar que sea la empresa correcta",
  "research.site": "Usando {site}",
  "research.notThis": "¿No es esta empresa?",
  "research.addSite": "Añadir la web de la empresa",
  "research.refresh": "Investigar de nuevo",
  "research.form.company": "Nombre de la empresa",
  "research.form.site": "Web de la empresa, p. ej. acme.com",
  "research.form.go": "Investigar",
  "research.form.cancel": "Cancelar",
  "research.form.siteInvalid": "Eso no parece una dirección web.",

  "progress.label": "Progreso del formulario",
  "progress.summary": "{done} de {total}",
  "progress.fields": "Campos",
  "progress.status.pending": "Pendiente",
  "progress.status.inserted": "Insertado",
  "progress.status.edited": "Editado por ti",
  "progress.status.filled": "Ya rellenado",
  "progress.status.needs_you": "Te toca a ti",
  "progress.status.locked": "Lo dejo para ti",
  "progress.status.skipped": "Omitido",
  "progress.required": "obligatorio",
  "progress.section.other": "Otros",
  "progress.goto": "Ir a {label}",
  "act.fillVerified": "Rellenar los verificados ({n})",
  "act.fillVerified.hint": "Solo respuestas verificadas; nunca las personales ni legales.",
  "act.fillVerified.none": "No queda nada verificado por rellenar.",
  "act.keys.label": "Usar Tab para insertar y avanzar",
  "act.keys.hint.off": "Tab funciona como siempre. Actívalo para insertar con él.",
  "more.dock": "Posición del panel",
  "more.dock.left": "A la izquierda",
  "more.dock.right": "A la derecha",
  "more.alwaysOn": "Empezar automáticamente en {site}",
  "more.alwaysOn.generic": "Empezar automáticamente en este sitio",
  "more.hideHint": "Ocultar el aviso en {site}",
  "more.hideHint.generic": "Ocultar el aviso en este sitio",
  "more.deactivate": "Desactivar el asistente en esta página",
  "footer.settings": "Ajustes",
  "footer.help": "Ayuda",
  "footer.spend": "IA este mes: {spent}",
  "footer.spendOf": "IA este mes: {spent} de {limit}",
  "footer.nav": "Enlaces del panel",

  "chip.stale.research": "Se escribió antes de la última investigación. Si lo reescribes, usará lo que he encontrado desde entonces.",
  "chip.stale.profile": "Tu perfil cambió después de escribirlo, así que no lo inserto. Reescríbelo primero.",
  "chip.stale.company_rejected": "Dijiste que esta no es la empresa correcta, así que no inserto este borrador. Reescríbelo con la nueva investigación.",
  "chip.announce.stale": "Este borrador está desactualizado.",
  "btn.rewriteNew": "Reescribir con la nueva investigación",
  "btn.rewriteProfile": "Reescribir con mi perfil actualizado",
  "chip.skip.label.file_unapproved": "CV aún sin aprobar",
  "chip.skip.reason.file_unapproved": "Apruébalo en Meridian (Documents) y lo adjunto aquí.",
  "chip.skip.label.file_cover_letter": "La subes tú",
  "chip.skip.reason.file_cover_letter": "Solo adjunto tu CV aprobado. La carta de presentación súbela tú.",
  "chip.skip.label.file_none": "Aún no hay CV en Meridian",
  "chip.skip.reason.file_none": "Importa tu CV en Meridian (Documents), apruébalo y lo adjunto aquí.",
  "chip.skip.label.generic": "La subes tú",
  "chip.skip.reason.generic": "Solo adjunto tu CV aprobado, y solo en la subida de un CV. Sube este archivo tú.",
  "chip.alreadyInserted": "Ya insertado",
  "chip.announce.already": "Ya está insertado.",

  "announce.inserted": "Insertado en {label}: {value}",
  "announce.inserted.novalue": "Insertado en {label}",
  "announce.save": "Meridian pregunta: ¿guardar esta respuesta? Pulsa {keys} para responder.",
  "announce.track": "Meridian pregunta: ¿registrar esta candidatura? Pulsa {keys} para responder.",

  "more.track.save": "Guardar en el Tracker",
  "more.track.applied": "Ya la envié: registrarla",
  "dup.applied.title": "Ya enviaste tu candidatura a esta oferta",
  "dup.applied.hint.since": "Enviaste tu candidatura el {date}. No inserto nada hasta que continúes.",
  "dup.applied.hint": "Meridian la tiene registrada como enviada. No inserto nada hasta que continúes.",
  "dup.rejected.hint.since": "Enviaste tu candidatura el {date} y fue rechazada. No inserto nada hasta que continúes.",
  "dup.rejected.hint": "Meridian la tiene registrada como rechazada. No inserto nada hasta que continúes.",
  "dup.withdrawn.hint.since": "Enviaste tu candidatura el {date} y luego la retiraste. No inserto nada hasta que continúes.",
  "dup.withdrawn.hint": "Meridian la tiene registrada como retirada. No inserto nada hasta que continúes.",
  "dup.ghosted.hint.since": "Enviaste tu candidatura el {date} y no recibiste respuesta. No inserto nada hasta que continúes.",
  "dup.ghosted.hint": "Meridian la tiene registrada como enviada, sin respuesta. No inserto nada hasta que continúes.",
  "dup.preparing.title": "Esta oferta ya está en tu Tracker",
  "dup.preparing.hint.since": "La empezaste el {date}. Puedes seguir desde aquí.",
  "dup.preparing.hint": "Ya empezaste una candidatura para ella en Meridian. Puedes seguir desde aquí.",
  "dup.continue": "Continuar de todos modos",
  "dup.close": "Desactivar",
  "dup.pill.applied": "Candidatura a esta oferta ya enviada",
  "dup.pill.preparing": "Ya está en tu Tracker",
  "first.title": "Cómo funciona",
  "first.step1": "Haz clic en el primer campo del formulario.",
  "first.step2": "Pulsa Tab. Inserto la sugerencia y paso a la siguiente pregunta.",
  "first.step3": "Esc oculta una sugerencia; Mayús+Tab vuelve al campo anterior.",
  "first.shortcut": "Activa o desactiva el asistente con {keys} (atajo por defecto).",
  "first.keys": "Ver todos los atajos",
  "first.dismiss": "Entendido",
  "review.hint": "Échales un vistazo antes de enviar. Pulsa una para ir a ella.",
  "review.more": "+{n} más",
  "review.dismiss": "Cerrar este recordatorio",
  "review.region": "Antes de enviar",
  "help.region": "Atajos de teclado",
  "help.title": "Atajos de teclado",
  "help.back": "Volver al panel",
  "help.row.tab": "Inserta la sugerencia y pasa al siguiente campo",
  "help.row.shiftTab": "Vuelve al campo anterior",
  "help.row.enter": "Usa una respuesta personal (esas nunca se insertan con Tab)",
  "help.row.esc": "Oculta la sugerencia de este campo",
  "help.row.rewrite": "Reescribe un borrador",
  "help.row.undo": "Deshace la última inserción",
  "help.row.chip": "Acciones de la sugerencia, indicadas en sus botones: Reemplazar, Más corto, Más largo, Otro enfoque, Ver más, fuentes",
  "help.row.panel": "Lleva el teclado a este panel",
  "help.row.toggle": "Activa o desactiva el asistente (atajo por defecto)",
  "research.privacy": "Meridian lee estas páginas públicas por sí mismo, desde este Mac. Tu perfil y tus respuestas nunca se envían a ellas.",

  "research.actions": "Acciones de la investigación",
  "help.full": "Abrir la ayuda completa",
  "help.row.arrows": "Muévete por la lista de campos y por las filas de enlaces",
  "help.row.undoAll": "Deshace todas las respuestas del último Rellenar los verificados",
  "reason.duplicate": "Ya enviaste tu candidatura a esta oferta, así que no inserto nada. Elige Continuar de todos modos en el panel para cambiarlo.",
  "chip.announce.duplicate": "No se insertará nada: ya enviaste tu candidatura a esta oferta. Elige Continuar de todos modos en el panel para cambiarlo.",
  "more.alwaysOn.permission": "Permite el acceso a este sitio en la ventana que se acaba de abrir.",
  "fill.preview.region": "Respuestas por rellenar",
  "fill.preview.more": "y {n} más",
  "act.fillConfirm": "Rellenar ahora",

  "toast.already_recorded": "Ya está registrada en Meridian como enviada",
  "dup.interview.hint.since": "Enviaste tu candidatura el {date} y llegó a la fase de entrevista. No inserto nada hasta que continúes.",
  "dup.interview.hint": "Meridian la tiene en la fase de entrevista. No inserto nada hasta que continúes.",
  "dup.offer.hint.since": "Enviaste tu candidatura el {date} y recibiste una oferta. No inserto nada hasta que continúes.",
  "dup.offer.hint": "Meridian tiene registrada una oferta para ella. No inserto nada hasta que continúes.",
  "more.alwaysOn.rule": "Una regla para {rule} sigue iniciando el asistente aquí. Quítala en la página de opciones o en Meridian (Settings → Browser assistant).",
  "more.alwaysOn.rule.generic": "Otra regla sigue iniciando el asistente aquí. Quítala en la página de opciones o en Meridian (Settings → Browser assistant).",
  "note.draftsPaused": "Meridian ha pausado los borradores automáticos en esta página: pide uno con Redactar con IA.",
  "cv.use": "Usar otro CV",
  "cv.use.letter": "Usar otra carta de presentación",
  "cv.menu": "Otros documentos que Meridian puede adjuntar aquí",
  "cv.version": "versión {n}",
  "cv.option": "Adjuntar {name}",
};

const esPlural: Record<PluralKey, Plural> = {
  "chip.words": { one: "{n} palabra", other: "{n} palabras" },
  "chip.announce.draft": { one: "{n} palabra. Tómate un momento para leerlo y pulsa Tab para insertarlo.", other: "{n} palabras. Tómate un momento para leerlo y pulsa Tab para insertarlo." },
  "research.sources": { one: "{n} fuente", other: "{n} fuentes" },
  "progress.inserted": { one: "{n} insertado", other: "{n} insertados" },
  "act.drafts": { one: "{n} borrador en camino", other: "{n} borradores en camino" },
  "toast.filled.count": { one: "{n} respuesta insertada", other: "{n} respuestas insertadas" },
  "review.title": { one: "Falta {n} pregunta obligatoria por responder", other: "Faltan {n} preguntas obligatorias por responder" },
  "toast.undone.count": { one: "{n} respuesta deshecha", other: "{n} respuestas deshechas" },
  "fill.preview.title": { one: "¿Rellenar {n} respuesta?", other: "¿Rellenar {n} respuestas?" },
};

export const dictionaries: Record<Locale, { strings: Record<StringKey, string>; plurals: Record<PluralKey, Plural> }> = {
  en: { strings: en, plurals: enPlural },
  es: { strings: es, plurals: esPlural },
};

// ---- locale resolution ------------------------------------------------------------------------------------------
export function resolveLocale(language: Language | Locale | undefined, navigatorLanguage?: string): Locale {
  if (language === "en" || language === "es") return language;
  const nav = navigatorLanguage ?? (typeof navigator !== "undefined" ? navigator.language : "") ?? "";
  return nav.toLowerCase().startsWith("es") ? "es" : "en";
}

let current: Locale = resolveLocale("auto");
let numberFormat = new Intl.NumberFormat(current);
let pluralRules = new Intl.PluralRules(current);
let relativeFormat = new Intl.RelativeTimeFormat(current, { numeric: "auto" });

export function setLocale(language: Language | Locale | undefined, navigatorLanguage?: string): Locale {
  current = resolveLocale(language, navigatorLanguage);
  numberFormat = new Intl.NumberFormat(current);
  pluralRules = new Intl.PluralRules(current);
  relativeFormat = new Intl.RelativeTimeFormat(current, { numeric: "auto" });
  return current;
}

export function getLocale(): Locale {
  return current;
}

// ---- lookup -----------------------------------------------------------------------------------------------------
/**
 * How the Enter key is named in running text. Spanish Macs name it "Retorno" and Spanish PCs "Intro", so the Spanish
 * dictionary says `{enter}` and the platform decides (same rule as the key hints: macOS, by navigator, else the rest).
 * English keeps "Enter" everywhere.
 */
export function enterKeyName(locale: Locale = current, mac: boolean = detectMac()): string {
  if (locale !== "es") return "Enter";
  return mac ? "Retorno" : "Intro";
}

/** Placeholders every string may use without the caller passing them. Explicit params win. */
const BUILTINS: Record<string, () => string> = { enter: () => enterKeyName() };

function interpolate(template: string, params?: Params): string {
  if (!template.includes("{")) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    if (params && Object.prototype.hasOwnProperty.call(params, name)) return String(params[name]);
    return Object.prototype.hasOwnProperty.call(BUILTINS, name) ? BUILTINS[name]() : match;
  });
}

export function t(key: StringKey, params?: Params): string {
  return interpolate(dictionaries[current].strings[key], params);
}

/** Plural lookup: `{n}` is filled with the locale-formatted count; extra params are interpolated too. */
export function tn(key: PluralKey, count: number, params?: Params): string {
  const category = pluralRules.select(count) === "one" ? "one" : "other";
  return interpolate(dictionaries[current].plurals[key][category], { ...params, n: formatNumber(count) });
}

export function hasString(key: string): key is StringKey {
  return Object.prototype.hasOwnProperty.call(en, key);
}

/** Localised copy for a server `why.code`, or null when the code is unknown (the caller then shows the English fallback). */
export function whyText(code: string | undefined): string | null {
  const key = `why.${code ?? ""}`;
  return hasString(key) ? t(key) : null;
}

/** Localised calm sentence for a `needs.code` / blocked reason code, or null when unknown. */
export function reasonText(code: string | undefined): string | null {
  const key = `reason.${code ?? ""}`;
  return hasString(key) ? t(key) : null;
}

// ---- formatting pinned to the chosen locale ---------------------------------------------------------------------
export function formatNumber(n: number): string {
  return numberFormat.format(n);
}

export function formatMoney(usd: number): string {
  return new Intl.NumberFormat(current, { style: "currency", currency: "USD", maximumFractionDigits: usd >= 100 ? 0 : 2 }).format(usd);
}

/**
 * "14 Oct" / "14 oct" for the day something was recorded. `value` is the ISO date or timestamp the server knows; a date-only value
 * is read as that calendar day (not shifted by the time zone), and the year is added when it is not the current one.
 * Anything that is not a date comes back as null: the caller then says the sentence without a date.
 */
export function formatDay(value: string | undefined, now: Date = new Date()): string | null {
  const text = (value ?? "").trim();
  if (!text || text.length > 40) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const date = dateOnly ? new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3])) : new Date(text);
  if (Number.isNaN(date.getTime()) || date.getFullYear() < 2000 || date.getTime() > now.getTime() + 86_400_000 * 366) return null;
  const sameYear = date.getFullYear() === now.getFullYear();
  // the browser's own region decides the order ("11 Oct" in Britain, "Oct 11" in the States) as long as it speaks the language chosen here
  let tag: string = current;
  try {
    if (navigator.language && navigator.language.toLowerCase().startsWith(current)) tag = navigator.language;
  } catch { /* no navigator: the language alone */ }
  return new Intl.DateTimeFormat(tag, sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" }).format(date);
}

/** m:ss for countdowns. */
export function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${formatNumber(minutes)}:${String(seconds).padStart(2, "0")}`;
}

/** "2 minutes ago" / "hace 2 minutos". `deltaMs` is target minus now (negative = past). */
export function formatRelative(deltaMs: number): string {
  const abs = Math.abs(deltaMs);
  const sign = deltaMs < 0 ? -1 : 1;
  if (abs < 45_000) return relativeFormat.format(sign * Math.round(abs / 1000), "second");
  if (abs < 45 * 60_000) return relativeFormat.format(sign * Math.round(abs / 60_000), "minute");
  if (abs < 22 * 3_600_000) return relativeFormat.format(sign * Math.round(abs / 3_600_000), "hour");
  return relativeFormat.format(sign * Math.round(abs / 86_400_000), "day");
}

/** `lang` attribute value for the UI root. */
export function htmlLang(): string {
  return current;
}
