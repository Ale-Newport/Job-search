import { useState } from "react";
import { api, items, label, pretty, useResource } from "./api";
import { AnswerInspector } from "./answer-inspector";
import { AnswerLLMStatusCard } from "./answer-llm";
import {
  Button,
  Editor,
  Empty,
  ErrorBox,
  KeyValues,
  Panel,
  Resource,
  Tag,
} from "./components";
import type { AppContext, Data, Job } from "./types";

export function CandidateIntelligence({
  ctx,
  coverageOnly = false,
}: {
  ctx: AppContext;
  coverageOnly?: boolean;
}) {
  const resource = useResource("/intelligence", ctx.refresh);
  const jobs = useResource(
    coverageOnly ? null : "/jobs?limit=100",
    ctx.refresh,
  );
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [edit, setEdit] = useState<Data | null>(null);
  const [question, setQuestion] = useState("");
  const [jobId, setJobId] = useState("");
  const [help, setHelp] = useState("");
  const [answer, setAnswer] = useState<Data | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState("");
  const data = resource.data || {};
  const entities: Data[] = data.entities || [];
  const facts: Data[] = data.facts || [];
  const categories = [...new Set(entities.map((e) => String(e.kind)))].sort();
  const query = search.toLowerCase();
  const educationSearch =
    /graduat|graduaci|degree|university|education|secondary|high school|sixth form/.test(
      query,
    );
  const matching = entities.filter(
    (e) =>
      (!kind || e.kind === kind) &&
      (!query ||
        (educationSearch && e.kind === "education") ||
        `${e.name} ${e.kind}`.toLowerCase().includes(query) ||
        facts.some(
          (f) =>
            f.entity_id === e.id &&
            `${f.concept} ${JSON.stringify(f.value)}`
              .toLowerCase()
              .includes(query),
        )),
  );
  const linkedIds = new Set(
    (data.relationships || [])
      .filter((r: Data) => matching.some((e) => e.id === r.subject_id))
      .map((r: Data) => r.object_id),
  );
  const visible = entities.filter(
    (e) => matching.includes(e) || (!kind && linkedIds.has(e.id)),
  );
  const missing: string[] = [
    ...new Set<string>(
      (data.coverage || []).flatMap((row: Data) => row.missing || []),
    ),
  ];
  return (
    <>
      <AnswerLLMStatusCard ctx={ctx} />
      <Panel
        title={
          coverageOnly
            ? "Candidate Intelligence · Coverage"
            : "Candidate Intelligence"
        }
        description={
          coverageOnly
            ? "See which concepts have evidence and which still need information."
            : "Verified facts, preferences and country policies form one central knowledge base for future applications."
        }
      >
        <Resource {...resource} retry={resource.reload}>
          {coverageOnly ? (
            <>
              <p className="panel-copy">{data.coverage_note}</p>
              <div className="application-profile-grid">
                {(data.coverage || []).map((row: Data) => (
                  <article
                    className="application-profile-field"
                    key={row.category}
                  >
                    <h4>
                      {row.category} · {row.percentage}%
                    </h4>
                    <progress
                      max="100"
                      value={row.percentage}
                      aria-label={`${row.category} coverage`}
                    />
                    <p>
                      {row.supported} / {row.total} diagnostic concepts
                      supported
                    </p>
                    {(row.missing || []).map((q: string) => (
                      <p className="muted" key={q}>
                        {q}
                      </p>
                    ))}
                  </article>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="application-profile-intro">
                <div>
                  <strong>
                    {
                      facts.filter((f) => f.verification_status === "verified")
                        .length
                    }{" "}
                    verified facts
                  </strong>
                  <span> · As of {data.as_of}</span>
                </div>
                <input
                  aria-label="Search knowledge"
                  placeholder="Search languages, education, projects, evidence…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div
                className="application-profile-tabs"
                role="tablist"
                aria-label="Candidate knowledge categories"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={!kind}
                  onClick={() => setKind("")}
                >
                  All knowledge
                </button>
                {categories.map((category) => (
                  <button
                    type="button"
                    role="tab"
                    key={category}
                    aria-selected={kind === category}
                    onClick={() => setKind(category)}
                  >
                    {label(category)}
                  </button>
                ))}
              </div>
              <div className="application-profile-grid">
                {visible.map((entity) => (
                  <article
                    className="application-profile-field"
                    key={entity.id}
                  >
                    <h4>{entity.name}</h4>
                    <small>{label(entity.kind)}</small>
                    {facts
                      .filter((f) => f.entity_id === entity.id)
                      .map((f) => (
                        <div key={f.id} className="knowledge-fact">
                          <strong>{label(f.concept)}</strong>{" "}
                          <Tag
                            tone={
                              f.verification_status === "verified"
                                ? "green"
                                : f.verification_status === "rejected"
                                  ? "red"
                                  : "amber"
                            }
                          >
                            {label(f.verification_status || "unverified")}
                          </Tag>
                          <p className="prose-text">
                            {Array.isArray(f.value)
                              ? f.value.map(pretty).join(", ")
                              : pretty(f.value)}
                          </p>
                          <details>
                            <summary>Evidence and scope</summary>
                            <KeyValues
                              data={{
                                source: f.source,
                                evidence: f.evidence?.excerpt || f.evidence,
                                country: f.scope?.country || f.country,
                                recorded: f.updated_at || f.created_at,
                                fact_id: f.id,
                              }}
                            />
                          </details>
                          <Button secondary onClick={() => setEdit(f)}>
                            Edit fact
                          </Button>
                        </div>
                      ))}
                    {(data.relationships || [])
                      .filter((r: Data) => r.subject_id === entity.id)
                      .map((r: Data, index: number) => (
                        <p className="muted" key={r.id || index}>
                          {label(r.relation || r.kind || "Related to")}:{" "}
                          {entities.find((e) => e.id === r.object_id)?.name ||
                            r.object_id}
                        </p>
                      ))}
                  </article>
                ))}
              </div>
              {!visible.length && (
                <Empty
                  title="No matching candidate facts"
                  description="Try another search or add verified details in Application profile."
                />
              )}
              <details>
                <summary>Derived facts and reasoning</summary>
                <div className="simple-list">
                  {(data.derived || []).map((a: Data) => (
                    <div key={a.question}>
                      <div>
                        <strong>{a.question}</strong>
                        <p>{pretty(a.answer) || "Not enough evidence"}</p>
                        <Tag tone="amber">Derived</Tag>
                        <AnswerInspector answer={a} />
                      </div>
                    </div>
                  ))}
                </div>
              </details>
              {!!data.policies?.length && (
                <details open>
                  <summary>
                    Professional preferences and scoped policies
                  </summary>
                  <div className="application-profile-grid">
                    {data.policies.map((p: Data) => (
                      <article className="application-profile-field" key={p.id}>
                        <h4>{label(p.subject)}</h4>
                        <Tag tone={p.confirmed ? "green" : "amber"}>
                          {p.confirmed
                            ? "Confirmed policy"
                            : "Needs confirmation"}
                        </Tag>
                        <p>
                          {p.subject === "personal_disclosures"
                            ? (typeof p.value === "object"
                                ? p.value?.action
                                : p.value) === "use_confirmed"
                              ? "Use confirmed profile answers"
                              : "Leave blank"
                            : pretty(p.value)}
                        </p>
                        <small>
                          {p.scope
                            ? `Scope: ${pretty(p.scope)}`
                            : "General candidate policy"}
                        </small>
                        <Button
                          secondary
                          onClick={() =>
                            setEdit({
                              ...p,
                              policy: true,
                              concept: p.subject,
                              value_type:
                                p.subject === "personal_disclosures"
                                  ? "select"
                                  : "text",
                              verification_status: p.confirmed
                                ? "verified"
                                : "unverified",
                              value:
                                typeof p.value === "object"
                                  ? (p.value.action ??
                                    p.value.target ??
                                    pretty(p.value))
                                  : p.value,
                            })
                          }
                        >
                          Edit policy
                        </Button>
                      </article>
                    ))}
                  </div>
                </details>
              )}
              <details open={missing.length <= 8}>
                <summary>
                  Missing factual information · {missing.length} diagnostic
                  questions
                </summary>
                <p className="panel-copy">
                  These diagnostic questions currently lack supported answers.
                  Add verified information once in your profile to make it
                  available to future applications. Open-ended questions also
                  require a configured LLM.
                </p>
                <div className="simple-list">
                  {missing.map((q) => (
                    <div key={q}>
                      <span>{q}</span>
                      <Tag tone="amber">Unsupported</Tag>
                    </div>
                  ))}
                </div>
                {!missing.length && (
                  <p className="panel-copy">
                    The listed diagnostics have support. This does not establish
                    coverage for every possible application question.
                  </p>
                )}
                <div className="panel-actions">
                  <Button secondary onClick={() => ctx.navigate("profile")}>
                    Open candidate profile
                  </Button>
                </div>
              </details>
              <Panel
                title="Try an application question"
                description="Inspect the exact question, retrieved facts, requested format and answer checks."
              >
                <form
                  className="form-fields"
                  onSubmit={async (e) => {
                    e.preventDefault();
                    setAsking(true);
                    setError("");
                    setAnswer(null);
                    try {
                      const job = items<Job>(jobs.data).find(
                        (j) => j.id === jobId,
                      );
                      setAnswer(
                        await api(
                          "/intelligence/answer",
                          "POST",
                          {
                            question,
                            field: { help_text: help },
                            job: job || {},
                          },
                          undefined,
                          300000,
                        ),
                      );
                    } catch (e) {
                      setError(e instanceof Error ? e.message : String(e));
                    } finally {
                      setAsking(false);
                    }
                  }}
                >
                  <label className="field">
                    <span>Exact application question</span>
                    <textarea
                      value={question}
                      onChange={(e) => {
                        setQuestion(e.target.value);
                        setAnswer(null);
                      }}
                      placeholder="Highest completed degree?"
                      required
                      maxLength={4000}
                      rows={3}
                      disabled={asking}
                    />
                  </label>
                  <label className="field">
                    <span>Job context (optional)</span>
                    <select
                      aria-label="Job context"
                      value={jobId}
                      onChange={(e) => {
                        setJobId(e.target.value);
                        setAnswer(null);
                      }}
                      disabled={asking}
                    >
                      <option value="">General candidate question</option>
                      {items<Job>(jobs.data).map((job) => (
                        <option key={job.id} value={job.id}>
                          {job.company} · {job.title} · {job.location}
                        </option>
                      ))}
                    </select>
                    <small>
                      Choose a job for company, role, location and discovery
                      context.
                    </small>
                  </label>
                  {jobs.error && (
                    <ErrorBox
                      error={`Job context could not be loaded: ${jobs.error}`}
                      retry={jobs.reload}
                    />
                  )}
                  <label className="field">
                    <span>Field help or format instructions (optional)</span>
                    <input
                      value={help}
                      onChange={(e) => {
                        setHelp(e.target.value);
                        setAnswer(null);
                      }}
                      placeholder="For example: MM/YYYY"
                      disabled={asking}
                    />
                  </label>
                  <Button
                    type="submit"
                    loading={asking}
                    disabled={!question.trim()}
                  >
                    Explain answer
                  </Button>
                </form>
                {asking && (
                  <p role="status" className="panel-copy">
                    Generating an answer for this question…
                  </p>
                )}
                {error && <ErrorBox error={error} />}
                {answer && (
                  <article className="application-profile-field">
                    <Tag>{answer.confidence_level || "Unknown"}</Tag>
                    <p className="prose-text">
                      {pretty(answer.answer) ||
                        (answer.leave_blank
                          ? "Left blank by your policy"
                          : "No supported answer")}
                    </p>
                    <AnswerInspector answer={answer} open />
                  </article>
                )}
              </Panel>
            </>
          )}
          {!!data.conflicts?.length && (
            <details>
              <summary>{data.conflicts.length} evidence conflicts</summary>
              {data.conflicts.map((c: Data, index: number) => (
                <p key={index}>
                  {c.entity} · {c.concept}: {c.reason}
                </p>
              ))}
            </details>
          )}
          {edit && (
            <Editor
              title={label(edit.concept)}
              initial={{
                value: Array.isArray(edit.value)
                  ? edit.value.join("\n")
                  : typeof edit.value === "boolean"
                    ? edit.value
                      ? "Yes"
                      : "No"
                    : pretty(edit.value),
                confirmed: edit.verification_status === "verified",
              }}
              fields={[
                {
                  key: "value",
                  label: "Value",
                  type:
                    edit.value_type === "select"
                      ? "select"
                      : edit.value_type === "date"
                        ? "date"
                        : edit.value_type === "list"
                          ? "textarea"
                          : edit.value_type === "number" ||
                              edit.value_type === "integer"
                            ? "number"
                            : "text",
                  options:
                    edit.concept === "personal_disclosures"
                      ? [
                          {
                            value: "leave_blank",
                            label: "Leave personal disclosures blank",
                          },
                          {
                            value: "use_confirmed",
                            label: "Use confirmed profile answers",
                          },
                        ]
                      : undefined,
                  hint:
                    edit.concept === "personal_disclosures"
                      ? "Uses only values you explicitly confirm in Application profile. Unspecified personal attributes are never inferred."
                      : undefined,
                },
                {
                  key: "confirmed",
                  label: edit.policy
                    ? "Confirmed — apply this policy"
                    : "Confirmed — allow relevant application use",
                  type: "checkbox",
                },
              ]}
              onClose={() => setEdit(null)}
              onSave={async (values) => {
                await api(
                  `/intelligence/${edit.policy ? "policies" : "facts"}/${edit.id}`,
                  "PUT",
                  values,
                );
                setEdit(null);
                await ctx.act(
                  "knowledge-refresh",
                  async () => ({}),
                  "Knowledge updated.",
                );
              }}
            />
          )}
        </Resource>
      </Panel>
    </>
  );
}
