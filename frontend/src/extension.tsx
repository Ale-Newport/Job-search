import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowRight,
  Check,
  Chrome,
  Compass,
  Copy,
  FileText,
  FolderOpen,
  Link2,
  LockKeyhole,
  PenLine,
  Plus,
  Power,
  Puzzle,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  Unlink,
  X,
} from "lucide-react";
import { api, useResource } from "./api";
import { Button, Confirm, Empty, IconButton, Panel, Resource, Tag } from "./components";
import { COPY, EXTENSION_PORTS, GUIDE_ES } from "./extension-copy";
import type { StateVars } from "./extension-copy";
import {
  ago,
  browserName,
  chipFor,
  clock,
  formatCode,
  money,
  normalizeHost,
  plural,
  spokenCode,
  useCountdown,
  useExtensionFeed,
  ExtensionFeedContext,
} from "./extension-state";
import type { ExtensionFeed } from "./extension-state";
import type {
  AppContext,
  ExtensionActivityItem,
  ExtensionBrowser,
  ExtensionClient,
  ExtensionInstallAction,
  ExtensionInstallRequest,
  ExtensionInstallResult,
  ExtensionLanguage,
  ExtensionOverallState,
  ExtensionPairing,
  ExtensionPatch,
  ExtensionSaveCorrections,
  ExtensionSettings,
  ExtensionStatus,
  List,
} from "./types";
import "./extension.css";

const en = COPY.en;
const TONE: Record<ExtensionOverallState, "green" | "amber" | "neutral"> = {
  ready: "green",
  pairing: "amber",
  detected: "amber",
  outdated: "amber",
  bridge_error: "amber",
  disabled: "neutral",
  not_installed: "neutral",
};
const ACTIVITY_TITLE = "Recent assistant activity";
const ACTIVITY_HINT = "What the assistant did in your browser. Answer text is never stored here.";
// Meridian only hears from an extension that is running: "not detected" is the honest word, not "not installed".
const BROWSER_TAG = {
  not_installed: ["Not detected", "neutral"],
  detected: ["Not connected", "amber"],
  pairing: ["Waiting for approval", "amber"],
  connected: ["Connected", "green"],
  outdated: ["Update needed", "amber"],
} as const;

/** Browsers that explain the current headline state, e.g. ["chrome","safari"] -> "Chrome and Safari". */
function involved(s: ExtensionStatus): string {
  const wanted: Partial<Record<ExtensionOverallState, string>> = {
    ready: "connected",
    detected: "detected",
    outdated: "outdated",
  };
  if (s.state === "pairing" && s.pending_pairing) return browserName(s.pending_pairing.browser);
  const list = (["chrome", "safari"] as const).filter((b) => s.browsers[b].state === wanted[s.state]);
  return (list.length ? list : (["chrome"] as const)).map(browserName).join(" and ");
}
function varsFor(s: ExtensionStatus): StateVars {
  const old = s.clients.find((c) => c.outdated);
  return {
    browser: involved(s),
    port: s.bridge.port,
    holder: s.bridge.conflict_process,
    ports: s.bridge.candidate_ports,
    appVersion: s.app_version,
    extVersion: old?.version,
    bridgeState: s.bridge.state,
  };
}

/** Settings > Browser assistant. `feed` is the one status poller owned by the app shell. */
export function ExtensionPanel({ ctx, feed }: { ctx: AppContext; feed: ExtensionFeed }) {
  return (
    <ExtensionFeedContext value={feed}>
      <PanelBody ctx={ctx} />
    </ExtensionFeedContext>
  );
}

