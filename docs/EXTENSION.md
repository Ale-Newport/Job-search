# Meridian Assistant: the Chrome and Safari extension

Meridian Assistant is a browser extension that helps you fill in job-application forms in your own Chrome or Safari, one question at a time. You click its toolbar icon on an application page, click a field, and it shows what it suggests for that field and why. **Tab** inserts the suggestion and moves you to the next question. Long, personal questions ("Why Acme?") are drafted in the background from your verified profile and from research on that company and role, so the draft is ready when you arrive.

This guide is for users and for whoever administers a Meridian installation. The last part is for developers. Where this guide and the code disagree, the code wins; the wire contract is `backend/jobagent/extension/schemas.py` and its TypeScript mirror `extension/src/shared/protocol.ts`. The design record is [EXTENSION_DESIGN.md](EXTENSION_DESIGN.md).

Status: implemented. What has and has not been verified is listed in [VERIFICATION.md](VERIFICATION.md) and under "Known limits" below.

## 1. What it is and what it never does

The assistant is a client of Meridian. Your profile, your verified facts, your CV and your saved answers stay in Meridian on your Mac. The extension talks only to Meridian over the loopback address (`127.0.0.1`); it never contacts an employer site, a search engine or an AI provider itself.

It **never**:

- clicks Next, Continue, Apply or Submit, or sends an application. Submitting is always your own click;
- touches password fields, one-time or security codes, card or bank details, government ID numbers, security answers, CAPTCHAs or multi-factor pages;
- ticks consent or legal boxes, or types a signature without you pressing Enter;
- uploads anything except your own **approved** CV (a cover letter only if Meridian wrote one for that job and you approved it);
- overwrites text you already typed in a field (it offers a **Replace** button instead);
- starts on its own on a page you did not choose: it runs where you click its icon, where its small "ready" hint on a known application site is clicked, or on a site you set to start automatically;
- records an application as sent unless you confirm it.

Everything it inserts is plain text through the page's own input events. It can be taken back with Undo.

## 2. Requirements

