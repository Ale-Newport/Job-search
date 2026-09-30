import { OpportunityActions } from "./tracking";
import { useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  CheckCheck,
  ExternalLink,
  FileText,
  Hand,
  Link2,
  Mail,
  Pencil,
  Play,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import type {
  AppContext,
  Application,
  Data,
  Fact,
  Job,
  Selection,
} from "./types";
import {
  api,
  date,
  download,
  items,
  label,
  openExternal,
  pretty,
  showReviewWindow,
  time,
  useResource,
} from "./api";
import {
  Button,
  Editor,
  Empty,
  ErrorBox,
  JsonDetails,
  KeyValues,
  Modal,
  Panel,
  Resource,
  Score,
  Status,
  Tag,
} from "./components";
import { DocumentVersions, Timeline } from "./pages";

export function Detail({
  selection,
  ctx,
  onClose,
}: {
  selection: Selection;
  ctx: AppContext;
  onClose: () => void;
}) {
  const names = {
    job: "Opportunity details",
    application: "Application workspace",
    email: "Email evidence",
    run: "Browser agent inspector",
    document: "Document versions",
  };
  return (
    <Modal wide title={names[selection.kind]} onClose={onClose}>
      {selection.kind === "job" ? (
        <JobDetail id={selection.id} ctx={ctx} />
      ) : selection.kind === "application" ? (
        <ApplicationDetail id={selection.id} ctx={ctx} />
      ) : selection.kind === "email" ? (
        <EmailDetail id={selection.id} ctx={ctx} />
      ) : selection.kind === "run" ? (
        <RunDetail id={selection.id} ctx={ctx} />
      ) : (
        <DocumentVersions id={selection.id} ctx={ctx} />
      )}
    </Modal>
  );
}
export function JobDetail({ id, ctx }: { id: string; ctx: AppContext }) {
  const r = useResource<Job>(`/jobs/${id}`, ctx.refresh),
    match = useResource(`/jobs/${id}/match`, ctx.refresh),
    [prepare, setPrepare] = useState(false),
    [generate, setGenerate] = useState(false),
    [feedback, setFeedback] = useState(false),
    j = r.data;
  return (
    <Resource {...r} retry={r.reload}>
      {j && (
        <>
          <div className="detail-hero">
            <div className="company-avatar large">{j.company.slice(0, 1)}</div>
            <div className="grow">
              <h2>{j.title}</h2>
              <p>
                {j.company} · {j.location || "Location not specified"}
              </p>
              <div className="tags">
                <Status value={j.status} />
                {j.ats && <Tag>{label(j.ats)}</Tag>}
                {j.remote && <Tag>Remote</Tag>}
                {j.priority_score != null && (
                  <Tag>Priority {Math.round(j.priority_score)}</Tag>
                )}
              </div>
            </div>
            <div className="detail-score">
              <Score value={j.match_score} />
              <small>Profile match</small>
            </div>
          </div>
          <div className="detail-actions">
            <OpportunityActions
              job={{
                ...j,
                submitted:
                  !!j.application &&
                  !["PREPARING", "NEEDS_REVIEW"].includes(j.application.status),
              }}
              ctx={ctx}
            />
            <Button secondary onClick={() => setPrepare(true)}>
              Prepare documents
            </Button>
            <Button secondary onClick={() => setGenerate(true)}>
              <Sparkles size={15} />
              Tailor documents
            </Button>
            <Button
              secondary
              onClick={() =>
                ctx.act("external", () =>
                  openExternal(j.application_url || j.url),
                )
              }
            >
              <ExternalLink size={15} />
              View posting
            </Button>
            <div className="grow" />
            <Button
              secondary
              onClick={() =>
                ctx.act(
                  "feedback",
                  () =>
                    api(`/jobs/${id}/feedback`, "POST", { interested: true }),
                  "Interest saved.",
                )
              }
            >
              <ThumbsUp size={15} />
              Interested
            </Button>
            <Button secondary onClick={() => setFeedback(true)}>
              <ThumbsDown size={15} />
              Pass
            </Button>
          </div>
          <div className="detail-grid">
            <div>
              {j.metadata?.company_description && (
                <Panel
                  title="About the company"
                  description={`Source: ${j.metadata.company_source || j.source}`}
                >
                  <p className="panel-copy">{j.metadata.company_description}</p>
                  {j.metadata.company_url && (
                    <Button
                      secondary
                      onClick={() =>
                        ctx.act("company-site", () =>
                          openExternal(j.metadata.company_url),
                        )
                      }
                    >
                      Company careers site <ExternalLink size={14} />
                    </Button>
                  )}
                </Panel>
              )}
              <Panel title="Application details">
                <KeyValues
                  data={{
                    deadline:
                      j.metadata?.application_deadline || "Not supplied",
                    cv_required:
                      j.metadata?.cv_required == null
                        ? "Not supplied"
                        : j.metadata.cv_required
                          ? "Yes"
                          : "No",
                    cover_letter: j.metadata?.cover_letter || "Not supplied",
                    written_answers:
                      j.metadata?.written_answers || "Not supplied",
                    sponsorship: j.metadata?.sponsorship || "Not supplied",
                    recruitment_process:
                      j.metadata?.recruitment_process || "Not supplied",
                  }}
                />
              </Panel>
              <Panel title="Role overview">
                <div className="prose-text">
                  {j.description ||
                    "The source did not provide a full job description. Open the original posting to review the requirements."}
                </div>
              </Panel>
              {(j.skills?.length ?? 0) > 0 && (
                <Panel title="Skills in this role">
                  <div className="tags panel-copy">
                    {j.skills?.map((s: string | Data, i: number) => (
                      <Tag key={typeof s === "string" ? s : i}>
                        {typeof s === "string" ? s : s.name || s.skill}
                      </Tag>
                    ))}
                  </div>
                </Panel>
              )}
            </div>
            <div>
              <Panel
                title="Why this match"
                description="An explainable comparison with your profile"
              >
                <Resource {...match} retry={match.reload}>
                  {match.data ? (
                    <MatchBreakdown data={match.data} />
                  ) : (
                    <p className="panel-copy">
                      No match explanation is available yet.
                    </p>
                  )}
                </Resource>
              </Panel>
              <Panel title="Opportunity details">
                <KeyValues
                  data={{
                    source: j.source,
                    ats: j.ats,
                    location: j.location,
                    salary:
                      j.salary_min || j.salary_max
                        ? `${j.currency || ""} ${j.salary_min ?? "?"} – ${j.salary_max ?? "?"}`
                        : "Not published",
                    posted: j.posted_at ? date(j.posted_at) : undefined,
                    discovered: date(j.created_at),
                  }}
                />
              </Panel>
            </div>
          </div>
          {prepare && (
            <PrepareJob job={j} ctx={ctx} onClose={() => setPrepare(false)} />
          )}{" "}
          {generate && (
            <GenerateDocument
              job={j}
              ctx={ctx}
              onClose={() => setGenerate(false)}
            />
          )}{" "}
          {feedback && (
            <Editor
              title="What doesn’t fit?"
              description="Your feedback refines ranking without changing facts in your candidate profile."
              fields={[
                {
                  key: "reason",
                  label: "Reason (optional)",
                  type: "select",
                  options: [
                    "wrong_role",
                    "wrong_location",
                    "too_senior",
                    "salary",
                    "company",
                    "technology",
                    "other",
                  ],
                },
              ]}
              onClose={() => setFeedback(false)}
              submit="Save feedback"
              onSave={async (values) => {
                await api(`/jobs/${id}/feedback`, "POST", {
                  interested: false,
                  ...values,
                });
                await ctx.act(
                  "refresh",
                  () => Promise.resolve({}),
                  "Feedback saved.",
                );
              }}
            />
          )}
        </>
      )}
    </Resource>
  );
}
function MatchBreakdown({ data }: { data: Data }) {
  const d = data.details || data.match_details || data;
  return (
    <div className="match-breakdown">
      {(d.strengths || d.matched_skills || []).length > 0 && (
        <div>
          <h4>Aligned experience</h4>
          <div className="tags">
            {(d.strengths || d.matched_skills).map((v: any, i: number) => (
              <Tag tone="green" key={i}>
                {typeof v === "string" ? v : pretty(v)}
              </Tag>
            ))}
          </div>
        </div>
      )}
      {(d.gaps || d.missing_skills || []).length > 0 && (
        <div>
          <h4>Room to grow</h4>
          <div className="tags">
            {(d.gaps || d.missing_skills).map((v: any, i: number) => (
              <Tag tone="amber" key={i}>
                {typeof v === "string" ? v : pretty(v)}
              </Tag>
            ))}
          </div>
        </div>
      )}
      <KeyValues
        data={d}
        exclude={[
          "strengths",
          "gaps",
          "matched_skills",
          "missing_skills",
          "job_id",
          "id",
          "created_at",
        ]}
      />
    </div>
  );
}
function PrepareJob({
  job,
  ctx,
  onClose,
}: {
  job: Job;
  ctx: AppContext;
  onClose: () => void;
}) {
  const docs = useResource("/documents?limit=500", ctx.refresh),
    settings = useResource("/settings"),
    [doc, setDoc] = useState(""),
    [mode, setMode] = useState("review"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current || !settings.data || !docs.data) return;
    initialized.current = true;
    setMode(settings.data.default_mode || "review");
    if (
      items(docs.data).some(
        (d) =>
          d.latest_version_id === settings.data?.default_document_version_id,
      )
    ) {
      setDoc(settings.data.default_document_version_id);
    }
  }, [settings.data, docs.data]);
  return (
    <Modal
      title="Prepare this application"
      description={`${job.title} at ${job.company}`}
      onClose={onClose}
    >
      <div className="form-fields">
        <label className="field">
          <span>Application mode</span>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="manual">Manual — prepare only</option>
            <option value="review">
              Review — fill, then wait for your approval
            </option>
            <option value="auto">
              Auto — submit only when every safety gate passes
            </option>
          </select>
        </label>
        <label className="field">
          <span>CV version</span>
          <select value={doc} onChange={(e) => setDoc(e.target.value)}>
            <option value="">Choose later / no document</option>
            {items(docs.data).map((d) => (
              <option
                value={
                  d.latest_version_id ||
                  d.latest_version?.id ||
                  d.versions?.[0]?.id ||
                  ""
                }
                key={d.id}
              >
                {d.name || d.filename} · version{" "}
                {d.latest_version?.version || 1}
                {d.latest_version?.approved
                  ? " · approved"
                  : " · needs approval"}
              </option>
            ))}
          </select>
          <small>
            Choose the exact CV you want to use. A missing required attachment
            pauses automation.
          </small>
        </label>
        <div className="info-card">
          <ShieldCheck size={20} />
          <p>
            {mode === "review"
              ? "Review mode fills the form and pauses before submission, so you can check the answers and attachments."
              : mode === "auto"
                ? "Auto mode still requires verified answers, an approved domain, sufficient confidence, and rate limits."
                : "Manual mode creates a tracked application without filling or submitting the form."}
          </p>
        </div>
        {error && <ErrorBox error={error} />}
      </div>
      <div className="modal-footer">
        <Button secondary onClick={onClose}>
          Cancel
        </Button>
        <Button
          loading={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const a = await api(`/jobs/${job.id}/prepare`, "POST", {
                mode,
                ...(doc ? { document_version_id: doc } : {}),
              });
              await ctx.act(
                "refresh",
                () => Promise.resolve({}),
                "Application prepared.",
              );
              onClose();
              const id = a.id || a.application?.id || a.application_id;
              if (id) ctx.select({ kind: "application", id });
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Prepare application
          <ArrowRight size={15} />
        </Button>
      </div>
    </Modal>
  );
}
function GenerateDocument({
  job,
  ctx,
  onClose,
}: {
  job: Job;
  ctx: AppContext;
  onClose: () => void;
}) {
  const facts = useResource("/facts?limit=500", ctx.refresh),
    verified = items<Fact>(facts.data).filter(
      (f) => f.verification_status.toLowerCase() === "verified" || f.locked,
    ),
    [selected, setSelected] = useState<string[]>([]),
    [kind, setKind] = useState("cv"),
    [result, setResult] = useState<Data | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const generate = async (approved: boolean) => {
    setBusy(true);
    setError("");
    try {
      const r = await api("/documents/generate", "POST", {
        job_id: job.id,
        kind,
        fact_ids: selected,
        approved,
      });
      setResult(r);
      if (approved) {
        await ctx.act(
          "refresh",
          () => Promise.resolve(r),
          "Approved document saved as a new version.",
        );
        onClose();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      wide
      title="Tailor a document"
      description="Choose verified facts. Preview the result, then approve it as an immutable PDF."
      onClose={onClose}
    >
      <div className="form-fields">
        <label className="field">
          <span>Document type</span>
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setResult(null);
            }}
          >
            <option value="cv">Tailored CV</option>
            <option value="cover_letter">Cover letter</option>
          </select>
        </label>
        <div className="field">
          <span>Verified evidence</span>
          <Resource {...facts} retry={facts.reload}>
            {verified.length ? (
              <div className="fact-picker">
                {verified.map((f) => (
                  <label key={f.id}>
                    <input
                      type="checkbox"
                      checked={selected.includes(f.id)}
                      onChange={(e) => {
                        setSelected(
                          e.target.checked
                            ? [...selected, f.id]
                            : selected.filter((v) => v !== f.id),
                        );
                        setResult(null);
                      }}
                    />
                    <div>
                      <strong>{label(f.key)}</strong>
                      <small>{pretty(f.value)}</small>
                    </div>
                  </label>
                ))}
              </div>
            ) : (
              <p>
                No verified facts are available. Verify your candidate profile
                first.
              </p>
            )}
          </Resource>
        </div>
        {result && (
          <Panel title="Review your draft">
            <pre className="document-preview">
              {result.text ||
                result.preview ||
                result.content ||
                pretty(result)}
            </pre>
            {result.diff && (
              <JsonDetails title="Changes from source" data={result.diff} />
            )}
            <JsonDetails
              title="Evidence and generation details"
              data={result}
            />
          </Panel>
        )}
        {error && <ErrorBox error={error} />}
      </div>
      <div className="modal-footer">
        <Button secondary onClick={onClose}>
          Cancel
        </Button>
        <Button
          secondary
          disabled={!selected.length}
          loading={busy}
          onClick={() => generate(false)}
        >
          <Sparkles size={15} />
          Generate preview
        </Button>
        {result && (
          <Button loading={busy} onClick={() => generate(true)}>
            <Check size={15} />
            Approve & save PDF
          </Button>
        )}
      </div>
    </Modal>
  );
}
export function ApplicationDetail({
  id,
  ctx,
}: {
  id: string;
  ctx: AppContext;
}) {
  const r = useResource<Application>(`/applications/${id}`, ctx.refresh),
    [tab, setTab] = useState("overview"),
    [edit, setEdit] = useState(false),
    [answer, setAnswer] = useState(false),
    [approve, setApprove] = useState(false),
    [sectionReview, setSectionReview] = useState(false),
    [reconcile, setReconcile] = useState(false),
    a = r.data;
  const refreshApplication = r.reload;
  const previousRunStatus = useRef<string | undefined>(undefined);
  const runStatus = a?.runs?.at(-1)?.status;
  useEffect(() => {
    const wasRunning = ["running", "submitting"].includes(
      previousRunStatus.current || "",
    );
    previousRunStatus.current = runStatus;
    if (
      wasRunning &&
      ["section_review", "human_required", "needs_review", "error"].includes(
        runStatus || "",
      )
    ) {
      void showReviewWindow().catch(() => undefined);
    }
  }, [runStatus]);
  const active =
    ctx.busy === "apply" ||
    ctx.busy === "approve" ||
    ctx.busy === "approve-section" ||
    ["running", "submitting"].includes(a?.runs?.at(-1)?.status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refreshApplication, 2500);
    return () => clearInterval(timer);
  }, [active, refreshApplication]);
  return (
    <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
      {a && (
        <>
          <div className="detail-hero">
            <span className="company-avatar large">
              {(a.job?.company || a.company || "A").slice(0, 1)}
            </span>
            <div className="grow">
              <h2>{a.job?.title || a.title || "Application"}</h2>
              <p>{a.job?.company || a.company}</p>
              <div className="tags">
                <Status value={a.status} />
                <Tag>
                  {a.assisted_autofill
                    ? "Autofill · final approval"
                    : a.section_consent
                      ? "Approval per section"
                      : `${label(a.mode || "review")} mode`}
                </Tag>
                <Tag>Created {date(a.created_at)}</Tag>
              </div>
            </div>
            <Button secondary onClick={() => setEdit(true)}>
              <Pencil size={15} />
              Edit tracking
            </Button>
          </div>
          <div className="detail-actions">
            {a.mode !== "manual" && (
              <>
                {a.runs?.at(-1)?.status === "section_review" ? (
                  <Button onClick={() => setSectionReview(true)}>
                    <ShieldCheck size={15} />
                    Review section
                  </Button>
                ) : ["needs_review", "ready_for_review"].includes(
                    a.runs?.at(-1)?.status,
                  ) ? (
                  <Button onClick={() => setApprove(true)}>
                    <ShieldCheck size={15} />
                    Review & approve submission
                  </Button>
                ) : (
                  ![
                    "APPLIED",
                    "CONFIRMED",
                    "OFFER",
                    "REJECTED",
                    "WITHDRAWN",
                    "INTERVIEW",
                    "FINAL_INTERVIEW",
                  ].includes(a.status) && (
                    <Button
                      loading={active}
                      onClick={() =>
                        ctx.act(
                          "apply",
                          () => api(`/applications/${id}/apply`, "POST", {}),
                          "Browser run started.",
                        )
                      }
                    >
                      <Play size={15} />
                      Apply
                    </Button>
                  )
                )}
                <Button
                  secondary
                  onClick={() =>
                    ctx.act(
                      "takeover",
                      () => api(`/applications/${id}/takeover`, "POST", {}),
                      "Agent paused. Your browser is ready for manual control.",
                    )
                  }
                >
                  <Hand size={15} />
                  Take control
                </Button>
                <Button
                  secondary
                  onClick={() =>
                    ctx.act(
                      "resume",
                      () => api(`/applications/${id}/resume`, "POST", {}),
                      "Agent resumed from a fresh observation.",
                    )
                  }
                >
                  <Play size={15} />
                  Resume agent
                </Button>
                {a.runs?.some((run) =>
                  ["interrupted", "unconfirmed", "submitting"].includes(
                    run.status,
                  ),
                ) && (
                  <Button secondary onClick={() => setReconcile(true)}>
                    <ShieldCheck size={15} />
                    Reconcile submission
                  </Button>
                )}
              </>
            )}
            <Button
              secondary
              onClick={() => ctx.select({ kind: "job", id: a.job_id })}
            >
              View opportunity
              <ArrowRight size={15} />
            </Button>
          </div>
          {a.section_consent && a.runs?.at(-1)?.status === "section_review" && (
            <div className="tracking-callout">
              <ShieldCheck size={20} />
              <p>
                Waiting for your approval:{" "}
                {a.runs.at(-1)?.checkpoint?.section?.title}. Review the proposed
                values before Meridian fills this section.
              </p>
              <Button onClick={() => setSectionReview(true)}>
                Review section
              </Button>
            </div>
          )}
          <div className="tabs">
            {(a.mode === "manual"
              ? ["overview", "documents", "email"]
              : ["overview", "answers", "documents", "email", "automation"]
            ).map((t) => (
              <button
                key={t}
                className={tab === t ? "active" : ""}
                onClick={() => setTab(t)}
              >
                {label(t)}
                {t === "answers" && a.answers?.length
                  ? ` (${a.answers.length})`
                  : ""}
              </button>
            ))}
          </div>
          {tab === "overview" && (
            <div className="detail-grid">
              <Panel title="Application timeline">
                {a.events?.length ? (
                  <Timeline events={a.events} />
                ) : (
                  <Empty
                    title="No events recorded"
                    description="Each tracked change will appear in this timeline."
                  />
                )}
              </Panel>
              <div>
                <Panel title="Application notes">
                  <p className="panel-copy prose-text">
                    {a.notes ||
                      "Keep recruiter details, reflections, and next steps here. Use Edit tracking to add a note."}
                  </p>
                </Panel>
                <Panel title="Open tasks">
                  {a.tasks?.length ? (
                    <div className="simple-list">
                      {a.tasks.map((t) => (
                        <div key={t.id}>
                          <ShieldCheck size={16} />
                          <div>
                            <strong>
                              {t.question ||
                                t.title ||
                                t.reason ||
                                label(t.kind)}
                            </strong>
                            <Status value={t.status} />
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="panel-copy">
                      No open tasks for this application.
                    </p>
                  )}
                  <div className="panel-copy">
                    <Button
                      secondary
                      onClick={() => {
                        ctx.select(null);
                        ctx.navigate("review");
                      }}
                    >
                      Open review queue
                      <ArrowRight size={14} />
                    </Button>
                  </div>
                </Panel>
              </div>
            </div>
          )}
          {tab === "answers" && (
            <Panel
              title="Answers & provenance"
              description="Each answer retains the facts that support it"
              action={
                <Button secondary onClick={() => setAnswer(true)}>
                  <PlusIcon />
                  Add verified answer
                </Button>
              }
            >
              {a.answers?.length ? (
                <div className="answer-list">
                  {a.answers.map((v, i) => (
                    <div className="answer-card" key={v.id || i}>
                      <div className="row-between">
                        <h4>{v.question}</h4>
                        <Status
                          value={v.verified ? "verified" : "unverified"}
                        />
                      </div>
                      <p className="prose-text">{v.answer}</p>
                      <JsonDetails
                        title="Why this answer? View evidence"
                        data={{
                          fact_ids: v.fact_ids || v.facts_used,
                          evidence: v.evidence,
                          confidence: v.confidence,
                          source: v.source,
                        }}
                      />
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="No saved answers"
                  description="Add a verified answer or let the browser surface a question. Unsupported information always requires your input."
                />
              )}
            </Panel>
          )}
          {tab === "documents" && (
            <Panel title="Documents selected for this application">
              {a.documents?.length ? (
                <div className="simple-list">
                  {a.documents.map((d, i) => (
                    <div key={d.id || i}>
                      <FileText size={19} />
                      <div className="grow">
                        <strong>
                          {d.name || d.filename || label(d.kind || "Document")}
                        </strong>
                        <small>
                          Version{" "}
                          {d.version || d.document_version_id || d.version_id}
                        </small>
                      </div>
                      <Button
                        secondary
                        onClick={() =>
                          ctx.act("download", () =>
                            download(
                              `/document-versions/${d.document_version_id || d.version_id || d.id}/download`,
                              d.filename || d.name || "document.pdf",
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
                  title="No attachments selected"
                  description="Prepare the opportunity with a CV version before filling a form that requires an attachment."
                />
              )}
            </Panel>
          )}
          {tab === "email" && (
            <Panel title="Linked messages">
              {a.emails?.length ? (
                <div className="simple-list">
                  {a.emails.map((e) => (
                    <div key={e.id}>
                      <Mail size={18} />
                      <div className="grow">
                        <strong>{e.subject}</strong>
                        <small>{time(e.received_at)}</small>
                      </div>
                      <Button
                        secondary
                        onClick={() => ctx.select({ kind: "email", id: e.id })}
                      >
                        Read evidence
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="No linked messages yet"
                  description="Confirmation emails and recruitment updates will appear here after syncing your inbox."
                />
              )}
            </Panel>
          )}
          {tab === "automation" && (
            <Panel
              title="Automation runs"
              description="Inspect observations, decisions, execution, and verification"
            >
              {a.runs?.length ? (
                <div className="simple-list">
                  {a.runs.map((run) => (
                    <div key={run.id}>
                      <span className="code">{run.id.slice(0, 8)}</span>
                      <div className="grow">
                        <Status value={run.status} />
                        <small>
                          {time(run.created_at)} · {run.engine || "hybrid"}
                        </small>
                        {run.error && (
                          <small className="error-text">{run.error}</small>
                        )}
                      </div>
                      <Button
                        secondary
                        onClick={() => ctx.select({ kind: "run", id: run.id })}
                      >
                        Inspect
                        <ArrowRight size={14} />
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty
                  title="No browser runs yet"
                  description="Starting Apply creates an observable browser run for this application."
                />
              )}
            </Panel>
          )}
          {edit && (
            <Editor
              title="Update application tracking"
              fields={[
                {
                  key: "mode",
                  label: "Application mode",
                  type: "select",
                  options: ["manual", "review", "auto"],
                  required: true,
                },
                {
                  key: "status",
                  label: "Status",
                  type: "select",
                  options: [
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
                  ],
                  required: true,
                },
                { key: "notes", label: "Notes", type: "textarea" },
              ]}
              initial={a}
              onClose={() => setEdit(false)}
              onSave={async (values) => {
                await api(`/applications/${id}`, "PATCH", values);
                await ctx.act(
                  "refresh",
                  () => Promise.resolve({}),
                  "Application updated.",
                );
              }}
            />
          )}
          {reconcile && (
            <Editor
              title="Reconcile submission status"
              description="Check the original site or confirmation email before choosing an outcome. An uncertain submission is never retried automatically."
              fields={[
                {
                  key: "status",
                  label: "Verified outcome",
                  type: "select",
                  options: [
                    {
                      value: "CONFIRMED",
                      label: "Submission confirmed by evidence",
                    },
                    {
                      value: "PREPARING",
                      label: "Not submitted — allow preparation again",
                    },
                    { value: "WITHDRAWN", label: "Withdraw / do not retry" },
                  ],
                  required: true,
                },
                {
                  key: "evidence",
                  label: "Evidence for this decision",
                  type: "textarea",
                  required: true,
                },
              ]}
              initial={{ status: "CONFIRMED" }}
              onClose={() => setReconcile(false)}
              onSave={async (value) => {
                await api(`/applications/${id}/reconcile`, "POST", value);
                await ctx.act(
                  "refresh",
                  () => Promise.resolve({}),
                  "Submission status reconciled.",
                );
              }}
            />
          )}
          {answer && (
            <AnswerComposer
              application={a}
              ctx={ctx}
              onClose={() => setAnswer(false)}
            />
          )}{" "}
          {approve && (
            <Approval
              application={a}
              ctx={ctx}
              onClose={() => setApprove(false)}
            />
          )}
          {sectionReview && (
            <SectionApproval
              application={a}
              ctx={ctx}
              close={() => setSectionReview(false)}
            />
          )}
        </>
      )}
    </Resource>
  );
}
function PlusIcon() {
  return <Pencil size={15} />;
}
function SectionApproval({
  application: a,
  ctx,
  close,
}: {
  application: Application;
  ctx: AppContext;
  close: () => void;
}) {
  const checkpoint = a.runs?.at(-1)?.checkpoint,
    section = checkpoint?.section;
  return (
    <Modal
      wide
      title={`Review section · ${section?.title || "Application"}`}
      onClose={close}
    >
      <p className="panel-copy">
        {a.job?.title} · {a.job?.company}
      </p>
      <p className="panel-copy">Destination: {section?.destination}</p>
      <p className="panel-copy">
        {section?.kind === "continue"
          ? "Approve moving to the next step with the current form values. The next section will require another review."
          : "Approve these exact values and attachments for this section. Unknown answers are left for you to resolve."}
      </p>
      <div className="answer-list">
        {(section?.fields || []).map((field: Data, index: number) => (
          <div className="answer-card" key={index}>
            <h4>{field.question}</h4>
            <p>{field.answer}</p>
            <small>
              {field.operation === "UPLOAD"
                ? "Selected document"
                : field.operation === "CLICK" && section.kind === "continue"
                  ? "Navigation only"
                  : "Verified profile answer"}
            </small>
          </div>
        ))}
      </div>
      <p className="panel-copy">
        Final submission always requires a separate approval.
      </p>
      <div className="modal-actions">
        <Button secondary onClick={close}>
          Keep reviewing
        </Button>
        <Button
          disabled={!section}
          loading={ctx.busy === "approve-section"}
          onClick={async () => {
            const result = await ctx.act(
              "approve-section",
              () =>
                api(`/applications/${a.id}/approve-section`, "POST", {
                  approved: true,
                  snapshot_id: checkpoint.snapshot_id,
                }),
              "Section approved. Meridian will stop at the next review.",
            );
            if (result) close();
          }}
        >
          {section?.kind === "continue"
            ? "Approve & continue"
            : "Approve & fill section"}
        </Button>
      </div>
    </Modal>
  );
}
function Approval({
  application: a,
  ctx,
  onClose,
}: {
  application: Application;
  ctx: AppContext;
  onClose: () => void;
}) {
  const latest = a.runs?.[a.runs.length - 1],
    quality = latest?.quality || latest?.checkpoint?.quality || a.quality,
    questions: Data[] = latest?.checkpoint?.questions || [];
  return (
    <Modal
      wide
      title="Review before submission"
      description="Approval applies to the current answers, documents, and browser observation. Changed state requires a new review."
      onClose={onClose}
    >
      <div className="form-fields">
        <div className="info-card">
          <ShieldCheck size={22} />
          <div>
            <strong>
              {a.job?.title || a.title} · {a.job?.company || a.company}
            </strong>
            <p>
              Approval authorizes Meridian to submit this application. The
              backend revalidates the form, supporting facts, and safety checks.
            </p>
          </div>
        </div>
        {a.quality_score != null && (
          <div className="row-between">
            <strong>Application quality score</strong>
            <Score value={a.quality_score} />
          </div>
        )}
        {questions.length > 0 && (
          <Panel title="Questions requiring your review">
            <div className="simple-list">
              {questions.map((question, index) => (
                <div key={index}>
                  <ShieldCheck size={18} />
                  <div>
                    <strong>
                      {question.question ||
                        question.reason ||
                        "Human input required"}
                    </strong>
                    <small>{label(question.kind)}</small>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        )}
        <Panel title="Answers to be submitted">
          {a.answers?.length ? (
            <div className="answer-list">
              {a.answers.map((v, i) => (
                <div className="answer-card" key={v.id || i}>
                  <div className="row-between">
                    <h4>{v.question}</h4>
                    <Status value={v.verified ? "verified" : "unverified"} />
                  </div>
                  <p>{v.answer}</p>
                  <small>
                    Supporting facts:{" "}
                    {(v.fact_ids || v.facts_used || []).join(", ") ||
                      "No fact references"}
                  </small>
                </div>
              ))}
            </div>
          ) : (
            <p className="panel-copy">
              No answer records were returned. Inspect the browser run to verify
              the current form before approving.
            </p>
          )}
        </Panel>
        <Panel title="Attachments">
          {a.documents?.length ? (
            <div className="simple-list">
              {a.documents.map((d, i) => (
                <div key={d.id || i}>
                  <FileText size={17} />
                  <div>
                    <strong>{d.name || d.filename || "Document"}</strong>
                    <small>
                      Immutable version:{" "}
                      {d.document_version_id || d.version_id || d.id}
                    </small>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="panel-copy">No attachments recorded.</p>
          )}
        </Panel>
        {quality && (
          <JsonDetails title="Application quality checks" data={quality} open />
        )}
        {latest && (
          <JsonDetails
            title="Current browser checkpoint"
            data={latest.checkpoint || latest}
          />
        )}
      </div>
      <div className="modal-footer">
        <Button secondary onClick={onClose}>
          Keep reviewing
        </Button>
        <Button
          loading={ctx.busy === "approve"}
          disabled={questions.length > 0}
          onClick={async () => {
            const result = await ctx.act(
              "approve",
              () =>
                api(`/applications/${a.id}/approve`, "POST", {
                  approved: true,
                }),
              "Submission approved. Check the run for confirmation.",
            );
            if (result) onClose();
          }}
        >
          <CheckCheck size={16} />
          Approve submission
        </Button>
      </div>
    </Modal>
  );
}
function AnswerComposer({
  application: a,
  ctx,
  onClose,
}: {
  application: Application;
  ctx: AppContext;
  onClose: () => void;
}) {
  const facts = useResource("/facts?limit=500", ctx.refresh),
    verified = items<Fact>(facts.data).filter(
      (f) => f.verification_status.toLowerCase() === "verified" || f.locked,
    ),
    [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState(""),
    [factIds, setFactIds] = useState<string[]>([]),
    [evidence, setEvidence] = useState<Data | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal
      wide
      title="Add a verified answer"
      description="Review any draft before saving. You are confirming the accuracy of the answer."
      onClose={onClose}
    >
      <div className="form-fields">
        <label className="field">
          <span>Question</span>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
          />
        </label>
        <div className="field">
          <span>Relevant verified facts</span>
          <div className="fact-picker">
            {verified.map((f) => (
              <label key={f.id}>
                <input
                  type="checkbox"
                  checked={factIds.includes(f.id)}
                  onChange={(e) =>
                    setFactIds(
                      e.target.checked
                        ? [...factIds, f.id]
                        : factIds.filter((id) => id !== f.id),
                    )
                  }
                />
                <div>
                  <strong>{label(f.key)}</strong>
                  <small>{pretty(f.value)}</small>
                </div>
              </label>
            ))}
          </div>
        </div>
        <Button
          secondary
          disabled={!question || !factIds.length}
          loading={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              const d = await api("/ai/draft", "POST", {
                question,
                job_id: a.job_id,
                fact_ids: factIds,
              });
              setAnswer(d.answer || "");
              setEvidence(d);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Sparkles size={15} />
          Generate evidence-based draft
        </Button>
        <label className="field">
          <span>Your answer</span>
          <textarea
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            rows={6}
          />
        </label>
        {evidence && (
          <JsonDetails title="Draft evidence and confidence" data={evidence} />
        )}{" "}
        {error && <ErrorBox error={error} />}
      </div>
      <div className="modal-footer">
        <Button secondary onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={!question.trim() || !answer.trim()}
          loading={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api(`/applications/${a.id}/answers`, "POST", {
                question,
                answer,
                fact_ids: factIds,
                verified: true,
              });
              await ctx.act(
                "refresh",
                () => Promise.resolve({}),
                "Verified answer saved.",
              );
              onClose();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <Check size={15} />
          Verify & save answer
        </Button>
      </div>
    </Modal>
  );
}
export function EmailDetail({ id, ctx }: { id: string; ctx: AppContext }) {
  const r = useResource(`/emails/${id}`, ctx.refresh),
    apps = useResource("/applications?limit=200", ctx.refresh),
    [applicationId, setApplicationId] = useState(""),
    e = r.data;
  return (
    <Resource {...r} retry={r.reload}>
      {e && (
        <>
          <div className="detail-hero">
            <span className="file-icon">
              <Mail size={25} />
            </span>
            <div className="grow">
              <h2>{e.subject || "(No subject)"}</h2>
              <p>
                {e.sender || e.from_address} · {time(e.received_at || e.date)}
              </p>
            </div>
          </div>
          <div className="detail-grid">
            <Panel title="Original email text">
              <div className="prose-text email-body">
                {e.body_text ||
                  e.body ||
                  e.text ||
                  "No plain text body was supplied by this message."}
              </div>
            </Panel>
            <div>
              <Panel title="Classification & evidence">
                <KeyValues
                  data={{
                    classification: e.classification,
                    confidence: e.confidence,
                    deadline: e.deadline,
                    evidence: e.evidence,
                    linked_application: e.application_id,
                  }}
                />
              </Panel>
              <Panel title="Link to an application">
                <div className="form-fields">
                  <label className="field">
                    <span>Application</span>
                    <select
                      value={applicationId || e.application_id || ""}
                      onChange={(event) => setApplicationId(event.target.value)}
                    >
                      <option value="">Select an application</option>
                      {items<Application>(apps.data).map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.title || a.job?.title || a.id} ·{" "}
                          {a.company || a.job?.company}
                        </option>
                      ))}
                    </select>
                  </label>
                  <Button
                    disabled={!applicationId}
                    onClick={() =>
                      ctx.act(
                        "email-link",
                        () =>
                          api(`/emails/${id}/link`, "POST", {
                            application_id: applicationId,
                          }),
                        "Email linked to application.",
                      )
                    }
                  >
                    <Link2 size={15} />
                    Link message
                  </Button>
                </div>
              </Panel>
            </div>
          </div>
        </>
      )}
    </Resource>
  );
}
export function RunDetail({ id, ctx }: { id: string; ctx: AppContext }) {
  const r = useResource(`/automation/runs/${id}`, ctx.refresh),
    run = r.data,
    steps = run?.steps || [];
  const refreshRun = r.reload;
  const active = ["running", "submitting"].includes(run?.status);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refreshRun, 2500);
    return () => clearInterval(timer);
  }, [active, refreshRun]);
  return (
    <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
      {run && (
        <>
          <div className="detail-hero">
            <div className="grow">
              <h2>
                Run <span className="code">{id.slice(0, 8)}</span>
              </h2>
              <p>
                {label(run.engine || "hybrid")} · {time(run.created_at)}
              </p>
            </div>
            <Status value={run.status} />
            <Button secondary onClick={r.reload}>
              Refresh observation
            </Button>
          </div>
          {run.error && (
            <ErrorBox
              error={
                typeof run.error === "string" ? run.error : pretty(run.error)
              }
            />
          )}
          <div className="inspector-summary">
            <KeyValues
              data={run}
              exclude={[
                "steps",
                "id",
                "checkpoint",
                "observations",
                "application_id",
                "created_at",
                "updated_at",
                "status",
                "error",
              ]}
            />
          </div>
          {run.checkpoint && (
            <JsonDetails
              title="Current checkpoint & observation"
              data={run.checkpoint}
              open
            />
          )}
          <Panel title="Observation → decision → execution → verification">
            {steps.length ? (
              <div className="inspector-steps">
                {steps.map((s: Data, i: number) => (
                  <details
                    key={s.id || i}
                    className="inspector-step"
                    open={i === steps.length - 1}
                  >
                    <summary>
                      <span className="step-number">{i + 1}</span>
                      <strong>
                        {s.operation ||
                          s.action?.operation ||
                          s.kind ||
                          "Browser step"}
                      </strong>
                      {s.target != null && <Tag>Target {s.target}</Tag>}
                      <span className="grow" />
                      <Status
                        value={
                          s.status ||
                          s.result?.status ||
                          (s.success ? "success" : "recorded")
                        }
                      />
                      <time>
                        {s.duration_ms != null
                          ? `${s.duration_ms} ms`
                          : time(s.created_at)}
                      </time>
                    </summary>
                    <KeyValues data={s} />
                  </details>
                ))}
              </div>
            ) : (
              <Empty
                title="No browser steps recorded"
                description="A run records each observed state, selected action, and result as it progresses."
              />
            )}
          </Panel>
        </>
      )}
    </Resource>
  );
}