function PanelBody({ ctx }: { ctx: AppContext }) {
  const feed = useExtensionFeed();
  const s = feed.data;
  const [remove, setRemove] = useState<ExtensionClient | null>(null);
  // Deep links (banner, setup guide, Today callout) land here with target "extension": put keyboard focus on the
  // request instead of the top of the page. Decided once, so a request that arrives later never steals focus.
  const [landOnRequest] = useState(() => ctx.target === "extension" && !!s?.pending_pairing);
  const headline = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.body.classList.add("ext-panel-open");
    return () => document.body.classList.remove("ext-panel-open");
  }, []);
  const copy = s ? en.states[s.state](varsFor(s)) : null;
  const stale = !!feed.error && !!s; // a failed poll must not blank the pairing code
  // The button that was pressed disappears with the new state: move focus to the headline first.
  const focusHeadline = () => headline.current?.focus();
  const connected = !!s && s.state === "ready";

  return (
    <>
      <Resource loading={feed.loading && !s} error={s ? "" : feed.error} retry={feed.reload}>
        {s && copy && (
          <>
            <div
              className={`automation-banner ext-hero ${s.state === "ready" ? "running" : ""} ${TONE[s.state]}`}
              data-state={s.state}
            >
              <span className="automation-symbol" aria-hidden="true">
                {s.state === "ready" ? (
                  <Sparkles size={25} />
                ) : s.state === "bridge_error" ? (
                  <TriangleAlert size={25} />
                ) : (
                  <Puzzle size={25} />
                )}
              </span>
              <div className="grow">
                <div role="status">
                  <h3 tabIndex={-1} ref={headline}>
                    {copy.headline}
                  </h3>
                  <p>{copy.body}</p>
                </div>
                <HeroActions s={s} ctx={ctx} />
              </div>
              <Tag tone={TONE[s.state]}>{copy.tag}</Tag>
            </div>
            {stale && (
              <p className="ext-stale" role="status">
                {en.refreshFailed}
              </p>
            )}
            {s.pending_pairing && (
              <PairingCard
                key={s.pending_pairing.id}
                p={s.pending_pairing}
                ctx={ctx}
                focusOnMount={landOnRequest}
                onDone={focusHeadline}
                onFailed={feed.reload}
              />
            )}
            {connected ? (
              <>
                <ClientsCard s={s} onRemove={setRemove} />
                <HowItWorks />
                <ActivityCard ctx={ctx} title={ACTIVITY_TITLE} description={ACTIVITY_HINT} />
                <SettingsCard s={s} ctx={ctx} />
                <DraftsCard s={s} ctx={ctx} />
                <BrowsersGrid s={s} ctx={ctx} />
                <ConnectionHelp s={s} />
              </>
            ) : (
              <>
                <BrowsersGrid s={s} ctx={ctx} />
                <ConnectionHelp s={s} />
                <ClientsCard s={s} onRemove={setRemove} />
                <SettingsCard s={s} ctx={ctx} />
                <DraftsCard s={s} ctx={ctx} />
              </>
            )}
            <PrivacyCard />
            {!connected && s.clients.length > 0 && (
              <ActivityCard ctx={ctx} title={ACTIVITY_TITLE} description={ACTIVITY_HINT} />
            )}
            <SpanishGuide s={s} />
          </>
        )}
      </Resource>
      {remove && (
        <Confirm
          danger
          title={en.disconnectTitle(browserName(remove.browser))}
          description={en.disconnectBody}
          button={en.disconnect}
          onClose={() => setRemove(null)}
          onConfirm={async () => {
            await api(`/extension/clients/${remove.id}`, "DELETE");
            await ctx.act("extension-refresh", () => Promise.resolve({}), `${remove.label} disconnected.`);
            // The dialog hands focus back to the (now removed) Disconnect button when it closes: take it after that.
            window.setTimeout(focusHeadline, 80);
          }}
        />
      )}
    </>
  );
}

async function restart(ctx: AppContext) {
  const res = await ctx.act("extension-retry", () => api<ExtensionStatus>("/extension/bridge/restart", "POST", {}));
  if (!res) return;
  if (res.bridge && res.bridge.state !== "listening") ctx.toast(en.bridge.still, true);
  else ctx.toast(en.bridge.restored);
}

function HeroActions({ s, ctx }: { s: ExtensionStatus; ctx: AppContext }) {
  const feed = useExtensionFeed();
  if (s.state === "disabled") {
    return (
      <div className="actions ext-hero-actions">
        <Button
          loading={ctx.busy === "extension-enable"}
          onClick={() =>
            ctx.act(
              "extension-enable",
              () => api("/extension/settings", "PATCH", { enabled: true } satisfies ExtensionPatch),
              "Browser assistant turned on.",
            )
          }
        >
          <Power size={15} /> Turn it on
        </Button>
      </div>
    );
  }
  if (s.state === "bridge_error") {
    return (
      <>
        {s.bridge.state !== "port_in_use" && s.bridge.message && <p className="ext-detail">{s.bridge.message}</p>}
        <div className="actions ext-hero-actions">
          <Button loading={ctx.busy === "extension-retry"} onClick={() => restart(ctx)}>
            {en.bridge.retry}
          </Button>
        </div>
      </>
    );
  }
  if (s.bridge.state === "stopped") {
    return (
      <>
        {s.bridge.message && <p className="ext-detail">{s.bridge.message}</p>}
        <div className="actions ext-hero-actions">
          <Button secondary loading={ctx.busy === "extension-retry"} onClick={() => restart(ctx)}>
            {en.bridge.start}
          </Button>
        </div>
      </>
    );
  }
  if (s.state === "not_installed" && feed.watching) {
    return (
      <p className="ext-waiting" role="status">
        <span className="ext-pulse" aria-hidden="true" /> {en.waiting}
      </p>
    );
  }
  return null;
}