- Meridian.app running on this Mac (the extension finds it on `127.0.0.1`, ports 47615 to 47624). It can be in the menu bar with its window closed.
- Chrome 120 or newer (the manifest's `minimum_chrome_version`), or another Chromium browser that can load an unpacked extension (Meridian lists any Chromium browser as "Chrome"). Safari 18 or newer (`strict_min_version` of the Safari manifest).
- To prepare long answers: a writing model configured and tested under Settings > AI & providers. Without one, short answers still work and the field says so.

## 3. Install

Open **Settings > Browser assistant** in Meridian. It shows the state of each browser (not installed, detected, waiting for approval, connected, outdated) and the steps below.

### Chrome (and other Chromium browsers)

Chrome only runs an extension outside the Chrome Web Store when you load it yourself. Meridian prepares everything:

1. In Meridian, click **Install in Chrome**. In one click Meridian prepares the extension folder, copies its path to the clipboard, shows the folder in Finder and opens Chrome's extensions page. The folder is `~/Library/Application Support/Meridian/extensions/chrome`. Its path is stable across Meridian updates.
2. In Chrome, turn on **Developer mode** (top right of the extensions page).
3. Click **Load unpacked**, press Cmd+Shift+G, paste the path (Cmd+V), press Return and choose Open.
4. Pin the Meridian icon (puzzle-piece menu) and click it, then choose **Connect** (section 4).

If Google Chrome is not in Applications, Meridian still prepares the folder and copies the path; open your Chromium browser's `chrome://extensions` yourself. "Open with assistant" in the Tracker opens the paired browser (for a Chromium browser that is Google Chrome, because Meridian lists them all as Chrome).

The extension has a fixed id (`ophngbnbiekjhemmabbpildjedfphlbc`, pinned by the public key in the manifest). A folder with another id can still pair, but Meridian marks it **unrecognised extension** in red in the approval window and in the app; deny it unless you loaded it yourself. To use another id (for example a Chrome Web Store build), list it in `MERIDIAN_EXTENSION_IDS` (comma separated) for the Meridian process.

**Updating.** After a Meridian update the folder is refreshed by Meridian at the next start. The app then shows "Update the assistant" and an **Update in Chrome** button; in Chrome, click the reload arrow on Meridian Assistant. A running page keeps working until you reload it; an updated extension shows "Meridian Assistant was updated. Reload this page to keep going".

### Safari

The Safari extension is part of Meridian.app (it lives in `Meridian.app/Contents/PlugIns`). Safari never lets an installer switch an extension on, so you do three things yourself:

1. Open Meridian.app **from the Applications folder** at least once. macOS only registers the extension from the copy in Applications; keep a single copy of Meridian there (a build folder copy registers the same extension twice).
2. In Safari: **Safari > Settings > Extensions**, tick **Meridian Assistant**. If it is not listed, quit Safari (Cmd+Q) and open it again. This build is signed with an Apple Development certificate and is not notarised, so Safari may also need **Develop > Allow Unsigned Extensions** (if there is no Develop menu: Safari > Settings > Advanced > Show features for web developers). Safari switches that option off whenever it quits, so you may need it again after a restart.
3. With Meridian Assistant selected, choose **Edit Websites...** and set it to **Allow**, ideally **Always Allow on Every Website**. Add the icon to the toolbar with View > Customize Toolbar... and click it, then choose **Connect**.

Meridian cannot see whether Safari has the extension ticked or which websites it may use (Apple only answers that for the app that contains the extension); the Safari steps in the app turn to "done" when the extension actually reaches Meridian. A build without the Swift toolchain does not include the Safari extension at all; the app says so.

Safari has no keyboard shortcut for extensions, so there is none for starting the assistant: use the toolbar icon.

## 4. Connect (pairing)

You connect each browser once. There is no password and nothing to copy.

1. Click the Meridian icon in the toolbar, then **Connect**. The extension shows a 6-digit code ("Type this code in the Meridian window").
2. Meridian itself opens a small macOS window titled Meridian that asks for the code. Type the code and press **Connect** (or press **Deny**). The window has no default approve: a wrong code three times denies the request, and an empty answer is ignored. You can instead press **Approve connection** on the request shown at the top of Settings > Browser assistant, after checking that its code matches the one in the extension. Whichever decision comes first counts. Meridian also shows a macOS notification (without the code) and brings its window forward when it can.
3. The request lasts 120 seconds (the native window waits at most 90). After that, click Connect again for a new code.

Rules enforced by Meridian: one pending request per extension origin, at most five attempts per minute per origin (twenty overall) and eight pending requests in total. The extension's name and origin are shown in the window; the browser is taken from the request's origin, not from what the extension claims.

**Only type the code if Meridian opened the window.** If the extension shows a code but no Meridian window or request appears, press Cancel in the extension: something else on this Mac may be answering (section 9, "Known limits").

To disconnect: press **Disconnect** next to the browser in Settings > Browser assistant, or **Disconnect this browser** on the extension's options page. The browser returns to "not connected" and must be paired again. Disconnecting deletes nothing from your profile. If you turn off the setting **Turn on the browser assistant**, every request is refused with "switched off in Meridian" until you turn it on again.

## 5. Daily use

### Starting

Open an application form and click the Meridian toolbar icon (default shortcut Cmd+Shift+Y in Chrome; Safari has none). The badge shows **ON** while the assistant runs on that tab, **!** when it runs but Meridian needs your attention, and a green dot on a known application site where the form looks ready. A page it cannot run on (browser pages, the extension store, PDFs) opens the options page with an explanation.

Clicking the icon again toggles: if the panel is showing, the assistant turns off on that page; if the panel is hidden or collapsed away, the click shows it. The first time, a card explains the flow ("Click the first field of the form. Press Tab..."). On known application sites (the list in `extension/ats-hosts.json`: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Teamtailor, iCIMS, Taleo, SuccessFactors, BambooHR, Jobvite, Recruitee, Personio, Oracle Recruiting, and others) a small pill "Meridian is ready - click to assist" appears when the page shows an application form; clicking it starts the assistant like the icon. The pill only counts form controls: it reads no values, labels or text. You can hide it per site or switch it off in settings.

### The suggestion (chip)

Click or tab into a field and a small card (the chip) appears next to it with the suggested value, a trust label, a one-line reason, and where it came from. A choice field highlights the recommended option. If the form leaves no room, the chip folds into a one-line pill. For a field with nothing to suggest it says so quietly and says what to do.

### Trust colours

| Label (colour) | Meaning | Tab inserts? |
|---|---|---|
| **Verified** (green) | Taken from your verified profile or a saved policy | Yes |
| **Check this** (amber) | Worked out from your profile, or likely but not certain. Read it first. The field keeps a marker until you touch it | Yes |
| **AI draft - review it** (blue) | Written by your answer model from your profile and company research, validated against your facts. Always review and edit | Yes, after it has been on screen for 0.5 s |
| **Writing a draft** (blue, pulsing) | Still being written | No: Tab moves on, the draft is ready when you come back |
| **Protected answer** | Work authorisation, sponsorship, visa, demographics, adjustments, GDPR, a typed signature, and similar | Only with **Enter** (or a click), unless you allow Tab in settings. A typed signature always needs Enter |
| **Yours to decide** / **Left blank** (grey lock) | Consent, legal and optional personal questions | Never |
| **Needs you** (grey) | Meridian has no safe answer | Never |

### Keys

The assistant takes keys only on the page you started it on, only real (trusted) key presses, and never during IME composition.

| Key | What it does |
|---|---|
| **Tab** | Inserts the suggestion if it is eligible, then moves to the next question, whose chip is already showing. If there is nothing to insert, or the field already holds your text, it only moves on. When the browser's own Tab has a better claim (a link, a button or a plain control lies in between; a native date input still has segments; a code editor indents; a page popover such as a calendar owns the key; nothing follows) it is left to the browser |
| **Shift+Tab** | Goes back one field without changing anything |
| **Enter** | Inserts a protected answer when its chip says so. Otherwise untouched: it never hijacks form submission |
| **Esc** | Hides the suggestion for this field until you come back to it. With the focus inside the panel it returns you to the field |
| **Option+R** (Mac; Alt+R elsewhere) | Rewrites a draft |
| **Option+Z** (Alt+Z) | Undoes the last insertion. The Undo button on the chip lasts 8 seconds |
| **Option+Shift+Z** (Alt+Shift+Z) | Takes back everything the last **Fill verified** inserted, while the **Undo all** button shows (10 seconds) |
| **Option+Shift+M** (Alt+Shift+M) | Moves the keyboard into the Meridian panel (press again to return to the field), when the page lets the key through. From a cross-origin frame it is relayed to the top frame |
| **Control+Option+1..7** (Mac; Alt+Shift+1..7 elsewhere) | Runs the numbered buttons on a chip (Replace, Shorter, Longer, Different angle, Show more, and the source links). Each button prints its own key. These chords never type a character into the form |
| Cmd+Shift+Y (Chrome only, default) | Starts or stops the assistant. You can change it at `chrome://extensions/shortcuts` |

Inside the panel, the arrow keys move along the field list; Tab leaves it. The panel is not in the form's tab order; it can be reached at the end of the page or with Option+Shift+M.

To get the browser's normal Tab back for a while, turn off **Use Tab to insert & move on** in the panel.

If a field is covered by a popup the page opened (a date picker, a list), the assistant still inserts the answer but leaves the Tab to the page so the popup closes; it never writes into a field that popup covers.

### Fill verified

The panel's **Fill verified (n)** button fills every field that is currently Verified, Tab-eligible and not protected, in one go. It asks first: the panel lists the fields and the values that will go in, and nothing is written until you press **Fill now**. It never fills Check this answers, drafts, protected answers or consent. Afterwards, **Undo all** (or Option+Shift+Z) takes the batch back.

### Drafts and research

When you open a form, Meridian researches the company and role in the background (once per page, never per field) and shows a research strip in the panel ("Researching Acme and the role...", then which sources were used). Research is deterministic and uses no AI model:

- the job description from the page's own structured data, the page text, a public job-board API of the vendor, or the posting itself;
- the company's official website, accepted only after Meridian verified that it belongs to that company; then its about, mission, values, engineering and careers pages (fixed public paths, robots.txt respected);
- never LinkedIn, Indeed or Glassdoor, and never a page that could not be verified as the company's own.

Every page Meridian reads is parsed in a separate, short-lived helper process of the Meridian backend (the parse worker: at most two at once, low priority, a hard CPU and time limit, no access to your data). A page that is built to be slow to parse is skipped, never waited for.

Drafts are written only for long free-text questions (and for a short text field when you ask with **Draft with AI**), never for sensitive, legal, salary or personal-disclosure questions, one at a time in page order so earlier drafts inform later ones, through the writing model you chose in Meridian. A draft uses the question, the job text and the company pages Meridian verified, plus the verified facts it cites; it is validated against your profile and always labelled for review. It waits up to 8 seconds for research before drafting, records what the draft was based on (the job text, how many verified company pages, the research state) and lists the company pages it cited as sources. Late research never silently rewrites a draft (that would cost money); the chip says "Written before the latest research" and offers **Rewrite with new research**. If a fact the draft cites changes, or you say the company is wrong, the draft is no longer offered ("Rewrite with my updated profile").

Limits that protect your budget: at most 8 drafts a page, 40 a browser and 80 in total per rolling hour are started on their own (drafts you ask for with Draft with AI are never counted); a page spends at most the **spending cap per application** (default 1 USD) on drafts; the monthly AI budget of Settings > AI & providers applies. When a page reaches its automatic limit the panel says "Meridian paused automatic drafts on this page: ask for one with Draft with AI".

**Not this company?** If Meridian picked the wrong company, press **Not this company?** in the research strip and type the right name; **Add the company website** pins the official site (a bare domain). Each correction starts a new research run, is remembered for that page and, for a website, for that company once the run actually read it. **Research again** re-runs it. If Meridian cannot tell which company a page is, it asks instead of guessing.

### When you change an answer: saving corrections

When you leave a field whose final text differs from what the assistant inserted (or from nothing, where it offered nothing), and you typed or changed it yourself, a prompt asks **Save this answer in Meridian?** with **This application**, **Reuse in future applications** and **No**. "Reuse" is offered only for ordinary factual questions that map to a Profile field; narratives, company-specific, protected, legal and salary answers are only ever saved for this application. Passwords, one-time codes, card, bank and ID numbers and security answers are never offered. Only an answer that you typed or chose yourself is offered: the assistant needs a real key press, click, paste, drop or composition event on that field just before the change, so text that the page or a script wrote into a field (including through `execCommand`) is never saved or sent as one of your corrections. Under **When you correct a suggestion** in Settings you can choose Ask (default), Save automatically (not for sensitive answers) or Never save. This is the only moment typed text is sent to Meridian, and it stays on your Mac. Saved answers appear in the application's detail tagged "Inserted by assistant".

### Recording and tracking

Browsing creates nothing in Meridian. The first saved answer, or **Save to tracker** in the panel menu, creates the job and a bare application in status Preparing (no human task, no notification, no draft CV). **Save to tracker** never says you applied, and on an application that was already sent it changes nothing ("Already recorded").

The assistant watches your own click on a final submit button (English and Spanish wordings) and notes it. If the next page then shows confirmation text such as "thank you for applying" or "gracias por tu candidatura", it asks **Record this application in Meridian?**; **Record application** calls the same recording as **Mark applied** in Meridian. It never assumes a click succeeded; you can also choose **I applied: record this** in the panel menu at any time. If you already applied to the job, a banner says so ("You already applied to this job") and nothing is inserted until you press **Continue anyway**; the same banner covers rejected, withdrawn, interview and offer statuses.

When the form is complete, a "Before you submit" reminder lists the required questions that are still empty.

### CV and cover letter uploads

For a file field that asks for a CV, Meridian offers an approved CV in this order: the one attached to this application, the one Meridian wrote for this job, the CV chosen under Settings > Automation > Default CV, the newest approved base CV. Other eligible CVs are offered through **Use another CV** (a click, never a key). A cover letter is offered only if Meridian wrote it for this job and you approved it. If you have no approved CV, the field says so ("Approve your CV in Meridian (Documents)") and attaches nothing.

### Panel, frames and sites

The panel sits at the bottom right (dock left or right from its menu, collapse to a pill) and lists the company and role, the research strip, form progress by section, Fill verified, pending drafts, the keys toggle and links to Settings and Help. Many companies embed their form in a frame from another site (a Greenhouse or Workday form inside the company's page). Each frame scans itself and focus passes between frames with Tab and Shift+Tab. Reaching a frame of another site needs the browser's permission **Allow on all websites** (options page; in Safari: Always Allow on Every Website); without it the panel says "This form lives in another site's frame" with an **Allow all sites** button.

**Start automatically on these sites** (Settings > Browser assistant, or the panel menu on a site) starts the assistant as soon as a page of that site loads; the browser must also grant the extension that site, which the options page lists and asks for.

## 6. Settings

In Meridian, **Settings > Browser assistant** (applies to every connected browser straight away; the extension's options page shows the same behaviour switches when connected):

| Setting | Default | Meaning |
|---|---|---|
| Turn on the browser assistant | on | Off: the extension cannot reach your profile |
| Tab fills and moves to the next field | on | Off: Tab inserts and stays |
| Skip fields that already have a value | on | Tab passes over questions already answered |
| Let Tab insert sensitive answers | off | Protected answers normally need Enter. Asks for confirmation. Does not apply to a typed signature |
| When you correct a suggestion | Ask me each time | Ask, Save automatically, Never save |
| Research the company and role in the background | on | Public pages only, no AI |
| Search the web for the company's official site | on | Only when the page does not link to it; sends only the company name (section 8) |
| Prepare long answers in advance | on | Uses your answer model and counts in the monthly AI limit |
| Spending cap per application | 1 USD | An empty value means this default. Use a large amount for "no practical cap" |
| Show a discreet hint on known application sites | on | The "ready" pill |
| Language on websites | Automatic | English or Spanish for the assistant's buttons and tips (not your answers) |
| Start automatically on these sites | none | See section 5 |

The extension panel's footer shows this month's AI spend. Settings > Browser assistant also holds a feed of assisted pages, and the Tracker has an **Open with assistant** button on Tracker rows that opens the exact application link in the paired browser and starts the research. The setup guide gains a ninth step, "browser assistant". The native approval window can be switched off with the stored setting `extension_pairing_dialog` (there is no control for it in Settings > Browser assistant; the in-app Approve button always remains).

## 7. Troubleshooting

| Symptom | Cause and what to do |
|---|---|
| "Meridian isn't open" | Open Meridian (the panel has an **Open Meridian** button, which uses the `meridian://open` address registered by the app). The extension retries by itself every few seconds |
| Code shown, but no Meridian window or request appears | Press Cancel. Something other than Meridian may be answering. Open Meridian from the menu bar and try again |
| "More than one program on this Mac says it is Meridian" | The extension sends nothing to any of them. Usually two copies run (the installed app and a development backend, or two app copies). Quit the extra one, or start the development backend with `MERIDIAN_BRIDGE=0 make backend`, then press Retry |
| "Something answered, but it doesn't recognise this browser" | A listener on a bridge port says it is Meridian but gave no proof for this browser's credential, and did not say it had disconnected it (a reinstalled or restored Meridian looks the same as an impostor). Nothing is sent to it. Open Meridian to check; the options page offers "Connect to it…" after an explicit confirmation, and the existing credential is kept aside until the original Meridian answers again |
| "Something on this Mac answered, but it isn't Meridian" | The hello proof failed. Nothing was sent. Press Try again; if it persists, check which process holds ports 47615-47624 |
| "Meridian disconnected this browser" | You (or a backup restore) revoked it. Choose **Connect again** |
| "The browser assistant is switched off in Meridian" | Turn it on under Settings > Browser assistant |
| "Meridian is restoring a backup" | Wait; the assistant resumes when the restore ends |
| Meridian reports the browser connection cannot open | Every port 47615-47624 is taken. Quit the app that uses them, then press **Try again** in Settings > Browser assistant |
| "Meridian can't work on that page" | Browser pages, the extension store and PDFs are closed to every extension. Open the application in a normal tab |
| "This form lives in another site's frame" | Allow the extension on all websites (options page, or Safari's website access) |
| No chip on a field | Passwords, codes, card/bank/ID fields, hidden and read-only fields and anything not visible to a person are never assist fields. A field covered by a page layer is not written until it is uncovered: the check is made when the chip is drawn and again at the moment of every write, it also sees layers that let clicks through (`pointer-events: none`), and nothing is written while a popover or dialog of the page covers the assistant's own chip or panel (the note says the field is hidden) |
| Long answers say "Needs a writing model" | Configure and test a writing model under Settings > AI & providers |
| "Monthly AI budget reached; short answers still work" | The monthly AI limit is used up. Raise it or wait; short answers do not use the model |
| An upload says "CV not approved yet" or "No CV in Meridian yet" | Import and approve a CV in Documents |
| The assistant says "Reload this page" | The extension was reloaded or updated; reload the page (save what you typed first) |
| Safari does not list the extension | Open Meridian from /Applications, quit and reopen Safari, enable Develop > Allow Unsigned Extensions (section 3) |

The options page has a diagnostics block (ports checked, versions, last error) that is safe to share: it contains no tokens, page content or answers.

## 8. What leaves your Mac

| Data | Where it goes | When | Controlled by |
|---|---|---|---|
| Your profile, CV, saved answers, what you type | Nowhere. Stored in Meridian's SQLite database and documents on this Mac. The extension sends typed text to Meridian only on an explicit save | Always local | Save corrections setting |
| Page text and field labels from the form | From the page to the extension, then to Meridian over `127.0.0.1` (never leaves the Mac) | While the assistant runs | Starting the assistant |
| The employer's public pages (the posting, public job-board APIs of the ATS vendor, the company's verified website and its about/mission/values/engineering/careers pages, robots.txt) | Fetched **by Meridian**, from your Mac's IP address, as read-only GETs without cookies or credentials (User-Agent `Meridian/1.0`). No candidate data is in these requests | Once per page when research is enabled | Research setting |
| The company **name** only, as the search `<name> official website` | Meridian asks the public DuckDuckGo HTML page | Only when the page does not link to the company site and the web-lookup setting is on | "Search the web for the company's official site" |
| The question, the field's description, an excerpt of the job text and verified company pages, and the verified facts needed to answer | The text-model provider you configured (cloud provider endpoint, or a model on this Mac, which sends nothing out) | Only for a draft or a rewrite of a long answer | Prepare long answers setting; provider choice in Settings > AI & providers |
| Anything else | Nothing. No telemetry, no analytics, no project server | | |

The page address that is stored or sent to a model first has parameters that look like secrets, names or emails removed.

## 9. Known limits

- **Only English and Spanish are understood.** The classifiers (sensitive fields, submit buttons, confirmation text, consent questions, languages, levels and the plain-question gate) read English and Spanish. A form in another language is treated cautiously: fewer suggestions, not wrong ones. Field never-touch rules also cover some other languages' words for ID and tax numbers, but that is best effort.
- **Safari has not been run by any automated test.** The Safari extension is built, signed and its registration checked on a staged copy, and the same code runs in Playwright's WebKit (page world, not Safari's isolated world), but no test drives real Safari. Behaviour there is unverified: native focus order, the 0.5 s draft dwell, the website-access flow. Treat Safari as less proven than Chrome.
- **The first pairing cannot be authenticated.** Before a browser is connected there is no shared secret, so the extension cannot prove the program answering is the real Meridian. It pairs only if exactly one program answers like Meridian, and the real check is yours: Meridian must open a window asking for the code. A program that imitates Meridian perfectly could be chosen at that one moment; if no Meridian window opens, press Cancel.
- **A small window remains between the proof and the request.** After pairing, the extension proves the listener holds its key (a fresh hello proof) before every request that carries the credential. The proof and the request are separate connections, so a local process that took over the port between the two could still receive that one request. The key itself is never sent in the proof. The window is milliseconds and needs a local attacker able to bind the port at that instant.
- **Widget coverage is not verified against live employers.** The fill engine is tested against real widget libraries (react-select, Headless UI, MUI, Downshift, intl-tel-input, react-datepicker, Quill, Select2, Vue and others) and against hand-written replicas of the Workday, SmartRecruiters, Workable, iCIMS, Teamtailor and Lever widgets. The replicas were written from the vendors' public markup patterns and have not been verified against live tenants. No test contacts an employer site. Known gaps: default react-select markup after a choice, and Lever's location autocomplete (see `extension/tests/widgets/README.md`).
- **An unknown or oddly worded question is left to you.** The engine answers a factual question only when the whole question is a plain one of its family; any extra words it cannot account for (an employer note, a qualifier, a second question) leave it as "Needs you" instead of guessing. This fails closed, so some answerable questions are also left to you. When a page offers choices that do not include the verified answer, nothing is chosen.
- **Spain and EU answers need their own profile fields.** Right to work, sponsorship and expected salary are answered per place: separate **Application profile** fields exist for Spain (`work_authorization_es`, `sponsorship_es`, `expected_salary_es`) and for the EU/EEA (`work_authorization_eu`, `sponsorship_eu`). They are never inferred from the UK fields and stay "Needs you" until you fill them. A posting that names several countries, or none, gets no answer for these questions.
- **Research can be thin.** If the company's website cannot be verified, drafts rely on the job text and your profile; the research strip says so. Pages behind logins, scripts that need a browser, and sites that forbid crawling are not read. LinkedIn, Indeed and Glassdoor are never read.
- **Covers are detected by what they paint, with known gaps.** Before the chip is drawn and again right before every write, the assistant checks that you could see and click the field; the check includes a second pass that hits layers which a pointer would fall through. It cannot see a cover that is not an element with a background, an image or a border (`box-shadow`, blend modes, masks, backdrop filters), a cover whose `pointer-events: none` is an inline `!important` style or sits in an earlier CSS `@layer` than the assistant's own rule, and, for bulk fills ("Fill verified"), a field that is off screen is judged by its styles only (a file input is always checked on screen). A page of more than 8000 elements skips the paint pass when it is scanned (the write-time check still runs it). The value inserted is always shown beside the field: that is the last line of defence.
- **IntersectionObserver v2 is not used.** It was measured as a further gate (`trackVisibility`), but Chromium reports every target as not visible as soon as any element paints later in the page, including the transparent full-page layer the assistant's own interface always has, so it says nothing about a cover of the page. Safari's support was not evaluated.
- **Discovery still uses `is_global` for its address check.** `validate_public_url` in `backend/jobagent/discovery.py` refuses a destination only when a resolved address is not `ipaddress.is_global`, and it does not pin the connection to the address it checked. The assistant's research fetcher uses the stricter `security.is_public_ip` (which also excludes ranges that `is_global` still admits, and multicast) and pins the connection. Discovery reads the job sources the app is configured with, never a page the browser assistant names; moving it to `is_public_ip` is a known follow-up.
- **The extension's private key exists in one place.** The Chrome key that fixes the extension id (`ophngbnbiekjhemmabbpildjedfphlbc`) lives only in `.local-data/extension/` (git-ignored). Back it up offline: without it a rebuilt extension gets another id and every browser has to be paired again.
- **A submit is only noted, never known.** The record prompt depends on confirmation wording the page shows (English and Spanish phrases); a page that shows none never prompts, and you can record from the panel menu.
- Meridian's own desktop interface is English only; the extension's text is English and Spanish.
- One recorded product issue at the time of writing: a skipped transcript upload says "upload a cover letter yourself" (the wording is slightly wrong; the field is correctly left alone). The end-to-end suite reports it as a known issue and `--strict` fails on it until it is fixed.

## 10. Administration and privacy notes

- The extension holds no secrets. Its credential (`mrd1.<id>.<secret>`) is created by an approved pairing, stored hashed (SHA-256) in Meridian, and revocable in Settings > Browser assistant. In Chrome it is kept in extension storage that content scripts cannot read; where a browser cannot restrict that (Safari), it is encrypted with a non-extractable key that lives in the extension's own IndexedDB, and failing that kept for the browser session only.
- Backups never contain paired browsers, pairing records, assisted-page history or audit lines; every browser pairs again after a restore. Saved answers and the profile are part of the normal backup.
- Field-level audit events (never values) are pruned after 90 days; pages that never led to a job or application after a year.
- The bridge's logs omit request bodies and access logs; tokens are redacted (`mrd1.[redacted]`).
- See [SECURITY.md](../SECURITY.md) for the threat model.

## 11. Developer notes

### Layout

```
backend/jobagent/extension/   loopback bridge: app.py (guard, hello, pairing routes), server.py (listener), pairing.py, approval.py,
                              auth.py, routes.py, session.py, suggest.py, resolver.py, generation.py, research.py, feedback.py,
                              documents_pick.py, draft_basis.py, draft_limits.py, places.py, urls.py, markup.py, parse_worker.py, assets.py,
                              api.py (the desktop app's /api/extension/*), state.py, schemas.py
backend/jobagent/intelligence/plain.py, jurisdiction.py, languages.py, academic.py, legal.py, salary.py   answer-engine readers the resolver uses
extension/                    npm package: build.mjs, manifest.template.json, ats-hosts.json, src/{background,content,hint,options,shared}, _locales
extension/safari/             Swift sources for the app extension (no Xcode project)
extension/tests/              unit, content, ui, widgets, e2e, fixtures
frontend/src/extension*.ts(x) Settings > Browser assistant
migrations/versions/0013_browser_extension.py   tables extension_clients, extension_pairings, extension_sessions, extension_events
```

`extension/src/shared/protocol.ts` mirrors `backend/jobagent/extension/schemas.py`; change both together. The scanner is `backend/jobagent/automation/observation.js` bundled verbatim, so descriptors are the same ones Auto Apply's engine reads. `backend/jobagent/extension/resolver.py` reproduces the decisions of `automation/runner.py` for one control; `test_extension_resolver.py` compares them.

### Build

```sh
make extension              # type-check, build extension/dist/{chrome,safari}, production-build checks (needs Node.js)
make extension-safari       # also builds/signs the Safari app extension into build/safari (needs Xcode) and checks pluginkit on a throwaway copy
make extension-typecheck    # tsc --noEmit only
npm --prefix extension run zip   # extension/dist/meridian-assistant-chrome.zip (store-ready; not published)
```

`make build` and `make build-app` run `make extension` first. `scripts/build_sidecar.sh` embeds `extension/dist/chrome` into the frozen backend, which installs it into the data folder at start. The Tauri bundle build (`scripts/build_signed_app.py`) refuses a sidecar that lacks the current Chrome extension, builds and signs the Safari appex and embeds it in `Contents/PlugIns`, verifies the `meridian://` URL scheme and strict code signature, and prints `Safari extension: NOT included in this build (<reason>)` when Safari support is absent. `MERIDIAN_SKIP_SAFARI=1` or `--no-safari` ships without Safari.

Variants built by `extension/build.mjs`: `chrome`, `safari`, and the test variants `e2e`, `e2e-closed`, `e2e-top` (loopback host permissions, `__E2E__` hooks, open shadow root). `__E2E__` code is stripped from production and the production check fails the build if any remains.

### Tests

| Command | What it runs |
|---|---|
| `MERIDIAN_TEST=1 .venv/bin/pytest -q backend/tests/test_extension_*.py backend/tests/test_build_extension.py` | The backend half: pairing, bridge guard, sessions, suggest tiers, resolver parity, generation, research (offline), feedback, documents, backup exclusion, log hygiene |
| `npm --prefix extension test` | Unit tests of the worker, bridge client, hello proof, storage, polling, activation (seconds) |
| `make test-ext-fast` | Unit tests + content-script tests (Playwright, real keyboard, mock background; Chromium and WebKit) + panel UI tests |
| `make test-ext` | Everything: also the widget helper tests, the real-widget matrix, the production bundle report and the real-browser end-to-end suite |
| `make test-ext STRICT=1` / `make release-gate` | A missing runner fails; blocked or known-issue e2e scenarios count as failures |
| `.venv/bin/python extension/tests/e2e/test_extension_e2e.py [-k substr] [--recipe chrome] [--repeat N] [--list] [--strict]` | The real-browser suite alone (see `extension/tests/e2e/README.md`) |
| `.venv/bin/python extension/tests/widgets/run.py [--browser webkit] [--world main]` | Real-widget compatibility matrix (see `extension/tests/widgets/README.md`) |
| `.venv/bin/python frontend/tests/extension_e2e.py --base-url ... [--live]` | The Settings > Browser assistant UI, mocked or against the real backend and bridge |
| `MERIDIAN_TEST=1 .venv/bin/pytest -q backend/tests/test_engine_metamorphic.py` | The held-out metamorphic gate for answer safety (below) |

The **e2e harness** loads the real extension (the `e2e` build, plus a `chrome` production-build smoke journey) into real Chromium, or branded Chrome through CDP `Extensions.loadUnpacked`, against the real backend on a temporary data directory with an in-memory secret store, a fake loopback OpenAI-compatible model and local fixture pages (Greenhouse, Lever, Ashby, Workday in a cross-origin frame, React, masked phone, contenteditable, consent and demographics, uploads, submit and confirmation, a shadow-DOM form, a password page, a CAPTCHA gate and a fake employer site). Keyboard-only journeys complete whole applications with Tab, Shift+Tab, Enter and Esc and compare every control of every frame with an expectation file written from the seed data; each scenario also asserts forbidden effects (no submit click, no password write, no consent ticked, no trusted mouse event). Under `MERIDIAN_TEST` the product records instead of running `open`, `osascript` and `pbcopy`; the harness never touches the Keychain, the installed app or real data. `MERIDIAN_E2E_SLOWDOWN` (default 3) scales every event wait on a loaded machine.

The **metamorphic gate** (`backend/tests/test_engine_metamorphic.py`, helpers in `backend/tests/fixtures/engine/metamorphic`) wraps plain questions in English and Spanish with every kind of extra text (employer notes, negations, comparators, another country or person, injected instructions, hidden controls) for six synthetic candidates and asserts the safety properties through the real suggest pipeline. `extension/tests/e2e/scenarios_engine.py` runs a sample of twenty of its labels through the real page. A change that makes the engine answer a question it should leave to the user fails there.

Environment variables used by the bridge: `MERIDIAN_BRIDGE=0` disables it, `MERIDIAN_BRIDGE_PORT` overrides the ports (`PORT`, `LOW-HIGH` or `0`), `MERIDIAN_EXTENSION_IDS` adds recognised Chrome ids, `MERIDIAN_EXTENSION_SRC` points an unfrozen run at an extension build (a frozen sidecar only honours it inside `MERIDIAN_APP_BUNDLE`), `MERIDIAN_NO_NOTIFICATIONS` skips the native pairing dialog and bringing the app forward, `MERIDIAN_EXTENSION_RESEARCH_LOOPBACK` (tests only) lets research reach a loopback fixture site.

### Release and packaging notes

- Both builds read their version from `src-tauri/tauri.conf.json`. The Chrome manifest carries the public `key` (the private key lives only in the git-ignored `.local-data/extension/` of this Mac and must be backed up offline: the fixed extension id depends on it) so the id stays fixed.
- An extension that is older than the one bundled in the app is told apart by version and, when the version number did not change but the build did, by the digest of the shipped tree (`build.json` stamp, `latest_extension_digest` in the hello answer); a missing digest on either side is never reported as outdated.
- Chrome Web Store publication needs the owner's developer account and is not done; the build and zip are store-ready. Today the Chrome extension is loaded unpacked.
- The Safari build uses the Apple Development identity resolved by `build_signed_app.py`; it expires after a year and no Developer ID identity is installed, so the app and the Safari extension cannot be notarised or handed to another Mac. `MERIDIAN_SAFARI_NOTARIZED` switches off the "Allow Unsigned Extensions" hint in the app.
- `Info.plist` registers `meridian://`. `meridian://open` only raises the window; any web page can fire the address, so nothing in it is ever acted on.
- Before release run `make release-gate` and read the e2e summary: a known issue or blocked scenario fails it.
