import { label, pretty } from "./api";
import { JsonDetails, KeyValues, Status, Tag } from "./components";
import type { AnswerDiagnostic, Data } from "./types";

export function AnswerInspector({
  answer: record,
  open = false,
}: {
  answer: AnswerDiagnostic;
  open?: boolean;
}) {
  const answer: AnswerDiagnostic = { ...record, ...(record.diagnostics || {}) };
  const value = answer.answer ?? answer.form_value;
  const text = Array.isArray(value) ? value.join(", ") : pretty(value);
  const quality = answer.quality;
  const format =
    answer.requested_format ??
    answer.format ??
    answer.semantic?.format ??
    answer.semantic?.output;
  const missing = answer.missing_facts ?? answer.missing_fields;
  const validation = answer.validation ?? answer.validation_result;
  const state = answer.status || answer.state || answer.generation_state;
  return (
    <details className="answer-inspector" open={open}>
      <summary>Answer Inspector{state ? ` · ${label(state)}` : ""}</summary>
      <div className="application-profile-field">
        <div className="tags">
          {state && <Status value={state} />}
          {answer.confidence_level && <Tag>{answer.confidence_level}</Tag>}
          {quality?.passed !== undefined && (
            <Tag tone={quality.passed ? "green" : "red"}>
              {quality.passed
                ? "Quality checks passed"
                : "Quality checks failed"}
            </Tag>
          )}
        </div>
        <KeyValues
          data={{
            original_question:
              answer.question || answer.exact_question || "Not recorded",
            question_id: answer.question_id || "Not recorded",
            detected_intent:
              answer.intent || answer.canonical_intent || "Not recorded",
            answer_type: answer.answer_type || answer.kind || "Not recorded",
            requested_format: format || "Not recorded",
            generated_answer:
              text ||
              (state === "GENERATING"
                ? "Generating answer"
                : answer.leave_blank
                  ? "Left blank by policy"
                  : "No supported answer"),
            word_count:
              answer.word_count ?? (text ? text.trim().split(/\s+/).length : 0),
            candidate_facts_used: answer.facts_used || answer.fact_ids || [],
            llm_provider: answer.provider || "Not used / not recorded",
            llm_model: answer.model || "Not used / not recorded",
            validation_result:
              validation ??
              (quality?.passed === undefined
                ? "Not recorded"
                : quality.passed
                  ? "Passed"
                  : "Failed"),
            reason: answer.reasoning_summary || answer.reason || "Not recorded",
            ...(answer.selected_roles?.length
              ? { selected_roles: answer.selected_roles }
              : {}),
            ...(missing ? { missing_factual_information: missing } : {}),
            ...(answer.root_cause
              ? { failure_category: answer.root_cause }
              : {}),
          }}
        />
        {(answer.company_context ||
          answer.company_context_used ||
          answer.company_research) && (
          <JsonDetails
            title="Company context used"
            data={
              answer.company_context ||
              answer.company_context_used ||
              answer.company_research
            }
          />
        )}
        {(answer.company_claims?.length || 0) > 0 && (
          <JsonDetails
            title="Employer claims and source citations"
            data={answer.company_claims}
          />
        )}
        <JsonDetails
          title="Supporting evidence and derivation"
          data={{
            evidence: answer.evidence,
            facts_used: answer.facts_used || answer.fact_ids,
            claims: answer.claims,
            rules_used: answer.rules_used,
            semantic: answer.semantic,
          }}
        />
        {quality && (
          <JsonDetails
            title="Answer quality checks"
            data={quality}
            open={!quality.passed}
          />
        )}
        {(answer.answer_hash || answer.context_hash) && (
          <JsonDetails
            title="Answer identity and cache context"
            data={{
              answer_hash: answer.answer_hash,
              context_hash: answer.context_hash,
            }}
          />
        )}
      </div>
    </details>
  );
}

export function ApplicationAnswerInspector({
  application,
}: {
  application: Data;
}) {
  const checkpoint = application.runs?.at(-1)?.checkpoint || {};
  const recordedDiagnostics =
    application.answer_diagnostics ||
    checkpoint.answer_diagnostics ||
    checkpoint.diagnostics ||
    [];
  const diagnostics: AnswerDiagnostic[] = Array.isArray(recordedDiagnostics)
    ? recordedDiagnostics
    : [];
  const recordedProgress =
    application.answer_progress || checkpoint.answer_progress || [];
  const progress: AnswerDiagnostic[] = Array.isArray(recordedProgress)
    ? recordedProgress
    : [];
  const pending: Data[] = Array.isArray(checkpoint.questions)
    ? checkpoint.questions
    : [];
  const rows = [
    ...diagnostics,
    ...progress.filter(
      (p) =>
        !diagnostics.some(
          (d) => d.question_id === p.question_id && d.question_id,
        ),
    ),
  ];
  if (!rows.length && !pending.length) return null;
  return (
    <div className="answer-list" aria-live="polite">
      {rows.map((row, index) => (
        <div className="answer-card" key={row.question_id || index}>
          <h4>{row.question || row.exact_question}</h4>
          <AnswerInspector
            answer={row}
            open={
              !!(row.status || row.state || row.generation_state) &&
              !["COMPLETED", "ANSWER_VALIDATED", "ANSWER_ACCEPTED_BY_FORM"].includes(
                String(row.status || row.state || row.generation_state).toUpperCase(),
              )
            }
          />
        </div>
      ))}
      {pending
        .filter((q) => !rows.some((row) => row.question === q.question))
        .map((row, index) => (
          <div className="answer-card" key={`pending-${index}`}>
            <h4>{row.question}</h4>
            <AnswerInspector answer={row} open />
          </div>
        ))}
    </div>
  );
}
