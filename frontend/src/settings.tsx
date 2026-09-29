import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Chrome,
  Cpu,
  Database,
  ExternalLink,
  Fingerprint,
  Globe2,
  HardDrive,
  KeyRound,
  LockKeyhole,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
  Zap,
} from "lucide-react";
import type { AppContext, Data } from "./types";
import {
  api,
  download,
  items,
  label,
  openExternal,
  time,
  useResource,
} from "./api";
import { BoundaryConfirmation, BrowserAccountConfirmation } from "./onboarding";
import {
  Button,
  Confirm,
  Editor,
  Empty,
  ErrorBox,
  IconButton,
  JsonDetails,
  KeyValues,
  Panel,
  Resource,
  SectionTitle,
  Status,
  Tag,
} from "./components";
import type { Field } from "./components";
const automationFields: Field[] = [
  {
    key: "scheduler_enabled",
    label: "Enable scheduled discovery and email sync",
    type: "checkbox",
  },
  {
    key: "default_mode",
    label: "Default application mode",
    type: "select",
    options: ["manual", "review", "auto"],
    required: true,
  },
  {
    key: "auto_apply",
    label: "Enable autonomous applications (safety gates always apply)",
    type: "checkbox",
  },
  {
    key: "allowed_domains",
    label: "Approved application domains",
    type: "list",
    hint: "Exact domains, separated by commas. Empty means no unattended submission.",
  },
  {
    key: "min_match",
    label: "Minimum match for Auto (%)",
    type: "number",
    min: 0,
    max: 100,
  },
  {
    key: "min_confidence",
    label: "Minimum browser confidence (0–1)",
    type: "number",
    min: 0.5,
    max: 1,
    step: 0.01,
  },
  {
    key: "max_applications_day",
    label: "Maximum applications per day",
    type: "number",
    min: 1,
    max: 100,
  },
  {
    key: "max_applications_company",
    label: "Maximum applications per company",
    type: "number",
    min: 1,
    max: 20,
  },
  {
    key: "minimum_interval_seconds",
    label: "Minimum interval between applications (seconds)",
    type: "number",
    min: 1,
  },
  {
    key: "discovery_interval_minutes",
    label: "Scheduled discovery interval (minutes)",
    type: "number",
    min: 15,
  },
  {
    key: "email_interval_minutes",
    label: "Email sync interval (minutes)",
    type: "number",
    min: 5,
  },
];
const aiFields: Field[] = [
  {
    key: "browser_engine",
    label: "Browser decision engine",
    type: "select",
    options: ["hybrid", "laya", "jev", "deterministic"],
    required: true,
  },
  {
    key: "laya_endpoint",
    label: "Local Laya endpoint",
    type: "url",
    hint: "Must be a local loopback endpoint for private inference.",
  },
  { key: "jev_endpoint", label: "Jev endpoint", type: "url" },
  { key: "jev_model", label: "Jev model" },
  {
    key: "jev_fallback_enabled",
    label: "Allow fallback to Jev when local confidence is low",
    type: "checkbox",
  },
  {
    key: "text_provider",
    label: "Text generation provider",
    type: "select",
    options: [
      "none",
      "ollama",
      "openai",
      "anthropic",
      "gemini",
      "openai-compatible",
    ],
    required: true,
  },
  { key: "text_model", label: "Text model name" },
  {
    key: "text_base_url",
    label: "Text provider base URL",
    type: "url",
    hint: "Ollama default: http://127.0.0.1:11434/v1",
  },
  {
    key: "monthly_budget",
    label: "Monthly remote AI budget (USD)",
    type: "number",
    min: 0,
    step: 0.01,
  },
];
export function Automation({ ctx }: { ctx: AppContext }) {
  const r = useResource("/automation", ctx.refresh),
    [open, setOpen] = useState(false),
    [session, setSession] = useState<Data | null>(null),
    d = r.data || {};
  useEffect(() => {
    const timer = setInterval(r.reload, 5000);
    return () => clearInterval(timer);
  }, [r.reload]);
  return (
    <>
      <SectionTitle
        eyebrow="CONTROL AT EVERY STEP"
        title="Automation"
        description="A dedicated browser, visible decisions, and your boundaries always in force."
        actions={
          <Button
            secondary={!d.paused}
            danger={!d.paused}
            loading={ctx.busy === "pause"}
            onClick={() =>
              ctx.act(
                "pause",
                () =>
                  api(
                    `/automation/${d.paused ? "resume" : "pause"}`,
                    "POST",
                    {},
                  ),
                d.paused ? "Automation resumed." : "Automation paused.",
              )
            }
          >
            {d.paused ? <Play size={16} /> : <Pause size={16} />}{" "}
            {d.paused ? "Resume automation" : "Pause automation"}
          </Button>
        }
      />
      <Resource loading={r.loading && !r.data} error={r.error} retry={r.reload}>
        <div className={`automation-banner ${d.paused ? "paused" : "running"}`}>
          <span className="automation-symbol">
            {d.paused ? <Pause size={25} /> : <Zap size={25} />}
          </span>
          <div className="grow">
            <h3>
              {d.paused ? "Automation is paused" : "Automation is active"}
            </h3>
            <p>
              {d.paused
                ? "No new browser actions will run until you resume. Your workspace remains available."
                : "Configured discovery and permitted application actions can run. Pause is checked before each browser action."}
            </p>
          </div>
          <Tag>{d.running ? "Agent running" : "Agent idle"}</Tag>
        </div>
        <div className="two-col">
          <Panel
            title="Your dedicated browser"
            description="Sign in manually. Your sessions stay in the local browser profile."
          >
            <div className="browser-card">
              <Chrome size={36} />
              <div>
                <strong>Persistent application profile</strong>
                <p>MFA and human verification stay in your hands.</p>
              </div>
            </div>
            <div className="panel-actions">
              <Button
                secondary
                onClick={() =>
                  ctx.act(
                    "browser",
                    () =>
                      api("/browser/open", "POST", {
                        url: "https://www.linkedin.com/login",
                      }),
                    "LinkedIn opened in your dedicated browser.",
                  )
                }
              >
                <ExternalLink size={14} />
                Log into LinkedIn
              </Button>
              <Button
                secondary
                onClick={() =>
                  ctx.act(
                    "browser",
                    () =>
                      api("/browser/open", "POST", {
                        url: "https://secure.indeed.com/",
                      }),
                    "Indeed opened in your dedicated browser.",
                  )
                }
              >
                <ExternalLink size={14} />
                Log into Indeed
              </Button>
              <Button secondary onClick={() => setOpen(true)}>
                Open a website
              </Button>
              <Button
                secondary
                onClick={async () => {
                  const value = await ctx.act("browser-status", () =>
                    api("/browser/status"),
                  );
                  if (value) setSession(value);
                }}
              >
                <RefreshCw size={14} />
                Check browser
              </Button>
            </div>
            {session && (
              <JsonDetails title="Browser session status" data={session} open />
            )}
            {d.browser && (
              <JsonDetails title="Current browser state" data={d.browser} />
            )}
            <BrowserAccountConfirmation ctx={ctx} />
          </Panel>
          <Panel
            title="Active boundaries"
            description="Deterministic gates before an unattended submission"
          >
            <KeyValues
              data={{
                default_mode: d.settings?.default_mode || "review",
                autonomous_applications: !!d.settings?.auto_apply,
                minimum_match: d.settings?.min_match,
                minimum_confidence: d.settings?.min_confidence,
                applications_per_day: d.settings?.max_applications_day,
                allowed_domains: d.settings?.allowed_domains || [],
              }}
            />
            <div className="panel-actions">
              <Button
                secondary
                onClick={() => ctx.navigate("settings", "automation")}
              >
                Edit boundaries
                <ArrowRight size={14} />
              </Button>
            </div>
          </Panel>
        </div>
        <Panel
          title="Browser runs"
          description="Live status. Open a run to inspect the decision trail."
        >
          {(d.runs || []).length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Application</th>
                    <th>Engine</th>
                    <th>Result</th>
                    <th>Started</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {d.runs.map((run: Data) => (
                    <tr key={run.id}>
                      <td>
                        <strong>{run.title || "Application"}</strong>
                        <small>{run.company}</small>
                        {run.error && (
                          <small className="error-text">
                            {typeof run.error === "string"
                              ? run.error
                              : run.error.message}
                          </small>
                        )}
                      </td>
                      <td>
                        <Tag>{run.engine || "hybrid"}</Tag>
                      </td>
                      <td>
                        <Status value={run.status} />
                      </td>
                      <td>{time(run.created_at)}</td>
                      <td>
                        <Button
                          secondary
                          onClick={() =>
                            ctx.select({ kind: "run", id: run.id })
                          }
                        >
                          Inspect
                          <ArrowRight size={14} />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              icon={<Cpu size={25} />}
              title="Every action, observable"
              description="Browser runs record observations, selected operations, confidence, execution, and independent submission verification."
            />
          )}
        </Panel>
      </Resource>
      {open && (
        <Editor
          title="Open in the application browser"
          description="Use your dedicated local browser profile to sign in or complete human verification."
          fields={[
            { key: "url", label: "Website URL", type: "url", required: true },
          ]}
          submit="Open browser"
          onClose={() => setOpen(false)}
          onSave={async (value) => {
            await api("/browser/open", "POST", value);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Website opened.",
            );
          }}
        />
      )}
    </>
  );
}
export function Settings({
  ctx,
  theme,
  setTheme,
}: {
  ctx: AppContext;
  theme: string;
  setTheme: (theme: string) => void;
}) {
  const r = useResource("/settings", ctx.refresh),
    integrations = useResource("/integrations", ctx.refresh),
    health = useResource("/health"),
    [tab, setTab] = useState(ctx.target || "general"),
    [edit, setEdit] = useState<"automation" | "ai" | null>(null),
    [credential, setCredential] = useState<string | null>(null),
    [result, setResult] = useState<Data | null>(null),
    [textResult, setTextResult] = useState<Data | null>(null),
    [deepSeek, setDeepSeek] = useState(false),
    s = r.data || {};
  const tabs = [
    ["general", "General", Settings2],
    ["automation", "Automation", ShieldCheck],
    ["ai", "AI & providers", Cpu],
    ["backup", "Backup & restore", Database],
  ] as const;
  return (
    <>
      <SectionTitle
        eyebrow="BUILT AROUND YOUR BOUNDARIES"
        title="Settings"
        description="Make Meridian work your way. Your data and decisions remain under your control."
      />
      <div className="tabs settings-tabs">
        {tabs.map(([id, name, Icon]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            <Icon size={16} />
            {name}
          </button>
        ))}
      </div>
      <Resource {...r} retry={r.reload}>
        {tab === "general" && (
          <>
            <Panel
              title="Workspace"
              description="A private, local-first career workspace"
            >
              <div className="settings-row">
                <div>
                  <strong>Appearance</strong>
                  <p>Choose a comfortable workspace for your day.</p>
                </div>
                <select
                  aria-label="Appearance"
                  value={theme}
                  onChange={(e) => setTheme(e.target.value)}
                >
                  <option value="system">Follow system</option>
                  <option value="light">Light</option>
                  <option value="dark">Dark</option>
                </select>
              </div>
              <div className="settings-row">
                <div>
                  <strong>Getting started guide</strong>
                  <p>Reopen the eight setup steps on your dashboard.</p>
                </div>
                <Button
                  secondary
                  onClick={async () => {
                    const shown = await ctx.act("onboarding", () =>
                      api("/onboarding", "PATCH", { hidden: false }),
                    );
                    if (shown) ctx.navigate("dashboard");
                  }}
                >
                  Show guide
                  <ArrowRight size={14} />
                </Button>
              </div>
              <div className="settings-row">
                <div>
                  <strong>Data location</strong>
                  <p className="code">
                    {health.data?.data_dir ||
                      "Local application support directory"}
                  </p>
                </div>
                <HardDrive size={20} />
              </div>
              <div className="settings-row">
                <div>
                  <strong>Version</strong>
                  <p>{health.data?.version || "0.1.0"}</p>
                </div>
                <Tag tone="green">Local workspace</Tag>
              </div>
            </Panel>
            <div className="privacy-grid">
              <Panel>
                <div className="privacy-card">
                  <Fingerprint size={25} />
                  <h3>Your profile is yours</h3>
                  <p>
                    Candidate facts, applications, and documents are stored
                    locally. No telemetry or external analytics.
                  </p>
                </div>
              </Panel>
              <Panel>
                <div className="privacy-card">
                  <KeyRound size={25} />
                  <h3>Secrets in Keychain</h3>
                  <p>
                    API credentials and email tokens use macOS Keychain. They
                    are excluded from encrypted backups.
                  </p>
                </div>
              </Panel>
              <Panel>
                <div className="privacy-card">
                  <Globe2 size={25} />
                  <h3>Clear data boundaries</h3>
                  <p>
                    Websites receive application data you authorize. Configured
                    remote AI providers receive only the context required for
                    the selected task.
                  </p>
                </div>
              </Panel>
            </div>
          </>
        )}
        {tab === "automation" && (
          <>
            <Panel
              title="Application safety & scheduling"
              description="Start with Review mode and conservative limits"
              action={
                <Button secondary onClick={() => setEdit("automation")}>
                  Edit automation settings
                </Button>
              }
            >
              <KeyValues
                data={Object.fromEntries(
                  automationFields.map((f) => [f.key, s[f.key]]),
                )}
              />
              <BoundaryConfirmation ctx={ctx} />
            </Panel>
            <div className="info-card">
              <ShieldCheck size={22} />
              <div>
                <strong>Unknown information always needs you.</strong>
                <p>
                  CAPTCHA, MFA, sensitive questions, low confidence, and
                  uncertain submission outcomes pause the run. Interrupted
                  submissions require reconciliation before a retry.
                </p>
              </div>
            </div>
            <Rules ctx={ctx} settings={s} />
          </>
        )}
        {tab === "ai" && (
          <>
            <LocalLaya ctx={ctx} />
            <AIUsage settings={s} />
            <Panel
              title="Browser decision engine"
              description="DOM observations first. Explicit operations and confidence gates."
              action={
                <Button secondary onClick={() => setEdit("ai")}>
                  Edit AI configuration
                </Button>
              }
            >
              <KeyValues
                data={{
                  engine: s.browser_engine || "hybrid",
                  laya_endpoint: s.laya_endpoint,
                  jev_endpoint: s.jev_endpoint,
                  minimum_confidence: s.min_confidence,
                }}
              />
              <div className="panel-actions">
                <Button
                  secondary
                  loading={ctx.busy === "ai-test"}
                  onClick={async () => {
                    const d = await ctx.act("ai-test", () =>
                      api("/ai/test", "POST", {
                        engine: s.browser_engine || "laya",
                      }),
                    );
                    if (d) setResult(d);
                  }}
                >
                  <Zap size={15} />
                  Test engine
                </Button>
                <Button
                  secondary
                  loading={ctx.busy === "ai-benchmark"}
                  onClick={async () => {
                    const d = await ctx.act("ai-benchmark", () =>
                      api("/ai/benchmark", "POST", {
                        engine: s.browser_engine || "laya",
                      }),
                    );
                    if (d) setResult(d);
                  }}
                >
                  <Cpu size={15} />
                  Run local benchmark
                </Button>
              </div>
              {result && (
                <JsonDetails
                  title="Engine test / benchmark result"
                  data={result}
                  open
                />
              )}
            </Panel>
            <Panel
              title="Text generation"
              description="Drafts grounded in the verified facts you select"
              action={
                <Button secondary onClick={() => setDeepSeek(true)}>
                  Set up DeepSeek
                </Button>
              }
            >
              <KeyValues
                data={{
                  provider: s.text_provider || "none",
                  model: s.text_model,
                  endpoint: s.text_base_url,
                  monthly_budget_usd: s.monthly_budget ?? 0,
                }}
              />
              <div className="info-card compact">
                <LockKeyhole size={20} />
                <p>
                  {!s.text_provider || s.text_provider === "none"
                    ? "Extractive mode uses your selected verified facts without sending them to a model."
                    : s.text_provider === "ollama"
                      ? "Ollama sends selected verified facts and role context to your local endpoint."
                      : "Your selected remote provider receives the question, role context, and the verified facts you choose. Review every generated answer."}
                </p>
              </div>
              <div className="panel-actions">
                <Button
                  secondary
                  loading={ctx.busy === "text-test"}
                  onClick={async () => {
                    const value = await ctx.act("text-test", () =>
                      api("/ai/text/test", "POST", {}),
                    );
                    if (value) setTextResult(value);
                  }}
                >
                  <Zap size={15} />
                  Test text provider
                </Button>
                <span className="muted">
                  Uses a synthetic prompt with no CV or profile facts. Remote
                  tests use your configured budget.
                </span>
                <Button
                  secondary
                  onClick={() =>
                    ctx.act("deepseek-pricing", () =>
                      openExternal(
                        "https://api-docs.deepseek.com/quick_start/pricing/",
                      ),
                    )
                  }
                >
                  DeepSeek official pricing
                  <ExternalLink size={13} />
                </Button>
              </div>
              {textResult && (
                <JsonDetails
                  title="Text provider test result"
                  data={textResult}
                  open
                />
              )}
            </Panel>
            <Panel
              title="Provider credentials & cost control"
              description="Credentials are stored in Keychain; price settings support budget enforcement."
            >
              <div className="simple-list">
                {[
                  "jev",
                  "ollama",
                  "openai",
                  "anthropic",
                  "gemini",
                  "openai-compatible",
                ].map((p) => {
                  const integration = items(integrations.data).find(
                    (i) => i.provider === p,
                  );
                  return (
                    <div key={p}>
                      <span className="list-icon">
                        <KeyRound size={17} />
                      </span>
                      <div className="grow">
                        <strong>
                          {p === "openai-compatible"
                            ? "OpenAI-compatible"
                            : label(p)}
                        </strong>
                        <small>
                          {p === "ollama"
                            ? "Local inference"
                            : integration
                              ? "Provider configured"
                              : "Not configured"}
                        </small>
                      </div>
                      <Button secondary onClick={() => setCredential(p)}>
                        Configure
                      </Button>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </>
        )}
        {tab === "backup" && <Backups ctx={ctx} />}
      </Resource>
      {edit && (
        <Editor
          title={
            edit === "automation" ? "Automation boundaries" : "AI configuration"
          }
          fields={edit === "automation" ? automationFields : aiFields}
          initial={{
            default_mode: "review",
            min_match: 85,
            min_confidence: 0.85,
            max_applications_day: 5,
            max_applications_company: 2,
            minimum_interval_seconds: 120,
            discovery_interval_minutes: 120,
            email_interval_minutes: 10,
            browser_engine: "hybrid",
            text_provider: "none",
            ...s,
          }}
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            await api("/settings", "PATCH", values);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Settings saved.",
            );
          }}
        />
      )}
      {credential && (
        <Editor
          title={`Configure ${label(credential)}`}
          description="Your credential is stored only in macOS Keychain. Token prices are required for budget enforcement when using a remote text provider."
          fields={[
            {
              key: "secret",
              label: "API key / access credential",
              type: "password",
              hint: "Leave blank to retain the existing credential.",
            },
            {
              key: "input_cost_per_million",
              label: "Input price per million tokens (USD)",
              type: "number",
              min: 0,
              step: 0.01,
            },
            {
              key: "output_cost_per_million",
              label: "Output price per million tokens (USD)",
              type: "number",
              min: 0,
              step: 0.01,
            },
          ]}
          initial={{
            ...items(integrations.data).find((i) => i.provider === credential)
              ?.config,
            secret: "",
          }}
          onClose={() => setCredential(null)}
          onSave={async (values) => {
            const { secret, ...config } = values;
            await api(`/integrations/${credential}`, "PUT", {
              config,
              ...(secret ? { secret } : {}),
            });
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Provider configuration saved.",
            );
          }}
        />
      )}
      {deepSeek && (
        <Editor
          title="Set up DeepSeek"
          description="Uses the OpenAI-compatible provider slot. Verified 29 September 2026: deepseek-flash, https://api.deepseek.com/v1. Prices below use peak, uncached rates as a conservative budget bound. Enter your own key; no profile is sent by saving."
          fields={[
            {
              key: "secret",
              label: "DeepSeek API key",
              type: "password",
              hint: "Stored only in macOS Keychain. Blank retains the current OpenAI-compatible key; replace it if it belongs to a different provider.",
            },
            {
              key: "monthly_budget",
              label: "Monthly remote AI budget (USD)",
              type: "number",
              min: 0,
              step: 0.01,
              required: true,
              hint: "Set your own limit. Zero blocks paid remote inference.",
            },
            {
              key: "input_cost_per_million",
              label: "Input price per million tokens (USD)",
              type: "number",
              min: 0,
              step: 0.01,
              required: true,
            },
            {
              key: "output_cost_per_million",
              label: "Output price per million tokens (USD)",
              type: "number",
              min: 0,
              step: 0.01,
              required: true,
            },
          ]}
          initial={{
            secret: "",
            monthly_budget: s.monthly_budget ?? 0,
            input_cost_per_million: 0.3,
            output_cost_per_million: 1.2,
          }}
          submit="Save DeepSeek configuration"
          onClose={() => setDeepSeek(false)}
          onSave={async (values) => {
            const { secret, monthly_budget, ...prices } = values;
            const previous =
              items(integrations.data).find(
                (entry) => entry.provider === "openai-compatible",
              )?.config || {};
            await api("/integrations/openai-compatible", "PUT", {
              config: { ...previous, ...prices },
              ...(secret ? { secret } : {}),
            });
            await api("/settings", "PATCH", {
              text_provider: "openai-compatible",
              text_base_url: "https://api.deepseek.com/v1",
              text_model: "deepseek-flash",
              monthly_budget,
            });
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "DeepSeek configuration saved. Use Test text provider to verify the connection.",
            );
          }}
        />
      )}
    </>
  );
}
function Backups({ ctx }: { ctx: AppContext }) {
  const [password, setPassword] = useState(""),
    [restorePassword, setRestorePassword] = useState(""),
    [backup, setBackup] = useState<Data | null>(null),
    [file, setFile] = useState<File | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [error, setError] = useState(""),
    fileRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <Panel
        title="Export an encrypted backup"
        description="Protect your database, settings, documents, and candidate knowledge base"
      >
        <div className="backup-body">
          <div className="info-card">
            <LockKeyhole size={21} />
            <div>
              <strong>Encrypted with a password you control.</strong>
              <p>
                Browser sessions and Keychain credentials are excluded. Keep the
                password safe; Meridian cannot recover it.
              </p>
            </div>
          </div>
          <label className="field">
            <span>Backup password</span>
            <input
              type="password"
              minLength={12}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <small>Use at least 12 characters.</small>
          </label>
          <div className="actions">
            <Button
              disabled={password.length < 12}
              loading={ctx.busy === "export"}
              onClick={async () => {
                const value = await ctx.act(
                  "export",
                  () => api("/backups/export", "POST", { password }),
                  "Encrypted backup created.",
                );
                if (value) {
                  setBackup(value);
                  setPassword("");
                }
              }}
            >
              <Database size={16} />
              Create encrypted backup
            </Button>
            {backup && (
              <Button
                secondary
                onClick={() =>
                  ctx.act("download-backup", () =>
                    download(
                      backup.url || `/backups/${backup.name}/download`,
                      backup.name,
                    ),
                  )
                }
              >
                <ArrowDownToLine size={16} />
                Download {backup.name}
              </Button>
            )}
          </div>
        </div>
      </Panel>
      <Panel
        title="Restore a backup"
        description="Replace this workspace with a previously exported backup"
      >
        <div className="backup-body">
          <div className="info-card warning">
            <Database size={21} />
            <div>
              <strong>Restoring replaces your current local data.</strong>
              <p>
                Export a backup first if you need to keep the current workspace.
                Email and AI credentials may need to be reconnected.
              </p>
            </div>
          </div>
          <input
            className="visually-hidden"
            ref={fileRef}
            type="file"
            accept=".meridian,.enc,.backup,.bin"
            aria-label="Choose encrypted backup"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <Button secondary onClick={() => fileRef.current?.click()}>
            <Upload size={15} />
            {file ? file.name : "Choose backup file"}
          </Button>
          <label className="field">
            <span>Backup password</span>
            <input
              type="password"
              autoComplete="off"
              value={restorePassword}
              onChange={(e) => setRestorePassword(e.target.value)}
            />
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>
              I understand that restoring replaces my current workspace data.
            </span>
          </label>
          {error && <ErrorBox error={error} />}
          <Button
            danger
            disabled={!file || !restorePassword || !confirmed}
            loading={ctx.busy === "restore"}
            onClick={async () => {
              if (!file) return;
              setError("");
              const form = new FormData();
              form.append("file", file);
              form.append("password", restorePassword);
              const result = await ctx.act(
                "restore",
                () => api("/backups/restore", "POST", form),
                "Backup restored.",
              );
              if (result) {
                setRestorePassword("");
                setFile(null);
                setConfirmed(false);
              } else
                setError(
                  "Restore did not complete. Check the error notice and keep your current workspace open.",
                );
            }}
          >
            Restore workspace
          </Button>
        </div>
      </Panel>
    </>
  );
}
function LocalLaya({ ctx }: { ctx: AppContext }) {
  const r = useResource("/ai/local/status", ctx.refresh),
    state = r.data || {};
  const reload = r.reload;
  useEffect(() => {
    if (!["installing", "starting"].includes(state.state)) return;
    const timer = setInterval(reload, 4000);
    return () => clearInterval(timer);
  }, [state.state, reload]);
  return (
    <Panel
      title="Local Laya runtime"
      description="Private browser decisions on your Mac, with no per-token API cost."
    >
      <Resource loading={r.loading && !r.data} error={r.error} retry={reload}>
        <div className="settings-row">
          <div>
            <strong>{state.model || "Laya local decision model"}</strong>
            <p>
              {state.guidance ||
                "Install the runtime, start the model, and test it before enabling model-based automation."}
            </p>
          </div>
          <Status value={state.state || "not installed"} />
        </div>
        <div className="panel-actions">
          {!state.installed ? (
            <Button
              disabled={state.state === "installing"}
              loading={
                ctx.busy === "laya-install" || state.state === "installing"
              }
              onClick={() =>
                ctx.act(
                  "laya-install",
                  () => api("/ai/local/install", "POST", {}),
                  "Laya installation started. Progress appears here.",
                )
              }
            >
              <ArrowDownToLine size={15} />
              Install local Laya (~1.3 GB)
            </Button>
          ) : state.running ? (
            <Button
              secondary
              loading={ctx.busy === "laya-stop"}
              onClick={() =>
                ctx.act(
                  "laya-stop",
                  () => api("/ai/local/stop", "POST", {}),
                  "Local Laya stopped.",
                )
              }
            >
              <Pause size={15} />
              Stop local Laya
            </Button>
          ) : (
            <Button
              loading={ctx.busy === "laya-start"}
              onClick={() =>
                ctx.act(
                  "laya-start",
                  () => api("/ai/local/start", "POST", {}),
                  "Local Laya started.",
                )
              }
            >
              <Play size={15} />
              Start local Laya
            </Button>
          )}
          <Button secondary onClick={reload}>
            <RefreshCw size={15} />
            Refresh status
          </Button>
        </div>
        {state.error && (
          <div className="panel-copy">
            <ErrorBox error={String(state.error)} />
          </div>
        )}
        {state.logs?.length > 0 && (
          <JsonDetails
            title="Local runtime installation log"
            data={state.logs}
          />
        )}
      </Resource>
    </Panel>
  );
}
function Rules({ ctx, settings }: { ctx: AppContext; settings: Data }) {
  const [edit, setEdit] = useState<{ index: number; data: Data } | null>(null),
    [remove, setRemove] = useState<number | null>(null),
    docs = useResource("/documents?limit=500", ctx.refresh),
    rules: Data[] = settings.rules || [];
  const fields: Field[] = [
    { key: "name", label: "Rule name", required: true },
    { key: "enabled", label: "Rule enabled", type: "checkbox" },
    {
      key: "min_match",
      label: "Match is at least (%)",
      type: "number",
      min: 0,
      max: 100,
      required: true,
    },
    { key: "location", label: "Location contains (optional)" },
    { key: "role_contains", label: "Role title contains (optional)" },
    {
      key: "ats",
      label: "ATS equals (optional)",
      type: "select",
      options: [
        "greenhouse",
        "lever",
        "ashby",
        "workday",
        "smartrecruiters",
        "workable",
        "teamtailor",
        "icims",
        "taleo",
        "generic",
      ],
    },
    {
      key: "action",
      label: "Then",
      type: "select",
      options: ["prepare_application", "auto_apply"],
      required: true,
    },
  ];
  return (
    <>
      <Panel
        title="Rule builder"
        description="Conditions are combined with AND. Automatic submission still requires every safety gate."
        action={
          <Button
            secondary
            onClick={() =>
              setEdit({
                index: -1,
                data: {
                  enabled: true,
                  min_match: 85,
                  action: "prepare_application",
                },
              })
            }
          >
            <Plus size={15} />
            Add rule
          </Button>
        }
      >
        {rules.length ? (
          <div className="simple-list">
            {rules.map((rule, i) => (
              <div key={i}>
                <div className="grow">
                  <strong>{rule.name}</strong>
                  <small>
                    IF match ≥ {rule.conditions?.min_match ?? 0}%
                    {rule.conditions?.location &&
                      ` · location contains ${rule.conditions.location}`}
                    {rule.conditions?.role_contains &&
                      ` · role contains ${rule.conditions.role_contains}`}
                    {rule.conditions?.ats && ` · ATS ${rule.conditions.ats}`} →{" "}
                    {label(rule.action)}
                  </small>
                </div>
                <Status value={rule.enabled ? "enabled" : "paused"} />
                <IconButton
                  title={`Edit ${rule.name}`}
                  onClick={() =>
                    setEdit({ index: i, data: { ...rule, ...rule.conditions } })
                  }
                >
                  <Pencil size={15} />
                </IconButton>
                <IconButton
                  title={`Remove ${rule.name}`}
                  onClick={() => setRemove(i)}
                >
                  <Trash2 size={15} />
                </IconButton>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            icon={<ShieldCheck size={24} />}
            title="Make your boundaries explicit"
            description="Create a rule to prepare roles that match your preferences. Turn on scheduled discovery above to evaluate rules in the background."
          />
        )}
      </Panel>
      <Panel
        title="Default CV for scheduled preparation"
        description="Only approved document versions are available for unattended use."
      >
        <div className="form-fields">
          <label className="field">
            <span>Immutable CV version</span>
            <select
              value={settings.default_document_version_id || ""}
              onChange={(e) =>
                ctx.act(
                  "default-document",
                  () =>
                    api("/settings", "PATCH", {
                      default_document_version_id: e.target.value || null,
                    }),
                  "Default CV updated.",
                )
              }
            >
              <option value="">No default — require document review</option>
              {items(docs.data)
                .filter((d) => d.latest_version?.approved)
                .map((d) => (
                  <option key={d.id} value={d.latest_version_id}>
                    {d.name || d.filename} · version {d.latest_version.version}
                  </option>
                ))}
            </select>
            <small>
              Import and approve a CV in Documents before selecting it here.
            </small>
          </label>
        </div>
      </Panel>
      {edit && (
        <Editor
          title={
            edit.index === -1
              ? "Create automation rule"
              : "Edit automation rule"
          }
          fields={fields}
          initial={edit.data}
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            const { name, enabled, action, ...conditions } = values;
            const next = [...rules],
              value = { name, enabled, action, conditions };
            if (edit.index === -1) next.push(value);
            else next[edit.index] = value;
            await api("/settings", "PATCH", { rules: next });
            await ctx.act("refresh", () => Promise.resolve({}), "Rule saved.");
          }}
        />
      )}
      {remove !== null && (
        <Confirm
          title="Remove this rule?"
          description="Previously prepared applications remain in your workspace."
          danger
          button="Remove rule"
          onClose={() => setRemove(null)}
          onConfirm={async () => {
            await api("/settings", "PATCH", {
              rules: rules.filter((_, i) => i !== remove),
            });
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Rule removed.",
            );
          }}
        />
      )}
    </>
  );
}
function AIUsage({ settings }: { settings: Data }) {
  const periods = Object.entries(settings)
    .filter(([key]) => key.startsWith("ai_usage:"))
    .sort(([a], [b]) => b.localeCompare(a));
  const latest = periods[0],
    usage = latest?.[1] || {},
    providers = usage.providers || {};
  return (
    <Panel
      title="AI usage & budget"
      description={
        latest
          ? `Recorded usage · ${latest[0].slice(9)}`
          : "Remote provider usage is recorded locally; local inference has no API charge."
      }
    >
      {latest ? (
        <>
          <div className="settings-row">
            <div>
              <strong>
                {Number(usage.cost || 0).toLocaleString(undefined, {
                  style: "currency",
                  currency: "USD",
                  maximumFractionDigits: 4,
                })}{" "}
                used
              </strong>
              <p>
                {usage.calls ?? 0} calls ·{" "}
                {Number(usage.input_tokens || 0).toLocaleString()} input tokens
                · {Number(usage.output_tokens || 0).toLocaleString()} output
                tokens
              </p>
            </div>
            <Tag>Monthly budget ${settings.monthly_budget ?? 0}</Tag>
          </div>
          <div className="simple-list">
            {Object.entries(providers).map(([provider, value]) => {
              const record =
                typeof value === "object" && value
                  ? (value as Data)
                  : { cost: value };
              return (
                <div key={provider}>
                  <Cpu size={17} />
                  <div className="grow">
                    <strong>{label(provider)}</strong>
                    <small>{record.calls ?? 0} calls</small>
                  </div>
                  <Tag>
                    {Number(record.cost || 0).toLocaleString(undefined, {
                      style: "currency",
                      currency: "USD",
                      maximumFractionDigits: 4,
                    })}
                  </Tag>
                </div>
              );
            })}
          </div>
        </>
      ) : (
        <p className="panel-copy">
          No remote AI usage has been recorded. Current monthly budget: $
          {settings.monthly_budget ?? 0}. Configure provider token prices before
          using a remote engine.
        </p>
      )}
    </Panel>
  );
}
