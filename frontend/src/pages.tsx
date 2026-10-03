import { useEffect, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  BriefcaseBusiness,
  Building2,
  Check,
  CheckCheck,
  Clock3,
  FileText,
  Globe2,
  GraduationCap,
  Inbox,
  LayoutGrid,
  Link2,
  List,
  Mail,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Target,
  Trash2,
  TrendingUp,
  Upload,
} from "lucide-react";
import {
  api,
  date,
  download,
  items,
  label,
  openExternal,
  pretty,
  time,
  useResource,
} from "./api";
import type {
  AppContext,
  Application,
  Data,
  Document,
  Fact,
  Job,
} from "./types";
import {
  AddButton,
  Button,
  Confirm,
  Editor,
  Empty,
  IconButton,
  JsonDetails,
  KeyValues,
  MoreLink,
  Pagination,
  Panel,
  Resource,
  Score,
  SearchInput,
  SectionTitle,
  Status,
  Tag,
  UploadButton,
} from "./components";
import type { Field } from "./components";
import { SetupGuide } from "./onboarding";
import { CandidateIntelligence } from "./intelligence";
import { ApplicationProfile } from "./application-profile";

const STATUS = [
  "DISCOVERED",
  "SCORED",
  "SHORTLISTED",
  "IGNORED",
  "PREPARING",
  "NEEDS_REVIEW",
  "APPLYING",
  "APPLIED",
  "CONFIRMED",
  "RECRUITER_SCREEN",
  "ASSESSMENT",
  "TECHNICAL_TEST",
  "INTERVIEW",
  "FINAL_INTERVIEW",
  "OFFER",
  "REJECTED",
  "WITHDRAWN",
  "GHOSTED",
  "ERROR",
];
export function Dashboard({ ctx }: { ctx: AppContext }) {
  const resource = useResource("/dashboard", ctx.refresh),
    d = resource.data || {},
    s = d.stats || {};
  const cards = [
    {
      title: "Jobs discovered",
      value: s.jobs ?? s.total_jobs ?? s.jobs_discovered ?? 0,
      icon: Search,
      note: "In your opportunity library",
      page: "jobs",
    },
    {
      title: "Strong matches",
      value: s.strong_matches ?? s.high_matches ?? 0,
      icon: Target,
      note: "Aligned with your profile",
      page: "jobs",
    },
    {
      title: "Applications",
      value: s.applications ?? s.total_applications ?? 0,
      icon: BriefcaseBusiness,
      note: "Every step, in one place",
      page: "applications",
    },
    {
      title: "Needs your attention",
      value:
        s.open_tasks ??
        s.human_tasks ??
        s.needs_review ??
        s.action_required ??
        0,
      icon: ShieldCheck,
      note: "Review and move forward",
      page: "review",
    },
  ] as const;
  return (
    <>
      <SectionTitle
        eyebrow="YOUR CAREER, WITH DIRECTION"
        title="A clearer path to your next role."
        description="Discover thoughtfully. Apply confidently. Keep every opportunity in view."
        actions={
          <Button
            loading={ctx.busy === "search"}
            onClick={() =>
              ctx.act(
                "search",
                () => api("/sources/run", "POST", {}),
                "Search completed.",
              )
            }
          >
            <RefreshCw size={16} />
            Run search
          </Button>
        }
      />
      <Resource {...resource} retry={resource.reload}>
        <div className="stat-grid">
          {cards.map((c) => (
            <button
              className="stat-card"
              key={c.title}
              onClick={() => ctx.navigate(c.page)}
            >
              <div className="stat-label">
                {c.title}
                <c.icon size={17} />
              </div>
              <strong>{Number(c.value).toLocaleString()}</strong>
              <span>
                {c.note}
                <ArrowRight size={14} />
              </span>
            </button>
          ))}
        </div>
        <SetupGuide ctx={ctx} />
        <div className="two-col">
          <Panel
            title="Recent activity"
            description="The latest movement in your search"
            action={
              <MoreLink onClick={() => ctx.navigate("activity")}>
                View all
              </MoreLink>
            }
          >
            {(d.recent_events || []).length ? (
              <Timeline events={d.recent_events} />
            ) : (
              <Empty
                icon={<Clock3 size={25} />}
                title="Your story starts here"
                description="Application events, email updates, and progress will appear here as your search moves forward."
              />
            )}
          </Panel>
          <Panel
            title="Source health"
            description="A pulse on your connected sources"
            action={
              <MoreLink onClick={() => ctx.navigate("discover")}>
                Manage sources
              </MoreLink>
            }
          >
            {(d.source_health || []).length ? (
              <div className="simple-list">
                {d.source_health.map((source: Data) => (
                  <div key={source.id || source.name}>
                    <div className="list-icon">
                      <Globe2 size={17} />
                    </div>
                    <div className="grow">
                      <strong>{source.name}</strong>
                      <small>
                        {source.last_error ||
                          `Last checked ${time(source.last_checked)}`}
                      </small>
                    </div>
                    <Status
                      value={
                        source.last_error
                          ? "error"
                          : source.last_checked
                            ? "connected"
                            : "not checked"
                      }
                    />
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={<Globe2 size={25} />}
                title="Connect your first source"
                description="Add a company careers page, public ATS board, or GitHub jobs repository."
                action={
                  <Button secondary onClick={() => ctx.navigate("discover")}>
                    Add a source
                    <ArrowRight size={14} />
                  </Button>
                }
              />
            )}
          </Panel>
        </div>
        {d.deadlines?.length > 0 && (
          <Panel title="Upcoming deadlines">
            <div className="simple-list">
              {d.deadlines.map((v: Data, i: number) => (
                <div key={v.id || i}>
                  <Clock3 size={17} />
                  <div className="grow">
                    <strong>
                      {v.title || v.subject || "Application deadline"}
                    </strong>
                    <small>{v.company}</small>
                  </div>
                  <Tag tone="amber">{time(v.deadline || v.due_at)}</Tag>
                </div>
              ))}
            </div>
          </Panel>
        )}
      </Resource>
    </>
  );
}
export function Timeline({ events }: { events: Data[] }) {
  return (
    <div className="timeline">
      {events.map((e, i) => (
        <div className="timeline-item" key={e.id || i}>
          <div className="timeline-dot" />
          <div>
            <div className="row-between">
              <strong>
                {e.message ||
                  e.summary ||
                  e.subject ||
                  label(e.status || e.type)}
              </strong>
              <time>{time(e.created_at || e.timestamp)}</time>
            </div>
            <p>{[e.company, e.title, e.origin].filter(Boolean).join(" · ")}</p>
            {e.status && <Status value={e.status} />}
          </div>
        </div>
      ))}
    </div>
  );
}
export function Jobs({ ctx }: { ctx: AppContext }) {
  const [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [page, setPage] = useState(0),
    [urlModal, setUrlModal] = useState(false);
  const resource = useResource(
      `/jobs?limit=50&offset=${page * 50}&q=${encodeURIComponent(q)}&status=${status}`,
      ctx.refresh,
    ),
    jobs = items<Job>(resource.data);
  return (
    <>
      <SectionTitle
        eyebrow="OPPORTUNITY LIBRARY"
        title="Jobs"
        description="A considered collection of opportunities, matched to your verified experience."
        actions={
          <AddButton onClick={() => setUrlModal(true)}>Add job URL</AddButton>
        }
      />
      <div className="toolbar">
        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            setPage(0);
          }}
          placeholder="Search roles, companies, or skills"
        />
        <select
          aria-label="Filter by job status"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(0);
          }}
        >
          <option value="">All statuses</option>
          {STATUS.map((s) => (
            <option key={s} value={s}>
              {label(s)}
            </option>
          ))}
        </select>
        <Tag>{resource.data?.total ?? jobs.length} opportunities</Tag>
      </div>
      <Panel>
        <Resource {...resource} retry={resource.reload}>
          {jobs.length ? (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Opportunity</th>
                      <th>Location</th>
                      <th>Match</th>
                      <th>Status</th>
                      <th>Source</th>
                      <th>Added</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j) => (
                      <tr
                        key={j.id}
                        className="clickable"
                        onClick={() => ctx.select({ kind: "job", id: j.id })}
                      >
                        <td>
                          <button
                            className="table-title"
                            onClick={(e) => {
                              e.stopPropagation();
                              ctx.select({ kind: "job", id: j.id });
                            }}
                          >
                            <span className="company-avatar">
                              {j.company?.slice(0, 1) || "J"}
                            </span>
                            <span>
                              <strong>{j.title}</strong>
                              <small>{j.company}</small>
                            </span>
                          </button>
                        </td>
                        <td>
                          {j.location || "Not specified"}
                          {j.remote && <small>Remote</small>}
                        </td>
                        <td>
                          <Score value={j.match_score} />
                        </td>
                        <td>
                          <Status value={j.status} />
                        </td>
                        <td>
                          <span className="muted">
                            {j.ats || j.source || "Direct"}
                          </span>
                        </td>
                        <td className="muted">{date(j.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                total={resource.data?.total ?? jobs.length}
                onChange={setPage}
              />
            </>
          ) : (
            <Empty
              icon={<BriefcaseBusiness size={26} />}
              title={
                q
                  ? "No matching opportunities"
                  : "Your next opportunity belongs here"
              }
              description={
                q
                  ? "Try a broader search or change the status filter."
                  : "Paste a job URL or connect your sources to discover roles that fit your experience."
              }
              action={
                <Button secondary onClick={() => setUrlModal(true)}>
                  <Link2 size={16} />
                  Paste a job URL
                </Button>
              }
            />
          )}
        </Resource>
      </Panel>
      {urlModal && (
        <Editor
          title="Add an opportunity"
          description="Paste a public job posting. Meridian extracts the role, checks for duplicates, and calculates its match."
          fields={[
            {
              key: "url",
              label: "Job posting URL",
              type: "url",
              required: true,
              placeholder: "https://…",
            },
          ]}
          onClose={() => setUrlModal(false)}
          onSave={async (values) => {
            const result = await api("/jobs/ingest", "POST", values);
            await ctx.act(
              "refresh",
              () => Promise.resolve(result),
              "Opportunity added.",
            );
            if (result.id || result.job?.id)
              ctx.select({ kind: "job", id: result.id || result.job.id });
          }}
          submit="Add to opportunities"
        />
      )}
    </>
  );
}
const sourceFields: Field[] = [
  { key: "name", label: "Source name", required: true },
  {
    key: "kind",
    label: "Source type",
    type: "select",
    options: [
      "greenhouse",
      "lever",
      "ashby",
      "smartrecruiters",
      "workable",
      "teamtailor",
      "icims",
      "taleo",
      "workday",
      "careers",
      "github",
      "trackr",
      "linkedin",
      "indeed",
      "json",
      "csv",
      "html",
    ],
    required: true,
  },
  {
    key: "url",
    label: "Board, careers page, or repository URL",
    type: "url",
    required: true,
  },
  { key: "enabled", label: "Enable scheduled discovery", type: "checkbox" },
  {
    key: "poll_minutes",
    label: "Check interval (minutes)",
    type: "number",
    min: 15,
  },
  {
    key: "config",
    label: "Source configuration",
    type: "json",
    hint: "Provider options, such as repository, branch, paths, parser, or search filters.",
  },
];
const weightKeys = [
  "skills",
  "role",
  "location",
  "seniority",
  "salary",
  "technology_preference",
  "education",
  "experience",
] as const;
const searchFields: Field[] = [
  { key: "name", label: "Search profile name", required: true },
  {
    key: "roles",
    label: "Roles",
    type: "list",
    hint: "Comma-separated titles.",
  },
  { key: "keywords", label: "Keywords", type: "list" },
  { key: "negative_keywords", label: "Exclude keywords", type: "list" },
  { key: "locations", label: "Locations", type: "list" },
  {
    key: "remote",
    label: "Remote preference",
    type: "select",
    options: ["any", "remote", "onsite"],
  },
  {
    key: "minimum_salary",
    label: "Minimum annual salary",
    type: "number",
    min: 0,
  },
  {
    key: "salary_currency",
    label: "Salary currency (ISO code)",
    placeholder: "GBP",
    hint: "Optional three-letter code, such as GBP or EUR. Only explicitly annual salaries in the same currency are compared; other salary values remain unknown.",
  },
  { key: "experience_levels", label: "Experience levels", type: "list" },
  { key: "companies", label: "Preferred companies", type: "list" },
  { key: "excluded_companies", label: "Exclude companies", type: "list" },
  { key: "technologies", label: "Technologies", type: "list" },
  {
    key: "industries",
    label: "Industries",
    type: "list",
    hint: "Optional comma-separated industries. Missing advertised information remains unknown.",
  },
  {
    key: "work_arrangements",
    label: "Work arrangements",
    type: "multiselect",
    options: ["remote", "hybrid", "onsite"],
    hint: "Leave empty to accept any arrangement. Hold ⌘ to select or clear multiple choices.",
  },
  {
    key: "contract_types",
    label: "Contract types",
    type: "multiselect",
    options: [
      "full_time",
      "part_time",
      "contract",
      "temporary",
      "internship",
      "volunteer",
      "other",
    ],
    hint: "Leave empty to accept any contract. Hold ⌘ to select or clear multiple choices.",
  },
  {
    key: "max_posting_age_days",
    label: "Maximum posting age (days)",
    type: "number",
    min: 1,
    max: 3650,
    hint: "Optional. Offers without a known publication date remain unknown.",
  },
  {
    key: "requires_sponsorship",
    label: "Visa sponsorship preference",
    type: "select",
    options: [
      { value: "unspecified", label: "Not specified" },
      { value: "required", label: "I need visa sponsorship" },
      {
        value: "not_required",
        label: "I do not need sponsorship for this search",
      },
    ],
    hint: "A search preference, not a verified work-authorization fact. Requiring sponsorship excludes only roles that explicitly say they cannot provide it.",
  },
  {
    key: "mode",
    label: "Application mode",
    type: "select",
    options: ["manual", "review", "auto"],
    required: true,
  },
  {
    key: "min_match",
    label: "Minimum match (%)",
    type: "number",
    min: 0,
    max: 100,
  },
  {
    key: "stretch_factor",
    label: "Stretch factor (0–1)",
    type: "number",
    min: 0,
    max: 1,
    step: 0.05,
  },
  ...weightKeys.map((key, index): Field => ({
    key: `weight_${key}`,
    label: `${label(key)} weight`,
    type: "number",
    min: 0,
    step: 0.1,
    hint:
      index === 0
        ? "Leave every weight blank to use the default mix. Setting any weight replaces the complete mix: blank categories receive zero, and the result is normalized to 100."
        : undefined,
  })),
];
export function Discover({ ctx }: { ctx: AppContext }) {
  const sources = useResource("/sources", ctx.refresh),
    profiles = useResource("/search-profiles", ctx.refresh),
    [edit, setEdit] = useState<{
      kind: "source" | "profile";
      data?: Data;
    } | null>(null),
    [deleting, setDeleting] = useState<{ kind: string; data: Data } | null>(
      null,
    ),
    [result, setResult] = useState<Data | null>(null);
  useEffect(() => {
    if (ctx.target === "sources")
      document
        .getElementById("discovery-sources")
        ?.scrollIntoView({ block: "start" });
  }, [ctx.target]);
  return (
    <>
      <SectionTitle
        eyebrow="BUILD YOUR SEARCH"
        title="Discover"
        description="Your preferences set the direction. Your sources bring the opportunities."
        actions={
          <Button
            loading={ctx.busy === "search"}
            onClick={async () => {
              const r = await ctx.act(
                "search",
                () => api("/sources/run", "POST", {}),
                "Discovery finished.",
              );
              if (r) setResult(r);
            }}
          >
            <Play size={16} />
            Run search
          </Button>
        }
      />
      {result && (
        <Panel
          title="Last search result"
          action={
            <button className="text-button" onClick={() => setResult(null)}>
              Dismiss
            </button>
          }
        >
          <KeyValues data={result} />
        </Panel>
      )}
      <Panel
        title="Search profiles"
        description="Editable rules for the roles you want to find"
        action={
          <AddButton onClick={() => setEdit({ kind: "profile" })}>
            New profile
          </AddButton>
        }
      >
        <Resource {...profiles} retry={profiles.reload}>
          {items(profiles.data).length ? (
            <div className="profile-grid">
              {items(profiles.data).map((p) => (
                <div className="search-profile" key={p.id}>
                  <div className="row-between">
                    <span className="card-symbol">
                      <Target size={18} />
                    </span>
                    <div className="actions">
                      <IconButton
                        title={`Edit ${p.name}`}
                        onClick={() => setEdit({ kind: "profile", data: p })}
                      >
                        <Pencil size={15} />
                      </IconButton>
                      <IconButton
                        title={`Delete ${p.name}`}
                        onClick={() =>
                          setDeleting({ kind: "search-profiles", data: p })
                        }
                      >
                        <Trash2 size={15} />
                      </IconButton>
                    </div>
                  </div>
                  <h3>{p.name}</h3>
                  <p>{(p.config?.roles || []).join(" · ") || "All roles"}</p>
                  <div className="tags">
                    <Tag>{label(p.config?.mode || "review")} mode</Tag>
                    <Tag>Match ≥ {p.config?.min_match ?? 0}%</Tag>
                    {(p.config?.locations || [])
                      .slice(0, 3)
                      .map((s: string) => (
                        <Tag key={s}>{s}</Tag>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              icon={<Target size={25} />}
              title="Give your search a direction"
              description="Set the roles, locations, skills, and match threshold you care about. Create more than one profile to explore different paths."
              action={
                <Button secondary onClick={() => setEdit({ kind: "profile" })}>
                  Create search profile
                </Button>
              }
            />
          )}
        </Resource>
      </Panel>
      <Panel
        id="discovery-sources"
        title="Discovery sources"
        description="Public job boards, company pages, and curated repositories"
        action={
          <AddButton onClick={() => setEdit({ kind: "source" })}>
            Add source
          </AddButton>
        }
      >
        <Resource {...sources} retry={sources.reload}>
          {items(sources.data).length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Type</th>
                    <th>Last checked</th>
                    <th>Jobs found</th>
                    <th>Health</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items(sources.data).map((s) => (
                    <tr key={s.id}>
                      <td>
                        <strong>{s.name}</strong>
                        <small className="ellipsis" title={s.url}>
                          {s.url}
                        </small>
                        {s.last_error && (
                          <small className="error-text">{s.last_error}</small>
                        )}
                      </td>
                      <td>
                        <Tag>{label(s.kind)}</Tag>
                      </td>
                      <td>{time(s.last_checked)}</td>
                      <td>{s.jobs_found ?? 0}</td>
                      <td>
                        <Status
                          value={
                            !s.enabled
                              ? "paused"
                              : s.config?.delivery === "email"
                                ? s.last_checked
                                  ? "receiving alerts"
                                  : "awaiting alerts"
                                : s.last_error
                                  ? "error"
                                  : s.last_checked
                                    ? "connected"
                                    : "not checked"
                          }
                        />
                      </td>
                      <td>
                        <div className="actions">
                          <IconButton
                            title={
                              s.config?.delivery === "email"
                                ? "Received through inbox sync"
                                : `Run ${s.name}`
                            }
                            disabled={
                              !!ctx.busy || s.config?.delivery === "email"
                            }
                            onClick={() =>
                              ctx.act(
                                `source:${s.id}`,
                                () =>
                                  api("/sources/run", "POST", {
                                    source_id: s.id,
                                  }),
                                "Source checked.",
                              )
                            }
                          >
                            <RefreshCw
                              size={15}
                              className={
                                ctx.busy === `source:${s.id}` ? "spin" : ""
                              }
                            />
                          </IconButton>
                          <IconButton
                            title={`Edit ${s.name}`}
                            onClick={() => setEdit({ kind: "source", data: s })}
                          >
                            <Pencil size={15} />
                          </IconButton>
                          <IconButton
                            title={`Delete ${s.name}`}
                            onClick={() =>
                              setDeleting({ kind: "sources", data: s })
                            }
                          >
                            <Trash2 size={15} />
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              icon={<Globe2 size={25} />}
              title="Find opportunities at the source"
              description="Add a public ATS board or careers page. Restricted platforms use your browser session and may require manual interaction."
              action={
                <Button secondary onClick={() => setEdit({ kind: "source" })}>
                  Add your first source
                </Button>
              }
            />
          )}
        </Resource>
      </Panel>
      {edit && (
        <Editor
          title={`${edit.data ? "Edit" : "Create"} ${edit.kind === "source" ? "discovery source" : "search profile"}`}
          fields={edit.kind === "source" ? sourceFields : searchFields}
          initial={
            edit.kind === "source"
              ? {
                  kind: "careers",
                  enabled: true,
                  poll_minutes: 60,
                  config: {},
                  ...edit.data,
                }
              : {
                  mode: "review",
                  min_match: 70,
                  stretch_factor: 0.2,
                  ...edit.data?.config,
                  remote:
                    edit.data?.config?.remote === true
                      ? "remote"
                      : edit.data?.config?.remote === false
                        ? "onsite"
                        : "any",
                  requires_sponsorship:
                    edit.data?.config?.requires_sponsorship === true
                      ? "required"
                      : edit.data?.config?.requires_sponsorship === false
                        ? "not_required"
                        : "unspecified",
                  ...Object.fromEntries(
                    weightKeys.map((key) => [
                      `weight_${key}`,
                      edit.data?.config?.weights?.[key] ?? "",
                    ]),
                  ),
                  name: edit.data?.name,
                }
          }
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            const endpoint =
              edit.kind === "source" ? "sources" : "search-profiles";
            let payload = values;
            if (edit.kind === "profile") {
              const { name, ...config } = values;
              config.requires_sponsorship =
                config.requires_sponsorship === "required"
                  ? true
                  : config.requires_sponsorship === "not_required"
                    ? false
                    : null;
              const weights: Record<string, number> = {};
              for (const key of weightKeys) {
                const value = config[`weight_${key}`];
                if (value != null && value !== "") weights[key] = Number(value);
                delete config[`weight_${key}`];
              }
              if (
                Object.keys(weights).length &&
                Object.values(weights).reduce(
                  (total, value) => total + value,
                  0,
                ) <= 0
              )
                throw new Error(
                  "Custom matching weights need at least one positive value.",
                );
              config.weights = weights;
              config.salary_currency =
                String(config.salary_currency || "")
                  .trim()
                  .toUpperCase() || null;
              if (
                config.salary_currency &&
                !/^[A-Z]{3}$/.test(config.salary_currency)
              ) {
                throw new Error(
                  "Use a three-letter ISO currency code, such as GBP or EUR.",
                );
              }
              config.remote =
                config.remote === "remote"
                  ? true
                  : config.remote === "onsite"
                    ? false
                    : null;
              payload = { name, config };
            }
            await api(
              `/${endpoint}${edit.data ? `/${edit.data.id}` : ""}`,
              edit.data ? "PATCH" : "POST",
              payload,
            );
            await ctx.act("refresh", () => Promise.resolve({}), "Saved.");
          }}
        />
      )}
      {deleting && (
        <Confirm
          title={`Delete ${deleting.data.name}?`}
          description="This removes the configuration. Previously discovered jobs remain in your library."
          danger
          button="Delete"
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api(`/${deleting.kind}/${deleting.data.id}`, "DELETE");
            await ctx.act("refresh", () => Promise.resolve({}), "Deleted.");
          }}
        />
      )}
    </>
  );
}
export function Applications({
  ctx,
  review = false,
}: {
  ctx: AppContext;
  review?: boolean;
}) {
  const [view, setView] = useState<"board" | "list">("board"),
    [q, setQ] = useState(""),
    [page, setPage] = useState(0),
    [filter, setFilter] = useState("");
  const resource = useResource(
      `/applications?limit=50&offset=${page * 50}&q=${encodeURIComponent(q)}&status=${review ? "NEEDS_REVIEW" : filter}`,
      ctx.refresh,
    ),
    tasks = useResource(review ? "/human-tasks" : null, ctx.refresh),
    [resolve, setResolve] = useState<Data | null>(null);
  const apps = items<Application>(resource.data),
    groups = [
      {
        title: "Prepared",
        statuses: ["PREPARING", "NEEDS_REVIEW"],
        tone: "amber",
      },
      {
        title: "Applied",
        statuses: ["APPLYING", "APPLIED", "CONFIRMED"],
        tone: "blue",
      },
      {
        title: "In conversation",
        statuses: [
          "RECRUITER_SCREEN",
          "ASSESSMENT",
          "TECHNICAL_TEST",
          "INTERVIEW",
          "FINAL_INTERVIEW",
        ],
        tone: "purple",
      },
      {
        title: "Outcome",
        statuses: ["OFFER", "REJECTED", "WITHDRAWN", "GHOSTED", "ERROR"],
        tone: "green",
      },
    ];
  const displayedGroups = [
    ...groups,
    ...(apps.some((a) => !groups.some((g) => g.statuses.includes(a.status)))
      ? [
          {
            title: "Other",
            statuses: apps
              .filter((a) => !groups.some((g) => g.statuses.includes(a.status)))
              .map((a) => a.status),
            tone: "neutral",
          },
        ]
      : []),
  ];
  return (
    <>
      <SectionTitle
        eyebrow={review ? "YOU HAVE THE FINAL SAY" : "YOUR SEARCH IN MOTION"}
        title={review ? "Review queue" : "Applications"}
        description={
          review
            ? "Review the details, answer open questions, and approve each submission with confidence."
            : "Every opportunity, conversation, and next step — with a complete history."
        }
      />
      {review && (
        <Panel
          title="Human input required"
          description="Answers here can be saved for future applications"
        >
          <Resource {...tasks} retry={tasks.reload}>
            {items(tasks.data).filter(
              (t) =>
                !["resolved", "completed"].includes(
                  String(t.status).toLowerCase(),
                ),
            ).length ? (
              <div className="simple-list">
                {items(tasks.data)
                  .filter(
                    (t) =>
                      !["resolved", "completed"].includes(
                        String(t.status).toLowerCase(),
                      ),
                  )
                  .map((t) => (
                    <div key={t.id}>
                      <span className="list-icon amber">
                        <ShieldCheck size={18} />
                      </span>
                      <div className="grow">
                        <strong>
                          {t.question || t.title || label(t.kind)}
                        </strong>
                        <small>
                          {t.reason ||
                            t.message ||
                            t.description ||
                            t.application_id}
                        </small>
                      </div>
                      <Button secondary onClick={() => setResolve(t)}>
                        Respond
                        <ArrowRight size={14} />
                      </Button>
                    </div>
                  ))}
              </div>
            ) : (
              <Empty
                icon={<CheckCheck size={25} />}
                title="No open questions"
                description="When an application needs information, a sensitive answer, or human verification, it will appear here."
              />
            )}
          </Resource>
        </Panel>
      )}
      <div className="toolbar">
        <SearchInput
          value={q}
          onChange={(v) => {
            setQ(v);
            setPage(0);
          }}
          placeholder="Search applications"
        />
        {!review && (
          <select
            aria-label="Application status"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setPage(0);
            }}
          >
            <option value="">All statuses</option>
            {STATUS.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
        )}
        <div className="segmented" aria-label="View">
          <button
            aria-label="Board view"
            aria-pressed={view === "board"}
            className={view === "board" ? "selected" : ""}
            onClick={() => setView("board")}
          >
            <LayoutGrid size={17} />
          </button>
          <button
            aria-label="List view"
            aria-pressed={view === "list"}
            className={view === "list" ? "selected" : ""}
            onClick={() => setView("list")}
          >
            <List size={17} />
          </button>
        </div>
      </div>
      <Resource {...resource} retry={resource.reload}>
        {apps.length ? (
          view === "board" && !review ? (
            <div className="kanban">
              {displayedGroups.map((g) => (
                <section className="kanban-column" key={g.title}>
                  <div className="kanban-heading">
                    <span className={`dot ${g.tone}`} />
                    <h3>{g.title}</h3>
                    <span>
                      {apps.filter((a) => g.statuses.includes(a.status)).length}
                    </span>
                  </div>
                  <div className="kanban-items">
                    {apps
                      .filter((a) => g.statuses.includes(a.status))
                      .map((a) => (
                        <button
                          className="application-card"
                          key={a.id}
                          onClick={() =>
                            ctx.select({ kind: "application", id: a.id })
                          }
                        >
                          <div className="row-between">
                            <span className="company-avatar">
                              {(a.company || a.job?.company || "A").slice(0, 1)}
                            </span>
                            <MoreHorizontal size={18} />
                          </div>
                          <strong>
                            {a.title || a.job?.title || "Application"}
                          </strong>
                          <p>
                            {a.company ||
                              a.job?.company ||
                              "Company not specified"}
                          </p>
                          <Status value={a.status} />
                          <div className="application-meta">
                            <span>{date(a.updated_at || a.created_at)}</span>
                            <span>{label(a.mode || "review")}</span>
                          </div>
                        </button>
                      ))}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <Panel>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Application</th>
                      <th>Status</th>
                      <th>Mode</th>
                      <th>Updated</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {apps.map((a) => (
                      <tr key={a.id}>
                        <td>
                          <button
                            className="table-title"
                            onClick={() =>
                              ctx.select({ kind: "application", id: a.id })
                            }
                          >
                            <span className="company-avatar">
                              {(a.company || a.job?.company || "A").slice(0, 1)}
                            </span>
                            <span>
                              <strong>
                                {a.title || a.job?.title || "Application"}
                              </strong>
                              <small>{a.company || a.job?.company}</small>
                            </span>
                          </button>
                        </td>
                        <td>
                          <Status value={a.status} />
                        </td>
                        <td>{label(a.mode || "review")}</td>
                        <td>{time(a.updated_at || a.created_at)}</td>
                        <td>
                          <Button
                            secondary
                            onClick={() =>
                              ctx.select({ kind: "application", id: a.id })
                            }
                          >
                            {review ? "Review" : "Open"}
                            <ArrowRight size={14} />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )
        ) : (
          <Panel>
            <Empty
              icon={
                review ? (
                  <ShieldCheck size={26} />
                ) : (
                  <BriefcaseBusiness size={26} />
                )
              }
              title={
                review
                  ? "You’re all caught up"
                  : "Your pipeline, ready for its first role"
              }
              description={
                review
                  ? "Applications waiting for approval will appear here. You can review their answers, attachments, and quality checks before submitting."
                  : "Prepare an opportunity from your job library to start tracking its journey."
              }
              action={
                <Button secondary onClick={() => ctx.navigate("jobs")}>
                  Explore opportunities
                  <ArrowRight size={14} />
                </Button>
              }
            />
          </Panel>
        )}
        {apps.length > 0 && (
          <Pagination
            page={page}
            total={resource.data?.total ?? apps.length}
            onChange={setPage}
          />
        )}
      </Resource>
      {resolve && (
        <Editor
          title="Provide the missing information"
          description={resolve.question || resolve.title || resolve.reason}
          fields={[
            {
              key: "answer",
              label: "Your answer",
              type: "textarea",
              required: true,
            },
            {
              key: "save",
              label: "Save as a verified answer for future applications",
              type: "checkbox",
            },
          ]}
          initial={{ save: false }}
          submit="Save answer"
          onClose={() => setResolve(null)}
          onSave={async (values) => {
            await api(`/human-tasks/${resolve.id}/resolve`, "POST", values);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Answer saved.",
            );
          }}
        />
      )}
    </>
  );
}
const companyFields: Field[] = [
  { key: "name", label: "Company name", required: true },
  { key: "careers_url", label: "Careers page", type: "url" },
  { key: "locations", label: "Locations", type: "list" },
  { key: "preferred_roles", label: "Preferred roles", type: "list" },
  {
    key: "priority",
    label: "Priority (0–100)",
    type: "number",
    min: 0,
    max: 100,
  },
  {
    key: "poll_minutes",
    label: "Check interval (minutes)",
    type: "number",
    min: 15,
  },
  { key: "salary_preference", label: "Salary preference", type: "json" },
  { key: "blacklist_keywords", label: "Excluded keywords", type: "list" },
  {
    key: "auto_apply",
    label: "Allow Auto mode for this company (global safety gates still apply)",
    type: "checkbox",
  },
  { key: "notes", label: "Notes", type: "textarea" },
];
export function Companies({ ctx }: { ctx: AppContext }) {
  const r = useResource("/companies", ctx.refresh),
    [q, setQ] = useState(""),
    [edit, setEdit] = useState<Data | null>(null),
    [del, setDel] = useState<Data | null>(null),
    companies = items(r.data).filter((c) =>
      `${c.name} ${c.notes}`.toLowerCase().includes(q.toLowerCase()),
    );
  return (
    <>
      <SectionTitle
        eyebrow="COMPANY WATCHLIST"
        title="Companies"
        description="Keep the places you’d love to work within reach."
        actions={<AddButton onClick={() => setEdit({})}>Add company</AddButton>}
      />
      <div className="toolbar">
        <SearchInput value={q} onChange={setQ} placeholder="Find a company" />
        <Tag>{companies.length} companies</Tag>
      </div>
      <Resource {...r} retry={r.reload}>
        {companies.length ? (
          <div className="company-grid">
            {companies.map((c) => (
              <Panel key={c.id}>
                <div className="company-card">
                  <div className="row-between">
                    <span className="company-avatar large">
                      {c.name.slice(0, 1)}
                    </span>
                    <div className="actions">
                      <IconButton
                        title={`Edit ${c.name}`}
                        onClick={() => setEdit(c)}
                      >
                        <Pencil size={15} />
                      </IconButton>
                      <IconButton
                        title={`Delete ${c.name}`}
                        onClick={() => setDel(c)}
                      >
                        <Trash2 size={15} />
                      </IconButton>
                    </div>
                  </div>
                  <h3>{c.name}</h3>
                  <p>
                    {(c.locations || []).join(" · ") ||
                      "No location preference"}
                  </p>
                  <div className="tags">
                    <Tag>Priority {c.priority ?? 50}</Tag>
                    {c.auto_apply && <Tag tone="amber">Auto eligible</Tag>}
                  </div>
                  <p className="company-notes">
                    {c.notes ||
                      "Add notes about what makes this company interesting to you."}
                  </p>
                  {c.careers_url && (
                    <Button
                      secondary
                      onClick={() =>
                        ctx.act(
                          "browser",
                          () =>
                            api("/browser/open", "POST", {
                              url: c.careers_url,
                            }),
                          "Careers page opened in your browser.",
                        )
                      }
                    >
                      <Globe2 size={15} />
                      Open careers
                    </Button>
                  )}
                </div>
              </Panel>
            ))}
          </div>
        ) : (
          <Panel>
            <Empty
              icon={<Building2 size={25} />}
              title="Build your company shortlist"
              description="Add companies you care about, their careers pages, and the roles you want to follow."
              action={
                <Button secondary onClick={() => setEdit({})}>
                  Add a company
                </Button>
              }
            />
          </Panel>
        )}
      </Resource>
      {edit && (
        <Editor
          title={edit.id ? "Edit company" : "Add company"}
          fields={companyFields}
          initial={{
            priority: 50,
            poll_minutes: 120,
            salary_preference: {},
            ...edit,
          }}
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            await api(
              `/companies${edit.id ? `/${edit.id}` : ""}`,
              edit.id ? "PATCH" : "POST",
              values,
            );
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Company saved.",
            );
          }}
        />
      )}
      {del && (
        <Confirm
          title={`Remove ${del.name}?`}
          description="This removes the company from your watchlist. Its jobs and applications are retained."
          danger
          button="Remove company"
          onClose={() => setDel(null)}
          onConfirm={async () => {
            await api(`/companies/${del.id}`, "DELETE");
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Company removed.",
            );
          }}
        />
      )}
    </>
  );
}
const factFields: Field[] = [
  {
    key: "category",
    label: "Category",
    type: "select",
    options: [
      "personal",
      "education",
      "experience",
      "project",
      "skill",
      "language",
      "certification",
      "link",
      "availability",
      "location",
      "work_authorization",
      "preference",
      "salary",
      "answer",
      "imported",
      "other",
    ],
    required: true,
  },
  { key: "key", label: "Fact name", required: true },
  { key: "value", label: "Value", type: "textarea", required: true },
  { key: "source", label: "Source / evidence" },
  {
    key: "verification_status",
    label: "Verification",
    type: "select",
    options: ["unverified", "verified", "rejected"],
    required: true,
  },
  {
    key: "locked",
    label: "Lock this fact for automatic use",
    type: "checkbox",
  },
  { key: "notes", label: "Notes", type: "textarea" },
];
export function Profile({ ctx }: { ctx: AppContext }) {
  const [q, setQ] = useState(""),
    [category, setCategory] = useState(""),
    [verification, setVerification] = useState(
      ctx.target === "review" ? "unverified" : "",
    ),
    [edit, setEdit] = useState<Data | null>(null),
    [del, setDel] = useState<Fact | null>(null),
    [relationship, setRelationship] = useState(false),
    r = useResource("/facts?limit=500", ctx.refresh),
    relationships = useResource("/relationships", ctx.refresh),
    facts = items<Fact>(r.data),
    filtered = facts.filter(
      (f) =>
        (!category || f.category === category) &&
        (!verification || f.verification_status === verification) &&
        `${f.key} ${pretty(f.value)} ${f.category}`
          .toLowerCase()
          .includes(q.toLowerCase()),
    );
  return (
    <>
      <SectionTitle
        eyebrow="CANDIDATE KNOWLEDGE BASE"
        title="Your profile"
        description="Your experience is the source of truth. Only verified or locked facts can be used automatically."
        actions={
          <>
            <Button secondary onClick={() => ctx.navigate("documents")}>
              <Upload size={15} />
              Import CV
            </Button>
            <AddButton onClick={() => setEdit({})}>Add fact</AddButton>
          </>
        }
      />
      <div className="profile-summary">
        <span className="profile-symbol">
          <GraduationCap size={26} />
        </span>
        <div>
          <h3>A profile built on evidence</h3>
          <p>
            {
              facts.filter(
                (f) => f.verification_status === "verified" || f.locked,
              ).length
            }{" "}
            verified facts ·{" "}
            {
              facts.filter(
                (f) => f.verification_status === "unverified" && !f.locked,
              ).length
            }{" "}
            need review
          </p>
        </div>
        <Tag tone="green">
          <ShieldCheck size={13} />
          Private on this Mac
        </Tag>
      </div>
      <CandidateIntelligence ctx={ctx} />
      <ApplicationProfile ctx={ctx} />
      <div className="toolbar">
        <SearchInput
          value={q}
          onChange={setQ}
          placeholder="Search facts, skills, or experience"
        />
        <select
          aria-label="Fact verification"
          value={verification}
          onChange={(e) => setVerification(e.target.value)}
        >
          <option value="">All verification states</option>
          <option value="unverified">Needs review</option>
          <option value="verified">Verified</option>
          <option value="rejected">Rejected</option>
        </select>
        <select
          aria-label="Fact category"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">All categories</option>
          {[...new Set(facts.map((f) => f.category))].sort().map((c) => (
            <option value={c} key={c}>
              {label(c)}
            </option>
          ))}
        </select>
      </div>
      <Panel>
        <Resource {...r} retry={r.reload}>
          {filtered.length ? (
            <div className="table-scroll">
              <table className="facts-table">
                <thead>
                  <tr>
                    <th>Fact</th>
                    <th>Value & evidence</th>
                    <th>Verification</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((f) => (
                    <tr key={f.id}>
                      <td>
                        <strong>{label(f.key)}</strong>
                        <small>{label(f.category)}</small>
                      </td>
                      <td>
                        <div className="fact-value">{pretty(f.value)}</div>
                        <small>
                          {f.source || "Manually provided"}
                          {f.notes && ` · ${f.notes}`}
                        </small>
                      </td>
                      <td>
                        <Status value={f.verification_status} />
                        {f.locked && <Tag>Locked</Tag>}
                      </td>
                      <td>
                        <div className="actions">
                          {f.verification_status !== "verified" && (
                            <IconButton
                              title={`Verify ${f.key}`}
                              disabled={!!ctx.busy}
                              onClick={() =>
                                ctx.act(
                                  `verify:${f.id}`,
                                  () =>
                                    api(`/facts/${f.id}`, "PATCH", {
                                      verification_status: "verified",
                                    }),
                                  "Fact verified.",
                                )
                              }
                            >
                              <Check size={16} />
                            </IconButton>
                          )}
                          <IconButton
                            title={`Edit ${f.key}`}
                            onClick={() =>
                              setEdit({ ...f, value: pretty(f.value) })
                            }
                          >
                            <Pencil size={15} />
                          </IconButton>
                          <IconButton
                            title={`Delete ${f.key}`}
                            onClick={() => setDel(f)}
                          >
                            <Trash2 size={15} />
                          </IconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              icon={<GraduationCap size={26} />}
              title={
                q
                  ? "No facts match your search"
                  : "Your experience, structured and in your control"
              }
              description="Import a CV to extract candidate facts, or add them yourself. Review each fact before marking it verified."
              action={
                <Button secondary onClick={() => ctx.navigate("documents")}>
                  Import your CV
                  <ArrowRight size={14} />
                </Button>
              }
            />
          )}
        </Resource>
      </Panel>
      <Panel
        title="Evidence relationships"
        description="Connect skills, experience, and projects for answer provenance"
        action={
          <Button secondary onClick={() => setRelationship(true)}>
            <Plus size={15} />
            Link facts
          </Button>
        }
      >
        {items(relationships.data).length ? (
          <div className="simple-list">
            {items(relationships.data).map((v) => (
              <div key={v.id}>
                <Link2 size={15} />
                <div>
                  <strong>
                    {facts.find((f) => f.id === v.from_fact_id)?.key ||
                      v.from_fact_id}{" "}
                    →{" "}
                    {facts.find((f) => f.id === v.to_fact_id)?.key ||
                      v.to_fact_id}
                  </strong>
                  <small>{label(v.kind || v.relationship)}</small>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="panel-copy">
            Link a skill to the experience or project that supports it. These
            connections preserve where your answers come from.
          </p>
        )}
      </Panel>
      {edit && (
        <Editor
          title={edit.id ? "Edit candidate fact" : "Add candidate fact"}
          fields={factFields}
          initial={{
            category: "personal",
            verification_status: "unverified",
            source: "manual",
            ...edit,
          }}
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            await api(
              `/facts${edit.id ? `/${edit.id}` : ""}`,
              edit.id ? "PATCH" : "POST",
              values,
            );
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Profile saved.",
            );
          }}
        />
      )}
      {del && (
        <Confirm
          title={`Delete ${del.key}?`}
          description="This removes the fact from your current knowledge base. Historical application answers retain their evidence records."
          danger
          button="Delete fact"
          onClose={() => setDel(null)}
          onConfirm={async () => {
            await api(`/facts/${del.id}`, "DELETE");
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Fact deleted.",
            );
          }}
        />
      )}
      {relationship && (
        <Editor
          title="Link candidate facts"
          description="Choose the fact and the experience or project that supports it."
          fields={[
            {
              key: "from_fact_id",
              label: "From fact",
              type: "select",
              options: facts.map((f) => ({
                value: f.id,
                label: `${label(f.key)}: ${pretty(f.value).slice(0, 80)}`,
              })),
              required: true,
            },
            {
              key: "to_fact_id",
              label: "Supporting fact",
              type: "select",
              options: facts.map((f) => ({
                value: f.id,
                label: `${label(f.key)}: ${pretty(f.value).slice(0, 80)}`,
              })),
              required: true,
            },
            {
              key: "kind",
              label: "Relationship",
              type: "select",
              options: ["supported_by", "used_in", "part_of"],
              required: true,
            },
          ]}
          initial={{ kind: "supported_by" }}
          onClose={() => setRelationship(false)}
          onSave={async (values) => {
            await api("/relationships", "POST", values);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Evidence linked.",
            );
          }}
        />
      )}
    </>
  );
}
export function Documents({ ctx }: { ctx: AppContext }) {
  const r = useResource("/documents", ctx.refresh),
    [imported, setImported] = useState<Data | null>(null),
    [reimport, setReimport] = useState<Document | null>(null),
    documents = items<Document>(r.data);
  return (
    <>
      <SectionTitle
        eyebrow="YOUR APPLICATION TOOLKIT"
        title="Documents"
        description="A versioned library of your CVs, cover letters, and supporting evidence."
        actions={
          <UploadButton
            ctx={ctx}
            path="/documents/import"
            label="Import document"
            accept=".pdf,.docx,.txt,.md,.csv,.json"
            success="Document imported. Review extracted facts before verification."
            onResult={setImported}
          />
        }
      />
      {imported && (
        <Panel
          id="document-import-review"
          title={
            imported.already_current
              ? "Extraction is up to date"
              : imported.reextracted
                ? "Facts re-extracted"
                : "Import complete"
          }
          description="Extracted information remains unverified until you review it."
          action={
            <MoreLink onClick={() => ctx.navigate("profile")}>
              Review facts
            </MoreLink>
          }
        >
          <div className="import-result">
            <CheckCheck size={21} />
            <div>
              <strong>
                {imported.document?.name ||
                  imported.document?.filename ||
                  "Your document is in the library."}
              </strong>
              <p>
                {(imported.proposed_facts || []).length} facts{" "}
                {imported.reextracted ? "available" : "proposed"} for review.
              </p>
              {imported.reextracted && (
                <p>
                  {imported.already_current
                    ? "This version already uses the current extractor; no new version was created."
                    : `Version ${imported.version?.version ?? "created"} saved. ${imported.superseded_fact_ids?.length ?? imported.superseded ?? 0} older unreviewed proposals superseded. Verified and locked facts were kept.`}
                </p>
              )}
            </div>
          </div>
          {(imported.warnings || []).length > 0 && (
            <div className="import-warnings">
              <strong>Import notes</strong>
              <ul>
                {imported.warnings.map((warning: string, index: number) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          <ImportedFacts
            key={imported.version?.id || imported.document?.id}
            ctx={ctx}
            initial={imported.proposed_facts || []}
          />
        </Panel>
      )}
      <Panel>
        <Resource {...r} retry={r.reload}>
          {documents.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Type</th>
                    <th>Added</th>
                    <th>Versions</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {documents.map((d) => (
                    <tr key={d.id} data-document-id={d.id}>
                      <td>
                        <button
                          className="table-title"
                          onClick={() =>
                            ctx.select({ kind: "document", id: d.id })
                          }
                        >
                          <span className="file-icon">
                            <FileText size={20} />
                          </span>
                          <strong>{d.name || d.filename || "Document"}</strong>
                        </button>
                      </td>
                      <td>
                        <Tag>{label(d.kind || "document")}</Tag>
                      </td>
                      <td>{date(d.created_at)}</td>
                      <td>
                        {d.versions_count ||
                          d.version_count ||
                          d.versions?.length ||
                          1}
                      </td>
                      <td>
                        <div className="actions">
                          <Button
                            secondary
                            onClick={() =>
                              ctx.select({ kind: "document", id: d.id })
                            }
                          >
                            View versions
                            <ArrowRight size={14} />
                          </Button>
                          {d.kind === "import" && (
                            <Button
                              secondary
                              aria-label={`Re-extract facts from ${d.name || d.filename || "document"}`}
                              onClick={() => setReimport(d)}
                            >
                              <RefreshCw size={14} />
                              Re-extract facts
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              icon={<FileText size={27} />}
              title="Start with your CV"
              description="Import PDF, DOCX, or text documents. Meridian proposes candidate facts for your review and keeps immutable document versions."
              action={
                <UploadButton
                  ctx={ctx}
                  path="/documents/import"
                  label="Choose a document"
                  accept=".pdf,.docx,.txt,.md,.csv,.json"
                  onResult={setImported}
                />
              }
            />
          )}
        </Resource>
      </Panel>
      <div className="info-card">
        <ShieldCheck size={21} />
        <div>
          <strong>Your documents stay on this Mac.</strong>
          <p>
            Facts extracted from documents are never automatically treated as
            verified. Tailored documents are generated from the verified facts
            you select in a job’s detail panel.
          </p>
        </div>
      </div>
      {reimport && (
        <Confirm
          title="Re-extract facts from this document?"
          description={`Run the current extractor on ${reimport.name || reimport.filename || "this imported document"}. When extraction needs updating, this creates a new immutable document version and supersedes only older unverified, unlocked proposals from this document. Original versions and verified or locked facts are preserved. New proposals still require your review.`}
          button="Re-extract facts"
          onClose={() => setReimport(null)}
          onConfirm={async () => {
            const result = await api(
              `/documents/${reimport.id}/reimport`,
              "POST",
              {},
            );
            setImported({ ...result, reextracted: true });
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              result.already_current
                ? "This document already uses the current extractor."
                : "Facts re-extracted. Review the new proposals.",
            );
            requestAnimationFrame(() =>
              document
                .getElementById("document-import-review")
                ?.scrollIntoView({ block: "start" }),
            );
          }}
        />
      )}
    </>
  );
}
function ImportedFacts({ ctx, initial }: { ctx: AppContext; initial: Fact[] }) {
  const [facts, setFacts] = useState(initial);
  const [editing, setEditing] = useState<Fact | null>(null);
  const [category, setCategory] = useState("");
  const updateFact = async (fact: Fact, values: Data) => {
    const updated = await api<Fact>(`/facts/${fact.id}`, "PATCH", values);
    setFacts((current) =>
      current.map((entry) => (entry.id === fact.id ? updated : entry)),
    );
    return updated;
  };
  if (!facts.length)
    return (
      <p className="panel-copy">
        No structured facts were extracted. Open the document to inspect it and
        add supported facts from Profile.
      </p>
    );
  return (
    <div className="import-facts">
      <div className="toolbar">
        <strong>Review extracted facts</strong>
        <Tag>
          {
            facts.filter((fact) => fact.verification_status === "unverified")
              .length
          }{" "}
          need review
        </Tag>
        <select
          aria-label="Imported fact category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="">All categories</option>
          {[...new Set(facts.map((fact) => fact.category))]
            .sort()
            .map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
        </select>
      </div>
      <p className="panel-copy">
        Compare each value with your document. Education, experience, and
        projects retain their source context. Edit inaccurate extraction or
        reject unsupported facts before using them.
      </p>
      {facts
        .filter((fact) => !category || fact.category === category)
        .map((fact) => (
          <article className="import-fact" key={fact.id}>
            <div className="row-between">
              <div>
                <Tag>{label(fact.category)}</Tag>
                <h4>{label(fact.key)}</h4>
              </div>
              <Status value={fact.verification_status} />
            </div>
            <div className="import-fact-value">{pretty(fact.value)}</div>
            <small>
              {fact.notes || "Check against the original document."}
            </small>
            <div className="actions">
              <Button
                secondary
                onClick={() =>
                  setEditing({ ...fact, value: pretty(fact.value) })
                }
              >
                <Pencil size={13} />
                Edit fact
              </Button>
              {fact.verification_status !== "verified" && (
                <Button
                  secondary
                  disabled={!!ctx.busy}
                  onClick={() =>
                    ctx.act(
                      `verify:${fact.id}`,
                      () =>
                        updateFact(fact, { verification_status: "verified" }),
                      "Fact verified after your review.",
                    )
                  }
                >
                  <Check size={14} />
                  Verify fact
                </Button>
              )}
              {fact.verification_status !== "rejected" && (
                <Button
                  secondary
                  disabled={!!ctx.busy}
                  onClick={() =>
                    ctx.act(
                      `reject:${fact.id}`,
                      () =>
                        updateFact(fact, {
                          verification_status: "rejected",
                          locked: false,
                        }),
                      "Fact rejected and excluded from automatic use.",
                    )
                  }
                >
                  Reject fact
                </Button>
              )}
            </div>
          </article>
        ))}
      {editing && (
        <Editor
          title="Review imported fact"
          description="Keep only information supported by your document. Verification is your explicit decision."
          fields={factFields}
          initial={editing}
          onClose={() => setEditing(null)}
          onSave={async (values) => {
            await updateFact(editing, values);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Fact review saved.",
            );
          }}
        />
      )}
    </div>
  );
}
export function Email({ ctx }: { ctx: AppContext }) {
  const [page, setPage] = useState(0),
    r = useResource(`/emails?limit=50&offset=${page * 50}`, ctx.refresh),
    integrations = useResource("/integrations", ctx.refresh),
    mailStatus = useResource("/email/status", ctx.refresh),
    [edit, setEdit] = useState<Data | null>(null);
  const providers = ["gmail", "outlook", "imap"];
  const reloadIntegrations = integrations.reload;
  const reloadMailStatus = mailStatus.reload;
  const reloadMessages = r.reload;
  const syncRevision = items(integrations.data)
    .map((item) => item.last_sync || "")
    .join("|");
  useEffect(() => {
    // OAuth completes in the system browser; refresh its outcome on return.
    window.addEventListener("focus", reloadIntegrations);
    return () => window.removeEventListener("focus", reloadIntegrations);
  }, [reloadIntegrations]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      reloadIntegrations();
      reloadMailStatus();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [reloadIntegrations, reloadMailStatus]);
  useEffect(() => reloadMessages(), [syncRevision, reloadMessages]);
  return (
    <>
      <SectionTitle
        eyebrow="CONVERSATIONS, CONNECTED"
        title="Email"
        description="Read-only email monitoring that keeps your application timeline up to date."
        actions={
          <>
            <UploadButton
              ctx={ctx}
              path="/emails/import"
              label="Import .eml"
              accept=".eml"
              success="Email imported and classified."
            />
            <Button
              secondary
              loading={
                ctx.busy === "email-sync" || Boolean(mailStatus.data?.syncing)
              }
              onClick={async () => {
                const result = await ctx.act("email-sync", () =>
                  api("/email/sync", "POST", {}),
                );
                reloadIntegrations();
                reloadMailStatus();
                reloadMessages();
                if (result) {
                  const failed = Boolean(result.errors?.length);
                  ctx.toast(
                    failed
                      ? "Email sync needs attention. Check the account message below."
                      : result.status === "already_running"
                        ? "Email sync is already running. Progress is shown below."
                        : `Email sync completed. ${result.imported || 0} new messages imported.`,
                    failed,
                  );
                }
              }}
            >
              <RefreshCw size={15} />
              Sync inbox
            </Button>
          </>
        }
      />
      {mailStatus.data?.phase !== "idle" && mailStatus.data?.phase && (
        <Panel>
          <div role="status" aria-live="polite">
            <strong>
              {mailStatus.data.phase === "waiting_for_keychain"
                ? "Waiting for macOS Keychain"
                : "Reading your email"}
            </strong>
            <p>
              {mailStatus.data.phase === "waiting_for_keychain"
                ? "If macOS asks to let meridian-backend access its saved credential, choose Allow. This permission is separate from signing in to your email account."
                : `${mailStatus.data.messages_read || 0} messages read. The first sync can take a few minutes.`}
            </p>
          </div>
        </Panel>
      )}
      <div className="integration-grid">
        {providers.map((p) => {
          const i = items(integrations.data).find((v) => v.provider === p);
          return (
            <Panel key={p}>
              <div className="integration-card">
                <div className="row-between">
                  <span className="list-icon">
                    <Mail size={20} />
                  </span>
                  <Status
                    value={
                      i?.last_error ? "error" : i?.status || "not connected"
                    }
                  />
                </div>
                <h3>
                  {p === "gmail"
                    ? "Gmail"
                    : p === "outlook"
                      ? "Microsoft Outlook"
                      : "IMAP over TLS"}
                </h3>
                <p>{i?.last_error || `Last sync: ${time(i?.last_sync)}`}</p>
                {i?.status === "connected" &&
                  !i?.last_sync &&
                  !i?.last_error && (
                    <p>Account connected. First sync pending.</p>
                  )}
                {i?.config?.account_email && (
                  <p>Verified account: {i.config.account_email}</p>
                )}
                <Button
                  secondary
                  onClick={() => setEdit({ provider: p, ...i })}
                >
                  {i ? "Manage connection" : "Configure"}
                  <ArrowRight size={14} />
                </Button>
              </div>
            </Panel>
          );
        })}
      </div>
      <Panel
        title="Application messages"
        description="Status updates with their original evidence"
      >
        <Resource {...r} retry={r.reload}>
          {items(r.data).length ? (
            <>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Message</th>
                      <th>Classification</th>
                      <th>Received</th>
                      <th>Linked application</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items(r.data).map((e) => (
                      <tr
                        key={e.id}
                        className="clickable"
                        onClick={() => ctx.select({ kind: "email", id: e.id })}
                      >
                        <td>
                          <button
                            className="table-title"
                            onClick={() =>
                              ctx.select({ kind: "email", id: e.id })
                            }
                          >
                            <span className="file-icon">
                              <Mail size={18} />
                            </span>
                            <span>
                              <strong>{e.subject || "(No subject)"}</strong>
                              <small>{e.sender || e.from_address}</small>
                            </span>
                          </button>
                        </td>
                        <td>
                          <Status
                            value={
                              typeof e.classification === "string"
                                ? e.classification
                                : e.classification?.category ||
                                  e.classification?.status ||
                                  e.category ||
                                  "unclassified"
                            }
                          />
                        </td>
                        <td>{time(e.received_at || e.date)}</td>
                        <td>
                          {e.application_id ? (
                            <Tag tone="green">
                              <Link2 size={12} />
                              Linked
                            </Tag>
                          ) : (
                            <Tag tone="amber">Needs linking</Tag>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={page}
                total={r.data?.total ?? items(r.data).length}
                onChange={setPage}
              />
            </>
          ) : (
            <Empty
              icon={<Inbox size={26} />}
              title="Keep the conversation in view"
              description="Connect your application inbox or import an email file. Confirmations, assessments, interviews, and offers will be linked to your applications."
            />
          )}
        </Resource>
      </Panel>
      {edit && (
        <IntegrationEditor
          ctx={ctx}
          integration={edit}
          onClose={() => setEdit(null)}
        />
      )}
    </>
  );
}
export function IntegrationEditor({
  ctx,
  integration,
  onClose,
}: {
  ctx: AppContext;
  integration: Data;
  onClose: () => void;
}) {
  const p = integration.provider,
    imap = p === "imap";
  return (
    <Editor
      title={`Configure ${p === "imap" ? "IMAP" : label(p)}`}
      description={
        imap
          ? "Use an application password where your provider requires one. It is stored in macOS Keychain. Save & connect authenticates and reads recent messages; it never sends email."
          : p === "gmail"
            ? "Use a Desktop app OAuth client from Google Cloud. Copy the client ID and client secret from that same client. These identify Meridian; they are not your Gmail password."
            : "Register your OAuth application and set its loopback redirect URI. Connecting opens your provider’s authorization page."
      }
      fields={
        imap
          ? [
              { key: "host", label: "IMAP host", required: true },
              {
                key: "port",
                label: "TLS port",
                type: "number",
                required: true,
              },
              { key: "username", label: "Email / username", required: true },
              { key: "folder", label: "Mailbox folder", required: true },
              {
                key: "secret",
                label: "Application password",
                type: "password",
                hint: "Leave blank to keep the saved credential.",
              },
            ]
          : [
              ...(p === "gmail"
                ? [
                    {
                      key: "expected_email",
                      label: "Expected Gmail account",
                      type: "text" as const,
                      hint: "Optional. Authorization must return this Google account; no password is entered here.",
                    },
                  ]
                : []),
              { key: "client_id", label: "OAuth client ID", required: true },
              {
                key: "secret",
                label:
                  p === "gmail"
                    ? "Google OAuth client secret"
                    : "Client secret (if required by your app)",
                type: "password",
                hint:
                  p === "gmail"
                    ? "Use client_secret from the same Google Desktop app client. Stored in macOS Keychain. Leave blank only if already saved."
                    : "Stored only in macOS Keychain. Leave blank to keep the existing secret.",
              },
              ...(p === "outlook"
                ? [
                    {
                      key: "tenant",
                      label: "Microsoft tenant",
                      hint: "Use common for personal or organizational accounts.",
                    },
                  ]
                : []),
            ]
      }
      initial={{
        port: 993,
        folder: "INBOX",
        tenant: "common",
        ...integration.config,
        secret: "",
      }}
      submit={imap ? "Save & connect" : "Save & authorize"}
      onClose={onClose}
      onSave={async (values) => {
        const { secret, ...config } = values;
        try {
          await api(`/integrations/${p}`, "PUT", {
            config,
            ...(secret ? { secret } : {}),
          });
          const connection = await api(`/integrations/${p}/connect`, "POST");
          if (connection.url) await openExternal(connection.url);
          ctx.toast(
            imap
              ? "IMAP connected and initial read-only sync completed."
              : "Authorization opened in your browser.",
          );
        } finally {
          // Saving a changed account invalidates its previous connection even
          // when the subsequent authorization fails.
          await ctx.act("refresh", () => Promise.resolve({}));
        }
      }}
    />
  );
}
export function Analytics({ ctx }: { ctx: AppContext }) {
  const r = useResource("/analytics", ctx.refresh),
    d = r.data || {};
  const statusCounts: Data[] = d.status_counts || [];
  const funnel: Data[] = d.funnel || [];
  const responseTime = d.time_to_response || {};
  const maxStage = Math.max(1, ...funnel.map((stage) => Number(stage.count)));
  const weeks: Data[] = (d.applications_by_week || []).slice(-12);
  const maxWeek = Math.max(1, ...weeks.map((week) => Number(week.count)));
  const offers =
    funnel.find((stage) => String(stage.stage).toLowerCase() === "offer")
      ?.count ??
    statusCounts.find((row) => row.status === "OFFER")?.count ??
    0;
  return (
    <>
      <SectionTitle
        eyebrow="LEARN FROM YOUR SEARCH"
        title="Analytics"
        description="Understand your recorded outcomes, response times, and the opportunities behind them."
      />
      <Resource {...r} retry={r.reload}>
        <div className="stat-grid">
          {[
            {
              name: "Applications",
              value: statusCounts.reduce(
                (sum, row) => sum + Number(row.count),
                0,
              ),
              icon: BriefcaseBusiness,
              note: `${d.submitted ?? 0} explicitly recorded submissions`,
            },
            {
              name: "Response rate",
              value: d.response_rate,
              percent: true,
              icon: Mail,
              note: `${d.responded ?? 0} responses from ${d.submitted ?? 0} submissions`,
            },
            {
              name: "Interview rate",
              value: d.interview_rate,
              percent: true,
              icon: TrendingUp,
              note: "Based on recorded interview events",
            },
            {
              name: "Offers",
              value: offers,
              icon: Target,
              note: "Observed offer events",
            },
          ].map((metric) => (
            <div className="stat-card" key={metric.name}>
              <div className="stat-label">
                {metric.name}
                <metric.icon size={17} />
              </div>
              <strong>
                {metric.value == null
                  ? "—"
                  : `${Number(metric.value).toLocaleString(undefined, { maximumFractionDigits: 1 })}${metric.percent ? "%" : ""}`}
              </strong>
              <span>{metric.note}</span>
            </div>
          ))}
        </div>
        <div className="two-col">
          <Panel
            title="Time to first response"
            description="Measured from explicit submission to the first recorded recruitment response."
          >
            {responseTime.sample_size > 0 ? (
              <>
                <div className="timing-metrics">
                  <div>
                    <span>Median</span>
                    <strong>{formatDays(responseTime.median_days)}</strong>
                  </div>
                  <div>
                    <span>Average</span>
                    <strong>{formatDays(responseTime.mean_days)}</strong>
                  </div>
                  <div>
                    <span>Observed range</span>
                    <strong className="small">
                      {formatDays(responseTime.min_days)} –{" "}
                      {formatDays(responseTime.max_days)}
                    </strong>
                  </div>
                </div>
                <div className="analytics-footnote">
                  <span>
                    {responseTime.sample_size} measured responses ·{" "}
                    {responseTime.pending_applications ?? 0} still awaiting a
                    response
                  </span>
                  {responseTime.small_sample && (
                    <Tag tone="amber">Small sample</Tag>
                  )}
                </div>
              </>
            ) : (
              <Empty
                icon={<Clock3 size={24} />}
                title="Response timing needs two recorded events"
                description={`There are no valid submission-to-response intervals yet. ${responseTime.pending_applications ?? 0} submitted applications are still awaiting a recorded response.`}
              />
            )}
          </Panel>
          <Panel
            title="Weekly application activity"
            description="Application workspaces created during the latest 12 recorded weeks."
          >
            {weeks.length ? (
              <div
                className="weekly-chart"
                role="img"
                aria-label={weeks
                  .map(
                    (week) => `${week.week_start}: ${week.count} applications`,
                  )
                  .join("; ")}
              >
                {weeks.map((week) => (
                  <div className="weekly-column" key={week.week_start}>
                    <strong>{week.count}</strong>
                    <div className="weekly-track">
                      <div
                        style={{
                          height: `${(Number(week.count) / maxWeek) * 100}%`,
                        }}
                        title={`${week.week_start}: ${week.count} applications`}
                      />
                    </div>
                    <span>
                      {new Date(
                        `${week.week_start}T12:00:00`,
                      ).toLocaleDateString(undefined, {
                        day: "numeric",
                        month: "short",
                      })}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                icon={<TrendingUp size={24} />}
                title="Your search over time"
                description="Weekly counts appear as you prepare and track applications."
              />
            )}
          </Panel>
        </div>
        <Panel
          title="Observed application funnel"
          description="Historical milestones; later events do not imply that an earlier stage was recorded."
        >
          {funnel.length ? (
            <div className="bar-chart">
              {funnel.map((stage) => (
                <div className="chart-row" key={stage.stage}>
                  <div>
                    <span>{stage.stage}</span>
                    <strong>
                      {stage.count}
                      <small className="muted">
                        {stage.rate == null
                          ? ""
                          : ` · ${formatRate(stage.rate)} of ${stage.denominator}`}
                      </small>
                    </strong>
                  </div>
                  <div className="bar-track">
                    <div
                      style={{
                        width: `${(Number(stage.count) / maxStage) * 100}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="No recorded milestones"
              description="Discovery and application events will populate the funnel."
            />
          )}
        </Panel>
        <CohortComparisons data={d} />
        <Panel
          title="How to read these measurements"
          description="Observed associations are evidence about your recorded pipeline, not explanations for a hiring decision."
        >
          <p className="panel-copy">
            {d.note ||
              "Rates use explicitly recorded submissions as their denominator. Outcomes without a recorded submission are retained as evidence but do not inflate submission counts."}
          </p>
          {d.definitions && (
            <JsonDetails title="Metric definitions" data={d.definitions} />
          )}
          {d.data_quality && <KeyValues data={d.data_quality} />}
        </Panel>
      </Resource>
    </>
  );
}
function formatRate(value: unknown): string {
  return value == null
    ? "—"
    : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}
function formatDays(value: unknown): string {
  return value == null
    ? "—"
    : `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} d`;
}
function CohortComparisons({ data }: { data: Data }) {
  const [dimension, setDimension] = useState("source"),
    [outcome, setOutcome] = useState("response"),
    [page, setPage] = useState(0);
  const dimensions = [
    { value: "source", label: "Source", key: "applications_by_source" },
    { value: "company", label: "Company", key: "applications_by_company" },
    { value: "role", label: "Role", key: "applications_by_role" },
    { value: "cv", label: "CV version", key: "cv_version_performance" },
    { value: "match", label: "Match score", key: "match_score_performance" },
  ];
  const outcomes = [
    { value: "response", label: "Responses", count: "responded" },
    { value: "assessment", label: "Assessments", count: "assessments" },
    { value: "interview", label: "Interviews", count: "interviews" },
    { value: "offer", label: "Offers", count: "offers" },
    { value: "rejection", label: "Rejections", count: "rejected" },
  ];
  const grouping = dimensions.find((value) => value.value === dimension)!;
  const metric = outcomes.find((value) => value.value === outcome)!;
  const rows: Data[] =
    data[grouping.key] ||
    (dimension === "source" ? data.source_performance : []) ||
    [];
  const rowName = (row: Data): string =>
    dimension === "cv"
      ? row.document_version_id
        ? `${row.document_name || row.filename || "CV"} · version ${row.version ?? "unknown"}`
        : "No recorded CV"
      : dimension === "match"
        ? row.bucket || "Unknown"
        : row[dimension] || "Unknown";
  return (
    <Panel
      title="Compare your application groups"
      description="Explore outcomes by source, company, role, CV version, or recorded match score."
    >
      <div className="cohort-controls">
        <label className="field">
          <span>Group by</span>
          <select
            value={dimension}
            onChange={(event) => {
              setDimension(event.target.value);
              setPage(0);
            }}
          >
            {dimensions.map((value) => (
              <option key={value.value} value={value.value}>
                {value.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Outcome</span>
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
          >
            {outcomes.map((value) => (
              <option key={value.value} value={value.value}>
                {value.label}
              </option>
            ))}
          </select>
        </label>
        <p>
          Every rate shows its recorded submission sample. Small groups are
          flagged so their percentages are not mistaken for reliable
          predictions.
        </p>
      </div>
      {rows.length ? (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{grouping.label}</th>
                  <th>Applications</th>
                  <th>Submitted</th>
                  <th>{metric.label}</th>
                  <th>{label(outcome)} rate</th>
                  <th>Average match</th>
                  <th>Sample</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(page * 50, (page + 1) * 50).map((row, index) => (
                  <tr key={`${rowName(row)}-${index}`}>
                    <td>
                      <strong>{rowName(row)}</strong>
                    </td>
                    <td>{row.applications ?? 0}</td>
                    <td>{row.submitted ?? 0}</td>
                    <td>{row[metric.count] ?? 0}</td>
                    <td>
                      <strong>{formatRate(row[`${outcome}_rate`])}</strong>
                      {row[`${outcome}_rate`] != null && (
                        <div className="bar-track cohort-bar">
                          <div
                            style={{
                              width: `${Math.max(0, Math.min(100, Number(row[`${outcome}_rate`])))}%`,
                            }}
                          />
                        </div>
                      )}
                    </td>
                    <td>
                      {row.average_match == null
                        ? "—"
                        : Number(row.average_match).toFixed(1)}
                    </td>
                    <td>
                      <Tag tone={row.small_sample ? "amber" : "neutral"}>
                        {row.sample_size ?? row.submitted ?? 0} submitted
                        {row.small_sample ? " · small sample" : ""}
                      </Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} total={rows.length} onChange={setPage} />
        </>
      ) : (
        <Empty
          icon={<Target size={24} />}
          title="No groups to compare yet"
          description="These comparisons appear as applications and their supporting evidence are recorded."
        />
      )}
    </Panel>
  );
}
export function Activity({ ctx }: { ctx: AppContext }) {
  const [page, setPage] = useState(0),
    r = useResource(`/activity?limit=50&offset=${page * 50}`, ctx.refresh);
  return (
    <>
      <SectionTitle
        eyebrow="A COMPLETE RECORD"
        title="Activity"
        description="The history of your search, from first discovery to final outcome."
      />
      <Panel>
        <Resource {...r} retry={r.reload}>
          {items(r.data).length ? (
            <>
              <Timeline events={items(r.data)} />
              <Pagination
                page={page}
                total={r.data?.total ?? items(r.data).length}
                onChange={setPage}
              />
            </>
          ) : (
            <Empty
              icon={<Clock3 size={26} />}
              title="Every step leaves a trace"
              description="Your application events and status changes appear here, with timestamps and their source."
            />
          )}
        </Resource>
      </Panel>
    </>
  );
}
export function DocumentVersions({ id, ctx }: { id: string; ctx: AppContext }) {
  const r = useResource(`/documents/${id}/versions`, ctx.refresh);
  return (
    <Resource {...r} retry={r.reload}>
      {items(r.data).length ? (
        <div className="simple-list">
          {items(r.data).map((v: Data, i: number) => (
            <div key={v.id}>
              <FileText size={19} />
              <div className="grow">
                <strong>
                  {v.filename || v.name || `Version ${v.version || i + 1}`}
                </strong>
                <small>
                  {date(v.created_at)} ·{" "}
                  {v.mime_type || v.content_type || "Document"}
                </small>
                {v.text && (
                  <JsonDetails
                    title="Read extracted document text"
                    data={v.text}
                  />
                )}
                {v.sha256 && (
                  <small className="code ellipsis" title={v.sha256}>
                    SHA-256 {v.sha256}
                  </small>
                )}
              </div>
              {!v.approved && (
                <Button
                  secondary
                  onClick={() =>
                    ctx.act(
                      `approve-document:${v.id}`,
                      () =>
                        api(`/document-versions/${v.id}/approve`, "POST", {}),
                      "Document approved for application use.",
                    )
                  }
                >
                  <Check size={15} />
                  Approve for use
                </Button>
              )}
              {v.approved && <Tag tone="green">Approved</Tag>}
              <Button
                secondary
                onClick={() =>
                  ctx.act("download", () =>
                    download(
                      `/document-versions/${v.id}/download`,
                      v.filename || v.name || "document.pdf",
                    ),
                  )
                }
              >
                <ArrowDownToLine size={15} />
                Download
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="No document versions"
          description="This document has no available version."
        />
      )}
    </Resource>
  );
}