function PairingCard({
  p,
  ctx,
  focusOnMount,
  onDone,
  onFailed,
}: {
  p: ExtensionPairing;
  ctx: AppContext;
  focusOnMount: boolean;
  onDone: () => void;
  onFailed: () => void;
}) {
  const left = useCountdown(p.expires_at);
  const name = browserName(p.browser);
  const expired = left === 0;
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusOnMount) title.current?.focus();
  }, [focusOnMount]);
  async function decide(kind: "approve" | "deny") {
    const done = await ctx.act(
      `extension-${kind}`,
      () => api(`/extension/pairings/${p.id}/${kind}`, "POST", {}),
      kind === "approve" ? `${name} connected.` : "Request denied.",
    );
    if (done) onDone();
    else onFailed(); // expired or already decided elsewhere: drop the stale card
  }
  return (
    <section className="panel ext-pairing" aria-labelledby={`pairing-${p.id}`}>
      {/* One-shot announcement: its text only changes when a NEW request arrives (the countdown below must not re-announce). */}
      <div role="alert" className="visually-hidden">
        {en.pairing.announce(name, spokenCode(p.code))}
      </div>
      <div className="ext-pairing-grid">
        <div>
          <span className="eyebrow">{en.pairing.eyebrow(name)}</span>
          <h3 id={`pairing-${p.id}`} tabIndex={-1} ref={title}>
            {en.pairing.title}
          </h3>
          <p>
            {en.pairing.compare} {en.pairing.version(p.extension_version)}
          </p>
          <p>{en.pairing.dialog}</p>
          {p.recognized === false && (
            <p className="ext-warning">
              <ShieldAlert size={15} aria-hidden="true" /> <span>{en.pairing.unrecognised}</span>
            </p>
          )}
        </div>
        <div className="ext-code-wrap">
          <code className="ext-code" aria-hidden="true">
            {formatCode(p.code)}
          </code>
          <small aria-hidden="true">{expired ? en.pairing.expired : en.pairing.expiresIn(clock(left))}</small>
          <small className="visually-hidden">
            Expires at {new Date(p.expires_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}
          </small>
        </div>
      </div>
      <div className="panel-actions">
        <Button disabled={expired} loading={ctx.busy === "extension-approve"} onClick={() => decide("approve")}>
          <Check size={15} /> {en.approve}
        </Button>
        <Button secondary loading={ctx.busy === "extension-deny"} onClick={() => decide("deny")}>
          {en.deny}
        </Button>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ browsers

interface Step {
  text: string;
  done: boolean;
  actions?: ReactNode;
}
function StepList({ steps }: { steps: Step[] }) {
  const current = steps.findIndex((step) => !step.done);
  return (
    <ol className="ext-steps">
      {steps.map((step, i) => (
        <li key={step.text} data-done={step.done} aria-current={i === current ? "step" : undefined}>
          <span className="ext-step-mark" aria-hidden="true">
            {step.done ? <Check size={13} /> : i + 1}
          </span>
          <span>
            {step.done && <span className="visually-hidden">Done: </span>}
            {step.text}
          </span>
          {step.actions ? <div className="ext-step-actions">{step.actions}</div> : null}
        </li>
      ))}
    </ol>
  );
}

function BrowsersGrid({ s, ctx }: { s: ExtensionStatus; ctx: AppContext }) {
  // Once one browser works, the install guides are a tucked-away reference instead of the main event.
  const tucked = s.clients.length > 0 && s.state === "ready";
  return (
    <div className="two-col ext-browsers">
      {(["chrome", "safari"] as const).map((b) => (
        <BrowserCard key={b} browser={b} s={s} ctx={ctx} tucked={tucked} />
      ))}
    </div>
  );
}

function BrowserCard({
  browser,
  s,
  ctx,
  tucked,
}: {
  browser: ExtensionBrowser;
  s: ExtensionStatus;
  ctx: AppContext;
  tucked: boolean;
}) {
  const feed = useExtensionFeed();
  const info = s.browsers[browser];
  const chrome = browser === "chrome";
  const name = browserName(browser);
  const pendingHere = s.pending_pairing?.browser === browser;
  const [tagText, tagTone] = BROWSER_TAG[pendingHere ? "pairing" : info.state];
  const installed = info.state !== "not_installed";
  const connected = info.state === "connected";
  const outdated = info.state === "outdated";
  const [busy, setBusy] = useState<ExtensionInstallAction | null>(null);
  const [prepared, setPrepared] = useState<string | null>(null);
  const path = info.extension_path ?? prepared;

  async function run(action: ExtensionInstallAction, quiet = false): Promise<ExtensionInstallResult | null> {
    setBusy(action);
    feed.watch();
    try {
      const res = await ctx.act(`extension-${browser}-${action}`, () =>
        api<ExtensionInstallResult>(`/extension/install/${browser}`, "POST", { action }),
      );
      if (res?.path) setPrepared(res.path);
      if (res && !quiet && res.message) ctx.toast(res.message, res.ok === false);
      return res;
    } finally {
      setBusy(null);
    }
  }
  /** Puts the folder path on the clipboard. The webview may refuse (no permission): the backend then uses pbcopy. */
  async function putPathOnClipboard(target: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(target);
      ctx.toast("Folder path copied. In Chrome's file picker press ⌘⇧G, paste it and press Return.");
    } catch {
      await run("copy_path");
    }
  }
  async function copyPath() {
    feed.watch();
    let target = path;
    if (!target) target = (await run("prepare", true))?.path ?? null;
    if (!target) return;
    await putPathOnClipboard(target);
  }
  /**
   * The whole install, or the whole update, in one click and ONE request: Meridian prepares the folder, puts its path on the
   * clipboard, shows it in Finder and opens Chrome's extensions page, in that order (an update only prepares and opens the
   * page). What cannot be done from here (Developer mode, Load unpacked, Connect) stays in the three steps below.
   */
  async function installInChrome(update: boolean) {
    setBusy("install");
    feed.watch();
    try {
      const body: ExtensionInstallRequest = update ? { action: "install", update: true } : { action: "install" };
      // a folder that cannot be prepared answers with an error, which act() already shows
      const res: ExtensionInstallResult | null = await ctx.act(`extension-${browser}-install`, () =>
        api<ExtensionInstallResult>(`/extension/install/${browser}`, "POST", body),
      );
      if (!res) return;
      if (res.path) setPrepared(res.path);
      if (res.ok === false) ctx.toast(res.message, true);
      // Chrome not found (Edge, Brave and other Chromium browsers load the same folder): everything else still worked
      else if (res.steps?.some((step) => step.action === "open_browser_page" && step.skipped))
        ctx.toast(update ? en.chrome.updatedNoChrome : en.chrome.installedNoChrome);
      else ctx.toast(update ? en.chrome.updated : en.chrome.installed);
    } finally {
      setBusy(null);
    }
  }

  const openPage = (label: string) => (
    <Button
      key="open"
      secondary
      loading={busy === "open_browser_page"}
      disabled={!info.browser_installed}
      onClick={() => run("open_browser_page")}
    >
      {label}
    </Button>
  );
  const steps: Step[] = chrome
    ? [
        { text: en.chrome.steps[0], done: installed },
        { text: en.chrome.steps[1], done: installed },
        { text: en.chrome.steps[2], done: connected },
      ]
    : [
        // Meridian cannot read Safari's settings: the extension reaching Meridian is the only evidence there is.
        { text: en.safari.steps[0], done: installed, actions: openPage("Open Safari") },
        { text: en.safari.steps[1], done: connected },
        { text: en.safari.steps[2], done: connected },
      ];
  const updateSteps: Step[] = chrome
    ? [
        { text: en.chrome.update[0], done: false },
        { text: en.chrome.update[1], done: false },
      ]
    : [
        { text: en.safari.update[0], done: false, actions: openPage("Open Safari") },
        { text: en.safari.update[1], done: false },
      ];
  /** Chrome: the one button first, then what is left to do by hand. Other browsers have no such button. */
  const guide = (list: Step[], update = false): ReactNode =>
    chrome ? (
      <>
        <div className="ext-install">
          <Button loading={busy === "install"} onClick={() => installInChrome(update)}>
            <Chrome size={15} /> {update ? en.chrome.updateButton : en.chrome.install}
          </Button>
          <p className="ext-detail">{update ? en.chrome.updateIntro : en.chrome.intro}</p>
          {!info.browser_installed && <p className="ext-note">{en.chrome.noChrome}</p>}
          {path && (
            <div className="ext-install-path">
              <code className="ext-path" tabIndex={0} aria-label="Extension folder path">
                {path}
              </code>
              <div className="ext-inline">
                <Button key="copy" secondary onClick={copyPath}>
                  <Copy size={14} /> {en.chrome.again.copy}
                </Button>
                <Button key="reveal" secondary loading={busy === "reveal"} onClick={() => run("reveal")}>
                  <FolderOpen size={14} /> {en.chrome.again.reveal}
                </Button>
                {openPage(en.chrome.again.open)}
              </div>
            </div>
          )}
        </div>
        <StepList steps={list} />
      </>
    ) : (
      <StepList steps={list} />
    );

  let body: ReactNode;
  if (info.bundled === false) {
    body = <p className="ext-note">{chrome ? en.chrome.missing : en.safari.missing}</p>;
  } else if (outdated) {
    body = (
      <>
        {guide(updateSteps, true)}
        <details className="ext-more">
          <summary>Reinstall from scratch</summary>
          {guide(steps)}
        </details>
      </>
    );
  } else if (connected) {
    body = (
      <details className="ext-more">
        <summary>{tucked ? "Reinstall or update" : "Show the install steps"}</summary>
        {guide(steps)}
      </details>
    );
  } else if (installed || pendingHere) {
    body = (
      <>
        <div className="ext-found" role="status">
          <Check size={16} aria-hidden="true" />
          <div>
            <strong>{pendingHere ? "Waiting for your approval" : en.foundTitle(name)}</strong>
            <p>{pendingHere ? "Approve the connection request above to finish." : en.found(name)}</p>
          </div>
        </div>
        <details className="ext-more">
          <summary>Show the install steps</summary>
          {guide(steps)}
        </details>
      </>
    );
  } else if (tucked) {
    body = (
      <details className="ext-more">
        <summary>Set up {name} too</summary>
        {guide(steps)}
      </details>
    );
  } else {
    body = guide(steps);
  }
  return (
    <Panel
      title={chrome ? en.chrome.title : en.safari.title}
      description={
        info.browser_installed
          ? `Extension ${info.bundled_version} ships with this Meridian`
          : `${name} was not found in Applications`
      }
      action={<Tag tone={tagTone}>{tagText}</Tag>}
      className="ext-browser"
    >
      <div className="ext-browser-body">
        {body}
        {!chrome && info.bundled !== false && !connected && (
          <>
            {!outdated && <p className="ext-detail">{en.safari.unseen}</p>}
            <p className="ext-detail">{en.safari.applications}</p>
            <p className="ext-note">
              {en.safari.notListed}
              {info.needs_unsigned_toggle ? ` ${en.safari.unsigned}` : ""}
            </p>
          </>
        )}
      </div>
    </Panel>
  );
}

// ------------------------------------------------------------------------------------------------ connection help

/** What to do when the extension and Meridian do not find each other: every sentence here describes behaviour the
 *  bridge really has (docs/EXTENSION_DESIGN.md 4.1 to 4.4), so it must change with it. */
function ConnectionHelp({ s }: { s: ExtensionStatus }) {
  const c = en.help;
  const range = `${EXTENSION_PORTS.first} to ${EXTENSION_PORTS.last}`;
  const port = s.bridge.state === "listening" ? s.bridge.port : null;
  const outside = port != null && (port < EXTENSION_PORTS.first || port > EXTENSION_PORTS.last);
  return (
    <details className="ext-help" open={outside || undefined}>
      <summary>{c.title}</summary>
      {port != null && (
        <p className={outside ? "ext-note" : "ext-detail"} role={outside ? "alert" : undefined}>
          {outside ? c.portOutside(port, range) : c.port(port, range)}
        </p>
      )}
      <dl>
        {c.items.map(([question, answer]) => (
          <div key={question}>
            <dt>{question}</dt>
            <dd>{answer}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

// ------------------------------------------------------------------------------------------------ connected browsers

function ClientsCard({ s, onRemove }: { s: ExtensionStatus; onRemove: (client: ExtensionClient) => void }) {
  if (!s.clients.length) return null;
  return (
    <Panel title="Connected browsers" description="Each connection can be removed at any time.">
      <div className="simple-list">
        {s.clients.map((c) => (
          <div key={c.id}>
            <span className="list-icon" aria-hidden="true">
              {c.browser === "chrome" ? <Chrome size={17} /> : <Compass size={17} />}
            </span>
            <div className="grow">
              <strong>{c.label}</strong>
              <small>
                Version {c.version} · {c.last_seen_at ? `last used ${ago(c.last_seen_at)}` : "not used yet"}
                {c.stats.pages > 0 &&
                  ` · ${plural(c.stats.insertions, "field")} filled on ${plural(c.stats.pages, "page")}`}
              </small>
              {c.last_error && <small className="error-text">{c.last_error}</small>}
            </div>
            {c.outdated ? <Tag tone="amber">Update needed</Tag> : <Tag tone="green">Connected</Tag>}
            <IconButton title={`Disconnect ${c.label}`} onClick={() => onRemove(c)}>
              <Trash2 size={15} />
            </IconButton>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function HowItWorks() {
  return (
    <Panel title={en.howTo.title} description={en.howTo.intro}>
      <dl className="ext-keys">
        {en.howTo.keys.map(([key, text]) => (
          <div key={key}>
            <dt>
              <kbd>{key}</kbd>
            </dt>
            <dd>{text}</dd>
          </div>
        ))}
      </dl>
      <p className="ext-detail">{en.howTo.more}</p>
    </Panel>
  );
}

// ------------------------------------------------------------------------------------------------ settings

const sameValue = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function ToggleRow({
  id,
  copy,
  checked,
  disabled,
  onChange,
}: {
  id: string;
  copy: readonly string[];
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="settings-row">
      <div>
        <strong id={`ext-${id}`}>{copy[0]}</strong>
        <p id={`ext-${id}-hint`}>{copy[1]}</p>
      </div>
      <input
        type="checkbox"
        aria-labelledby={`ext-${id}`}
        aria-describedby={`ext-${id}-hint`}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
    </div>
  );
}

function SettingsCard({ s, ctx }: { s: ExtensionStatus; ctx: AppContext }) {
  const set = s.settings;
  const c = en.settings;
  // The switches show the new value at once; the server's answer replaces it on the next status refresh.
  const [pending, setPending] = useState<ExtensionPatch>({});
  const settled = useRef(new Set<string>());
  const [confirmTab, setConfirmTab] = useState(false);
  const view: ExtensionSettings = { ...set, ...pending };
  useEffect(() => {
    setPending((p) => {
      const next: Record<string, unknown> = { ...p };
      for (const key of Object.keys(p)) {
        if (settled.current.has(key) || sameValue(next[key], set[key as keyof ExtensionSettings])) {
          delete next[key];
          settled.current.delete(key);
        }
      }
      return Object.keys(next).length === Object.keys(p).length ? p : (next as ExtensionPatch);
    });
  }, [set]);
  async function patch(change: ExtensionPatch) {
    const keys = Object.keys(change);
    setPending((p) => ({ ...p, ...change }));
    const ok = await ctx.act("extension-settings", () => api("/extension/settings", "PATCH", change));
    if (ok) keys.forEach((key) => settled.current.add(key));
    else
      setPending((p) => {
        const next: Record<string, unknown> = { ...p };
        keys.forEach((key) => delete next[key]);
        return next as ExtensionPatch;
      });
  }
  const locked = ctx.busy === "extension-settings";
  return (
    <>
      <Panel title="Assistant settings" description="Changes apply to every connected browser straight away.">
        <ToggleRow id="enabled" copy={c.enabled} checked={view.enabled} disabled={locked} onChange={(v) => patch({ enabled: v })} />
        <SettingsGroup title={c.groups.form} />
        <ToggleRow id="auto_advance" copy={c.autoAdvance} checked={view.auto_advance} disabled={locked} onChange={(v) => patch({ auto_advance: v })} />
        <ToggleRow id="skip_filled" copy={c.skipFilled} checked={view.skip_filled} disabled={locked} onChange={(v) => patch({ skip_filled: v })} />
        <ToggleRow
          id="tab_inserts_protected"
          copy={c.tabProtected}
          checked={view.tab_inserts_protected}
          disabled={locked}
          onChange={(v) => (v ? setConfirmTab(true) : patch({ tab_inserts_protected: false }))}
        />
        <div className="settings-row">
          <div>
            <strong id="ext-save_corrections">{c.saveCorrections[0]}</strong>
            <p id="ext-save_corrections-hint">{c.saveCorrections[1]}</p>
          </div>
          <select
            aria-labelledby="ext-save_corrections"
            aria-describedby="ext-save_corrections-hint"
            value={view.save_corrections}
            disabled={locked}
            onChange={(e) => patch({ save_corrections: e.target.value as ExtensionSaveCorrections })}
          >
            {(["ask", "always", "never"] as const).map((v) => (
              <option key={v} value={v}>
                {c.saveOptions[v]}
              </option>
            ))}
          </select>
        </div>
        <SettingsGroup title={c.groups.prepare} />
        <ToggleRow id="background_research" copy={c.research} checked={view.background_research} disabled={locked} onChange={(v) => patch({ background_research: v })} />
        <ToggleRow id="web_lookup" copy={c.webLookup} checked={view.web_lookup} disabled={locked || !view.background_research} onChange={(v) => patch({ web_lookup: v })} />
        <ToggleRow id="local_assist" copy={c.localAssist} checked={view.local_assist} disabled={locked} onChange={(v) => patch({ local_assist: v })} />
        <ToggleRow id="background_drafts" copy={c.drafts} checked={view.background_drafts} disabled={locked} onChange={(v) => patch({ background_drafts: v })} />
        <CapRow value={view.draft_budget_usd} disabled={locked || !view.background_drafts} onSave={(v) => patch({ draft_budget_usd: v })} />
        <SettingsGroup title={c.groups.browser} />
        <ToggleRow id="hint_on_ats" copy={c.hint} checked={view.hint_on_ats} disabled={locked} onChange={(v) => patch({ hint_on_ats: v })} />
        <div className="settings-row">
          <div>
            <strong id="ext-language">{c.language[0]}</strong>
            <p id="ext-language-hint">{c.language[1]}</p>
          </div>
          <select
            aria-labelledby="ext-language"
            aria-describedby="ext-language-hint"
            value={view.language}
            disabled={locked}
            onChange={(e) => patch({ language: e.target.value as ExtensionLanguage })}
          >
            {(["auto", "en", "es"] as const).map((v) => (
              <option key={v} value={v}>
                {c.languageOptions[v]}
              </option>
            ))}
          </select>
        </div>
        <SitesRow sites={view.always_on_sites} disabled={locked} onChange={(sites) => patch({ always_on_sites: sites })} />
      </Panel>
      {confirmTab && (
        <Confirm
          title={c.tabProtectedConfirm.title}
          description={c.tabProtectedConfirm.body}
          button={c.tabProtectedConfirm.button}
          onClose={() => setConfirmTab(false)}
          onConfirm={() => patch({ tab_inserts_protected: true })}
        />
      )}
    </>
  );
}

function SettingsGroup({ title }: { title: string }) {
  return <h4 className="ext-group">{title}</h4>;
}

/** Per-application draft spending cap: empty means "no extra cap" (null). */
function CapRow({
  value,
  disabled,
  onSave,
}: {
  value: number | null;
  disabled: boolean;
  onSave: (value: number | null) => void;
}) {
  const c = en.settings.draftCap;
  const [text, setText] = useState(value == null ? "" : String(value));
  useEffect(() => setText(value == null ? "" : String(value)), [value]);
  const trimmed = text.trim().replace(/^\$/, "");
  const parsed = trimmed === "" ? null : Number(trimmed);
  // Whole cents: the server stores the cap rounded to 2 decimals.
  const next = parsed === null ? null : Math.round(parsed * 100) / 100;
  const invalid = parsed !== null && (!Number.isFinite(parsed) || !next || next <= 0 || parsed > 1000);
  const dirty = !invalid && next !== value;
  function submit(e: FormEvent) {
    e.preventDefault();
    if (dirty) onSave(next);
  }
  return (
    <form className="settings-row" onSubmit={submit}>
      <div>
        <strong id="ext-draft_budget_usd">{c[0]}</strong>
        <p id="ext-draft_budget_usd-hint">{c[1]}</p>
        {invalid && (
          <p className="error-text" id="ext-draft_budget_usd-error">
            Enter an amount above 0, for example 0.50, or leave it empty.
          </p>
        )}
      </div>
      <div className="ext-cap">
        <span aria-hidden="true">US$</span>
        <input
          inputMode="decimal"
          placeholder="No cap"
          value={text}
          disabled={disabled}
          aria-labelledby="ext-draft_budget_usd"
          aria-describedby={`ext-draft_budget_usd-hint${invalid ? " ext-draft_budget_usd-error" : ""}`}
          aria-invalid={invalid}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" secondary disabled={disabled || !dirty}>
          Save
        </Button>
      </div>
    </form>
  );
}

const MAX_SITES = 50;
function SitesRow({
  sites,
  disabled,
  onChange,
}: {
  sites: string[];
  disabled: boolean;
  onChange: (sites: string[]) => void;
}) {
  const c = en.settings.sites;
  const [text, setText] = useState("");
  const [problem, setProblem] = useState("");
  const input = useRef<HTMLInputElement>(null);
  function add(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    const host = normalizeHost(text);
    if (!host) return setProblem("Enter a website address such as careers.example.com.");
    if (sites.includes(host)) return setProblem(`${host} is already in the list.`);
    if (sites.length >= MAX_SITES) return setProblem(`You can add up to ${MAX_SITES} sites.`);
    setProblem("");
    setText("");
    onChange([...sites, host]);
  }
  return (
    <div className="settings-row ext-sites-row">
      <div className="grow">
        <strong id="ext-always_on_sites">{c[0]}</strong>
        <p id="ext-always_on_sites-hint">{c[1]}</p>
        {sites.length > 0 && (
          <ul className="ext-sites" aria-label="Sites where the assistant starts automatically">
            {sites.map((site) => (
              <li key={site}>
                <span>{site}</span>
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`Remove ${site}`}
                  onClick={() => {
                    onChange(sites.filter((v) => v !== site));
                    input.current?.focus();
                  }}
                >
                  <X size={12} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <form className="ext-add-site" onSubmit={add}>
          <input
            ref={input}
            value={text}
            placeholder="careers.example.com"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            disabled={disabled}
            aria-labelledby="ext-always_on_sites"
            aria-describedby={`ext-always_on_sites-hint${problem ? " ext-always_on_sites-error" : ""}`}
            aria-invalid={!!problem}
            onChange={(e) => {
              setText(e.target.value);
              setProblem("");
            }}
          />
          <Button type="submit" secondary disabled={disabled || !text.trim()}>
            <Plus size={14} /> Add site
          </Button>
        </form>
        {problem && (
          <p className="error-text" id="ext-always_on_sites-error" role="status">
            {problem}
          </p>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ written answers

function DraftsCard({ s, ctx }: { s: ExtensionStatus; ctx: AppContext }) {
  const d = s.drafts;
  const capped = d.monthly_limit_usd != null && !d.unlimited;
  const reached = capped && d.spent_usd >= (d.monthly_limit_usd ?? Infinity);
  return (
    <Panel title="Written answers" description="Long, personalised answers use an answer model and your verified profile.">
      {d.configured ? (
        <div className="ext-drafts">
          <div className="settings-row">
            <div>
              <strong>
                {d.provider} · {d.model}
              </strong>
              <p>
                {money(d.spent_usd)} used in {d.month} across {plural(d.calls, "call")} ·{" "}
                {d.unlimited
                  ? "spending is uncapped"
                  : `monthly limit ${d.monthly_limit_usd == null ? "not set" : money(d.monthly_limit_usd)}`}
              </p>
              {d.message && <p className="ext-note">{d.message}</p>}
              {capped && (
                <progress
                  aria-label="Monthly AI spending against your limit"
                  value={Math.min(d.spent_usd, d.monthly_limit_usd ?? 0)}
                  max={d.monthly_limit_usd ?? 1}
                />
              )}
            </div>
            <Button secondary onClick={() => ctx.navigate("settings", "ai")}>
              AI usage & limit <ArrowRight size={14} />
            </Button>
          </div>
          {reached && (
            <div className="info-card compact warning">
              <TriangleAlert size={21} aria-hidden="true" />
              <div>
                <strong>Monthly AI limit reached.</strong>
                <p>Short answers such as name, email and education still work. Long drafts resume next month or when you raise the limit.</p>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="info-card compact warning">
          <TriangleAlert size={21} aria-hidden="true" />
          <div>
            <strong>Long answers need an answer model.</strong>
            <p>
              Short fields such as name, email, education and links work without one. Choose a provider, add its key
              and test the connection in Settings.
            </p>
            <Button secondary onClick={() => ctx.navigate("settings", "ai")}>
              Configure answer model
            </Button>
          </div>
        </div>
      )}
    </Panel>
  );
}

function PrivacyCard() {
  return (
    <div className="info-card ext-privacy">
      <ShieldCheck size={22} aria-hidden="true" />
      <div>
        <strong>{en.privacy.title}</strong>
        <ul>
          {en.privacy.items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ activity

const KIND_ICON = {
  paired: Link2,
  revoked: Unlink,
  page_assisted: FileText,
  inserted: PenLine,
  draft_ready: Sparkles,
  skipped_sensitive: LockKeyhole,
  blocked: ShieldAlert,
} as const;

function ActivityCard({
  ctx,
  title,
  description,
  quiet = false,
}: {
  ctx: AppContext;
  title: string;
  description: string;
  quiet?: boolean;
}) {
  const r = useResource<List<ExtensionActivityItem>>("/extension/activity?limit=10", ctx.refresh);
  if (quiet && !r.data && !r.loading) return null; // optional panel elsewhere in the app: never show an error box
  return (
    <Panel title={title} description={description}>
      <Resource loading={r.loading && !r.data} error={r.data ? "" : r.error} retry={r.reload}>
        {r.data?.items.length ? (
          <div className="simple-list ext-activity">
            {r.data.items.map((item) => {
              const Icon = KIND_ICON[item.kind] ?? FileText;
              const where = [item.host, item.company, item.title].filter(Boolean).join(" · ");
              return (
                <div key={item.id}>
                  <span className="list-icon" aria-hidden="true">
                    <Icon size={16} />
                  </span>
                  <div className="grow">
                    <strong>{item.message}</strong>
                    <small>
                      {where ? `${where} · ` : ""}
                      {browserName(item.browser)} · {ago(item.created_at)}
                    </small>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <Empty icon={<Sparkles size={25} />} title="Nothing yet" description="Pages you assist will appear here." />
        )}
      </Resource>
    </Panel>
  );
}

/** Recent assistant events on the Activity page (hidden until the assistant has been set up). */
export function AssistantActivity({ ctx }: { ctx: AppContext }) {
  if (ctx.assistant === "not_installed" || ctx.assistant === "unknown") return null;
  return (
    <ActivityCard
      ctx={ctx}
      quiet
      title="Browser assistant"
      description="Pages you filled with the Meridian assistant. Answer text is never recorded here."
    />
  );
}

// ------------------------------------------------------------------------------------------------ Spanish

/** Open the Spanish guide by default for people whose language is Spanish (decided once, never re-applied). */
const prefersSpanish = (language: string) =>
  language === "es" || (language === "auto" && (navigator.language || "").toLowerCase().startsWith("es"));

function SpanishGuide({ s }: { s: ExtensionStatus }) {
  const es = COPY.es;
  const [open] = useState(() => prefersSpanish(s.settings.language));
  const current = es.states[s.state](varsFor(s));
  const rows = [
    es.settings.enabled,
    es.settings.autoAdvance,
    es.settings.skipFilled,
    es.settings.tabProtected,
    es.settings.saveCorrections,
    es.settings.research,
    es.settings.webLookup,
    es.settings.drafts,
    es.settings.draftCap,
    es.settings.hint,
    es.settings.language,
    es.settings.sites,
  ];
  return (
    <details className="ext-guide" lang="es" open={open}>
      <summary>{GUIDE_ES.summary}</summary>
      <h4>{GUIDE_ES.now}</h4>
      <p>
        <strong>{current.headline}.</strong> {current.body}
      </p>
      {s.pending_pairing && (
        <p>
          <strong>{es.pairing.title}</strong> {es.pairing.compare} {es.pairing.dialog}
        </p>
      )}
      <h4>{es.chrome.title}</h4>
      <p>{es.chrome.intro}</p>
      <ol>
        {es.chrome.steps.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ol>
      <h4>{es.safari.title}</h4>
      <ol>
        {es.safari.steps.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ol>
      <p>{es.safari.unseen}</p>
      <p>{es.safari.applications}</p>
      <p>
        {es.safari.notListed}
        {s.browsers.safari.needs_unsigned_toggle ? ` ${es.safari.unsigned}` : ""}
      </p>
      <h4>{es.help.title}</h4>
      <ul>
        {es.help.items.map(([question, answer]) => (
          <li key={question}>
            <strong>{question}.</strong> {answer}
          </li>
        ))}
      </ul>
      <h4>{es.howTo.title}</h4>
      <p>{es.howTo.intro}</p>
      <ul>
        {es.howTo.keys.map(([key, text]) => (
          <li key={key}>
            <strong>{key}.</strong> {text}
          </li>
        ))}
      </ul>
      <p>{es.howTo.more}</p>
      <h4>{GUIDE_ES.settings}</h4>
      <ul>
        {rows.map(([title, hint]) => (
          <li key={title}>
            <strong>{title}.</strong> {hint}
          </li>
        ))}
      </ul>
      <h4>{es.privacy.title}</h4>
      <ul>
        {es.privacy.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </details>
  );
}

// ------------------------------------------------------------------------------------------------ app shell pieces

/** Quiet dot/pill for the Settings nav item. aria-hidden: it must NOT change the button's accessible name
 *  (tests and screen readers address the nav item as "Settings"); the text equivalent is the visually hidden
 *  description the nav button points at through aria-describedby. */
export function ExtensionNavChip({ status }: { status: ExtensionStatus | null }) {
  const chip = chipFor(status);
  if (!chip) return null;
  return (
    <span className={`nav-chip ${chip.tone}`} aria-hidden="true">
      {chip.text}
    </span>
  );
}

/** Global attention banner so an unanswered pairing request is visible on every page. */
export function ExtensionPairingBanner({
  status,
  onReview,
}: {
  status: ExtensionStatus | null;
  onReview: () => void;
}) {
  const p = status?.pending_pairing;
  if (!p || status.state !== "pairing") return null;
  const name = browserName(p.browser);
  return (
    <div className="info-card ext-banner">
      {/* One announcement per request (keyed): the visible text below is not a live region. */}
      <div role="alert" className="visually-hidden" key={p.id}>
        {en.pairing.announce(name, spokenCode(p.code))}
      </div>
      <Puzzle size={22} aria-hidden="true" />
      <div className="grow">
        <strong>{name} wants to connect to Meridian</strong>
        <p>
          Pairing code <span className="ext-inline-code">{formatCode(p.code)}</span>. Approve it here only if you just
          clicked Connect and the extension shows the same code. You can also type that code into the Meridian window
          that opened.
        </p>
      </div>
      <Button onClick={onReview}>Review request</Button>
    </div>
  );
}
