import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Bookmark,
  Check,
  ExternalLink,
  Mail,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import type { AppContext, Data } from "./types";
import { api, date, label, openExternal, useResource } from "./api";
import {
  Button,
  Empty,
  Modal,
  Resource,
  Score,
  SearchInput,
  SectionTitle,
  Status,
  Tag,
} from "./components";
import "./tracking.css";

const stages = [
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
];
const todayInput = () =>
  new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);

export function RecordApplied({
  job,
  ctx,
  close,
}: {
  job: Data;
  ctx: AppContext;
  close: () => void;
}) {
  const [when, setWhen] = useState(todayInput()),
    [notes, setNotes] = useState("");
  return (
    <Modal title="Record your application" onClose={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const applied = await ctx.act(
            "record-application",
            () =>
              api(`/jobs/${job.id}/record-application`, "POST", {
                applied_at: new Date(`${when}T00:00:00`).toISOString(),
                notes,
              }),
            "Application recorded. Related emails will update its progress.",
          );
          if (applied) close();
        }}
      >
        <p className="panel-copy">
          {job.title} · {job.company}
        </p>
        <p className="panel-copy">
          Record an application you have already submitted on the employer’s
          website.
        </p>
        <label className="field">
          Application date
          <input
            type="date"
            required
            max={todayInput()}
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
        </label>
        <label className="field">
          Notes
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Referral, contact, application reference…"
          />
        </label>
        <div className="modal-actions">
          <Button type="button" secondary onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={ctx.busy === "record-application"}>
            <Check size={15} />
            Save as applied
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function OpportunityActions({
  job,
  ctx,
  compact = false,
}: {
  job: Data;
  ctx: AppContext;
  compact?: boolean;
}) {
  const [record, setRecord] = useState(false);
  return (
    <>
      <div className="opportunity-actions">
        <Button
          secondary={compact}
          onClick={() =>
            ctx.act("open-job", () =>
              openExternal(job.application_url || job.url),
            )
          }
        >
          <ArrowUpRight size={15} />
          {compact ? "Apply link" : "Open application"}
        </Button>
        {!job.submitted && (
          <Button secondary onClick={() => setRecord(true)}>
            <Check size={15} />
            Mark applied
          </Button>
        )}
        {!job.submitted && (
          <Button
            secondary
            aria-label={
              job.status === "SHORTLISTED"
                ? "Unsave opportunity"
                : "Save opportunity"
            }
            title={
              job.status === "SHORTLISTED"
                ? "Saved for later"
                : "Save for later"
            }
            onClick={() =>
              ctx.act(
                "save-job",
                () =>
                  api(`/jobs/${job.id}`, "PATCH", {
                    status:
                      job.status === "SHORTLISTED"
                        ? "DISCOVERED"
                        : "SHORTLISTED",
                  }),
                job.status === "SHORTLISTED"
                  ? "Removed from saved."
                  : "Saved for later.",
              )
            }
          >
            <Bookmark
              size={15}
              fill={job.status === "SHORTLISTED" ? "currentColor" : "none"}
            />
          </Button>
        )}
      </div>
      {record && (
        <RecordApplied job={job} ctx={ctx} close={() => setRecord(false)} />
      )}
    </>
  );
}

export function Daily({ ctx }: { ctx: AppContext }) {
  const r = useResource("/daily", ctx.refresh),
    [query, setQuery] = useState("");
  const reload = r.reload;
  useEffect(() => {
    const timer = setInterval(reload, 10000);
    return () => clearInterval(timer);
  }, [reload]);
  const d = r.data,
    jobs = (d?.items || []).filter((j: Data) =>
      `${j.title} ${j.company} ${j.location}`
        .toLowerCase()
        .includes(query.toLowerCase()),
    );
  const email = d?.email?.find((v: Data) => v.status === "connected");
  return (
    <>
      <SectionTitle
        eyebrow="YOUR DAILY JOB SEARCH"
        title="A new day. Your next opportunity."
        description="Find roles, apply through the original link, and keep every application in one place."
        actions={
          <Button
            loading={!!d?.refreshing || ctx.busy === "daily-refresh"}
            onClick={() =>
              ctx.act(
                "daily-refresh",
                () => api("/daily/refresh", "POST", {}),
                "Checking your sources. New opportunities will appear here.",
              )
            }
          >
            <RefreshCw size={15} />
            Refresh jobs
          </Button>
        }
      />
      <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
        {d && (
          <>
            <div className="daily-summary">
              <div>
                <span>{d.new_today}</span>
                <small>new matches today</small>
              </div>
              <div>
                <span>{d.total}</span>
                <small>possible next applications</small>
              </div>
              <button onClick={() => ctx.navigate("applications")}>
                <span>{d.applied}</span>
                <small>
                  applications tracked <ArrowUpRight size={12} />
                </small>
              </button>
              <button onClick={() => ctx.navigate("applications", "saved")}>
                <span>{d.saved}</span>
                <small>
                  saved for later <Bookmark size={12} />
                </small>
              </button>
            </div>
            <div className="daily-meta">
              <span>
                {d.date} ·{" "}
                {d.refreshing
                  ? "Checking sources…"
                  : d.last_refresh
                    ? `Last checked ${date(d.last_refresh.at)}`
                    : "Ready for your first daily refresh"}
              </span>
              <span>
                {d.scheduler_enabled
                  ? "Refreshes daily while Meridian is open; catches up after sleep."
                  : "Daily refresh is off in Settings."}
              </span>
            </div>
            <div className="tracking-callout">
              <Mail size={17} />
              <p>
                {email?.last_sync
                  ? `Email connected · last sync ${date(email.last_sync)}. Matching recruitment emails update your table.`
                  : email
                    ? "Gmail is connected. The first inbox sync is still pending; open Email to finish the macOS Keychain prompt."
                    : "Connect your application inbox to detect confirmations, assessments, interviews and decisions."}
              </p>
              <Button secondary onClick={() => ctx.navigate("email")}>
                Email status
              </Button>
            </div>
            <div className="daily-columns">
              <section>
                <div className="daily-list-heading">
                  <h2>
                    Roles to consider <small>Top {Math.min(50, d.total)}</small>
                  </h2>
                  <SearchInput
                    value={query}
                    onChange={setQuery}
                    placeholder="Filter today’s list…"
                  />
                </div>
                <div className="daily-list">
                  {jobs.map((j: Data) => (
                    <article key={j.id} className="opportunity-card">
                      <div className="opportunity-top">
                        <span className="company-avatar">
                          {j.company.slice(0, 1)}
                        </span>
                        <div className="grow">
                          <button
                            className="text-link opportunity-title"
                            onClick={() =>
                              ctx.select({ kind: "job", id: j.id })
                            }
                          >
                            {j.title}
                          </button>
                          <p>
                            {j.company} ·{" "}
                            {j.location || "Location not specified"}
                          </p>
                        </div>
                        <Score value={j.match_score} />
                      </div>
                      <div className="tags">
                        <Tag>{j.source || "Imported"}</Tag>
                        {j.found_today && <Tag tone="green">Found today</Tag>}
                        {j.metadata?.application_deadline && (
                          <Tag tone="amber">
                            Closes {date(j.metadata.application_deadline)}
                          </Tag>
                        )}
                        {j.remote && <Tag>Remote</Tag>}
                      </div>
                      {j.metadata?.company_description && (
                        <p className="opportunity-summary">
                          {j.metadata.company_description}
                        </p>
                      )}
                      <div className="opportunity-bottom">
                        <OpportunityActions job={j} ctx={ctx} />
                        <button
                          className="text-link muted"
                          onClick={() =>
                            ctx.act(
                              "ignore-job",
                              () =>
                                api(`/jobs/${j.id}`, "PATCH", {
                                  status: "IGNORED",
                                }),
                              "Moved to ignored. You can restore it in the tracker.",
                            )
                          }
                        >
                          <X size={14} />
                          Pass
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
                {!jobs.length && (
                  <Empty
                    title={
                      query
                        ? "No roles match this filter"
                        : "No matching roles yet"
                    }
                    description="Refresh your sources or adjust your role and location preferences. Your full opportunity table remains available."
                    action={
                      <Button secondary onClick={() => ctx.navigate("jobs")}>
                        View all opportunities
                      </Button>
                    }
                  />
                )}
              </section>
              <aside className="daily-sidebar">
                <h3>Your sources</h3>
                <p>
                  Every offer keeps its original source and application link.
                </p>
                {(d.sources || []).map((s: Data) => (
                  <div className="daily-source" key={s.id}>
                    <strong>{s.name}</strong>
                    <small>
                      {s.config?.delivery === "email"
                        ? s.last_checked
                          ? `Alerts received · ${s.jobs_found} offers`
                          : "Waiting for inbox alerts"
                        : s.last_error
                          ? "Needs attention"
                          : s.last_checked
                            ? `${s.jobs_found} offers · ${date(s.last_checked)}`
                            : "Ready to check"}
                    </small>
                    {s.last_error && (
                      <small className="source-error">{s.last_error}</small>
                    )}
                  </div>
                ))}
                <Button secondary onClick={() => ctx.navigate("discover")}>
                  Manage sources
                </Button>
                <hr />
                <h3>LinkedIn & Indeed</h3>
                <p>
                  Job links from supported email alerts join this list when your
                  inbox syncs. You can also add a posting directly.
                </p>
                <div className="stack">
                  <Button
                    secondary
                    onClick={() =>
                      ctx.act("linkedin-search", () =>
                        openExternal(
                          "https://www.linkedin.com/jobs/search/?keywords=graduate%20software%20engineer&f_TPR=r86400",
                        ),
                      )
                    }
                  >
                    LinkedIn jobs <ExternalLink size={14} />
                  </Button>
                  <Button
                    secondary
                    onClick={() =>
                      ctx.act("indeed-search", () =>
                        openExternal(
                          "https://www.indeed.com/jobs?q=graduate+software+engineer&fromage=1",
                        ),
                      )
                    }
                  >
                    Indeed jobs <ExternalLink size={14} />
                  </Button>
                </div>
                <p className="muted">
                  Alert rows keep the details supplied by the email. Verify
                  requirements in the original posting.
                </p>
              </aside>
            </div>
          </>
        )}
      </Resource>
    </>
  );
}

export function Tracker({
  ctx,
  opportunities = false,
}: {
  ctx: AppContext;
  opportunities?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [scope, setScope] = useState(
      ctx.target === "saved" ? "saved" : opportunities ? "pending" : "all",
    ),
    [status, setStatus] = useState(""),
    [source, setSource] = useState(""),
    [offset, setOffset] = useState(0),
    [add, setAdd] = useState(false);
  const r = useResource(
      `/tracker?q=${encodeURIComponent(query)}&scope=${scope}&status=${status}&source=${encodeURIComponent(source)}&offset=${offset}&limit=50`,
      ctx.refresh,
    ),
    reload = r.reload;
  useEffect(() => {
    const timer = setInterval(reload, 15000);
    return () => clearInterval(timer);
  }, [reload]);
  return (
    <>
      <SectionTitle
        eyebrow="EVERY COMPANY. EVERY NEXT STEP."
        title={opportunities ? "Opportunities" : "Application tracker"}
        description="Your shortlist and applications, with dates, recruitment stages and email evidence."
        actions={<Button onClick={() => setAdd(true)}>Add opportunity</Button>}
      />
      <div className="tracker-filters">
        <SearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setOffset(0);
          }}
          placeholder="Company, role or location…"
        />
        <select
          aria-label="Show applications"
          value={scope}
          onChange={(e) => {
            setScope(e.target.value);
            setOffset(0);
          }}
        >
          <option value="all">All opportunities & applications</option>
          <option value="pending">To apply</option>
          <option value="saved">Saved for later</option>
          <option value="applied">Applied</option>
          <option value="ignored">Ignored</option>
        </select>
        <select
          aria-label="Filter recruitment stage"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All stages</option>
          {stages.map((v) => (
            <option key={v} value={v}>
              {label(v)}
            </option>
          ))}
        </select>
        <select
          aria-label="Filter source"
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">All sources</option>
          {(r.data?.sources || []).map((v: string) => (
            <option key={v}>{v}</option>
          ))}
        </select>
      </div>
      <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
        {r.data?.items?.length ? (
          <div className="tracking-table-wrap">
            <table className="tracking-table">
              <thead>
                <tr>
                  <th>Company & role</th>
                  <th>Location / source</th>
                  <th>Stage</th>
                  <th>Applied</th>
                  <th>Next deadline</th>
                  <th>Latest email</th>
                  <th>Notes / details</th>
                  <th>Application link</th>
                </tr>
              </thead>
              <tbody>
                {r.data.items.map((j: Data) => (
                  <tr key={j.id}>
                    <td>
                      <strong>{j.company}</strong>
                      <button
                        className="text-link"
                        onClick={() => ctx.select({ kind: "job", id: j.id })}
                      >
                        {j.title}
                      </button>
                      <small>
                        {Math.round(j.match_score || 0)}% profile match{" "}
                        {j.metadata?.closed ? "· Source deadline passed" : ""}
                      </small>
                    </td>
                    <td>
                      {j.location || "Not specified"}
                      <small>{j.source}</small>
                    </td>
                    <td>
                      {j.application_id ? (
                        <select
                          aria-label={`Stage for ${j.company} ${j.title}`}
                          value={j.application_status}
                          onChange={(e) =>
                            ctx.act(
                              "update-stage",
                              () =>
                                api(
                                  `/applications/${j.application_id}`,
                                  "PATCH",
                                  { status: e.target.value },
                                ),
                              "Recruitment stage updated.",
                            )
                          }
                        >
                          {!stages.includes(j.application_status) && (
                            <option value={j.application_status}>
                              {label(j.application_status)}
                            </option>
                          )}
                          {stages.map((v) => (
                            <option value={v} key={v}>
                              {label(v)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <Status
                          value={
                            j.status === "SHORTLISTED"
                              ? "Saved"
                              : j.status === "IGNORED"
                                ? "Ignored"
                                : "To apply"
                          }
                        />
                      )}
                    </td>
                    <td>{j.applied_at ? date(j.applied_at) : "—"}</td>
                    <td>
                      {j.deadline ? (
                        <>
                          <span>{date(j.deadline)}</span>
                          <small>{j.deadline_kind}</small>
                        </>
                      ) : (
                        "Not supplied"
                      )}
                    </td>
                    <td>
                      {j.last_email ? (
                        <>
                          <button
                            className="text-link"
                            onClick={() =>
                              ctx.select({ kind: "email", id: j.last_email.id })
                            }
                          >
                            {j.last_email.subject}
                          </button>
                          <small>
                            {date(j.last_email.received_at)} · {j.email_count}{" "}
                            emails
                          </small>
                        </>
                      ) : (
                        <small>
                          {j.submitted ? "Awaiting linked email" : "—"}
                        </small>
                      )}
                    </td>
                    <td>
                      <p className="tracker-notes">
                        {j.application_notes ||
                          j.metadata?.listing_notes ||
                          "—"}
                      </p>
                      <button
                        className="text-link"
                        onClick={() =>
                          ctx.select({
                            kind: j.application_id ? "application" : "job",
                            id: j.application_id || j.id,
                          })
                        }
                      >
                        {j.application_id
                          ? "Timeline & notes"
                          : "Company & role details"}
                      </button>
                    </td>
                    <td>
                      <OpportunityActions job={j} ctx={ctx} compact />
                      {j.status === "IGNORED" && (
                        <Button
                          secondary
                          onClick={() =>
                            ctx.act(
                              "restore-job",
                              () =>
                                api(`/jobs/${j.id}`, "PATCH", {
                                  status: "DISCOVERED",
                                }),
                              "Opportunity restored.",
                            )
                          }
                        >
                          Restore
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="Your table is ready"
            description="Save a role from your daily list, add a posting, or record an application you have already submitted."
            icon={<Search size={25} />}
          />
        )}
        <div className="tracker-pagination">
          <span>{r.data?.total || 0} records</span>
          <Button
            secondary
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            Previous
          </Button>
          <Button
            secondary
            disabled={offset + 50 >= (r.data?.total || 0)}
            onClick={() => setOffset(offset + 50)}
          >
            Next
          </Button>
        </div>
      </Resource>
      {add && <AddOpportunity ctx={ctx} close={() => setAdd(false)} />}
    </>
  );
}

function AddOpportunity({
  ctx,
  close,
}: {
  ctx: AppContext;
  close: () => void;
}) {
  const [url, setUrl] = useState(""),
    [company, setCompany] = useState(""),
    [title, setTitle] = useState(""),
    [location, setLocation] = useState("");
  return (
    <Modal title="Add an opportunity" onClose={close}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const result = await ctx.act(
            "add-opportunity",
            () =>
              api("/jobs", "POST", {
                url,
                title,
                company,
                location,
                source: "Added by you",
              }),
            "Opportunity saved.",
          );
          if (result) close();
        }}
      >
        <p className="panel-copy">
          Paste the application link and the details shown in the posting. You
          can mark it as applied from the table.
        </p>
        <label className="field">
          Application link
          <input
            type="url"
            required
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <label className="field">
          Company
          <input
            required
            value={company}
            onChange={(e) => setCompany(e.target.value)}
          />
        </label>
        <label className="field">
          Role
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="field">
          Location
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </label>
        <div className="modal-actions">
          <Button type="button" secondary onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={ctx.busy === "add-opportunity"}>
            Save opportunity
          </Button>
        </div>
      </form>
    </Modal>
  );
}
