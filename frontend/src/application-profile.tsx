import { useState } from "react";
import { Pencil, CheckCircle2, CircleHelp } from "lucide-react";
import { api, useResource } from "./api";
import { Button, Editor, Panel, Resource, Tag } from "./components";
import type { AppContext, Data } from "./types";

export function ApplicationProfile({ ctx }: { ctx: AppContext }) {
  const r = useResource("/application-profile", ctx.refresh);
  const [edit, setEdit] = useState<Data | null>(null);
  const [filter, setFilter] = useState("");
  const [group, setGroup] = useState("Contact");
  const fields: Data[] = r.data?.fields || [];
  const groups = [...new Set(fields.map((f) => String(f.group)))];
  const visible = fields.filter((f) =>
    !filter
      ? f.group === group
      : `${f.label} ${f.value}`.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <Panel
      title="Application profile"
      description="Contact details, preferences and answers beyond your CV. Confirmed answers can fill forms; suggestions remain drafts until you accept them."
    >
      <Resource {...r} retry={r.reload}>
        <div className="application-profile-body">
          <div className="application-profile-intro">
            <div>
              <strong>
                {fields.filter((f) => f.state === "confirmed").length} confirmed
              </strong>
              <span>
                {" "}
                · {fields.filter((f) => f.state === "suggested").length}{" "}
                suggestions ·{" "}
                {fields.filter((f) => f.state === "unknown").length} not
                provided
              </span>
            </div>
            <input
              aria-label="Search application profile"
              placeholder="Find a detail or answer…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </div>
          <div
            className="application-profile-tabs"
            role="tablist"
            aria-label="Application profile sections"
          >
            {groups.map((g) => (
              <button
                type="button"
                role="tab"
                aria-selected={group === g && !filter}
                key={g}
                onClick={() => {
                  setGroup(g);
                  setFilter("");
                }}
              >
                {g}
              </button>
            ))}
          </div>
          <div className="application-profile-grid">
            {visible.map((f) => (
              <article key={f.key} className="application-profile-field">
                <div className="application-profile-label">
                  <h4>{f.label}</h4>
                  <Tag
                    tone={
                      f.state === "confirmed"
                        ? "green"
                        : f.state === "suggested"
                          ? "amber"
                          : "neutral"
                    }
                  >
                    {f.state === "confirmed" ? (
                      <CheckCircle2 size={12} />
                    ) : (
                      <CircleHelp size={12} />
                    )}
                    {f.state === "unknown"
                      ? "Not provided"
                      : f.state === "suggested"
                        ? "Needs confirmation"
                        : "Confirmed"}
                  </Tag>
                </div>
                <p className={f.value ? "" : "muted"}>
                  {f.value ||
                    "Add when you are ready. Meridian will ask in the browser if this answer is needed."}
                </p>
                <small>
                  {f.sensitive ? "Personal disclosure · " : ""}
                  {f.scope ? `${f.scope} only · ` : ""}
                  {f.source}
                </small>
                <Button secondary onClick={() => setEdit(f)}>
                  <Pencil size={13} />
                  {f.value ? "Edit" : "Add answer"}
                </Button>
              </article>
            ))}
          </div>
          <p className="panel-copy">
            New questions appear beside the employer’s form. AI can draft longer
            answers from verified experience; you can edit and confirm them
            there. Optional disclosures can stay blank.
          </p>
          {!!r.data?.answers?.length && (
            <details>
              <summary>
                Answers saved from applications ({r.data.answers.length})
              </summary>
              <div className="simple-list">
                {r.data.answers.map((a: Data) => (
                  <div key={a.id}>
                    <div>
                      <strong>{a.question}</strong>
                      <p>{a.action === "skip" ? "Left blank" : a.answer}</p>
                      <small>
                        {a.reusable
                          ? `Reusable${a.scope ? ` · ${a.scope}` : ""}`
                          : "This application only"}
                      </small>
                    </div>
                    <Button
                      secondary
                      onClick={() =>
                        setEdit({
                          ...a,
                          memory: true,
                          label: a.question,
                          value: a.answer,
                          multiline: true,
                          state: "confirmed",
                        })
                      }
                    >
                      Edit answer
                    </Button>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      </Resource>
      {edit && (
        <Editor
          title={edit.label}
          description={`${edit.sensitive ? "Only enter information you choose to disclose. " : ""}${edit.scope ? `Used for ${edit.scope} only. ` : ""}Confirming allows Meridian to use this answer in matching form fields. You can clear it or save it as a suggestion.`}
          initial={{
            value: edit.value,
            confirmed: edit.state === "confirmed",
            reusable: !!edit.reusable,
          }}
          fields={[
            {
              key: "value",
              label: "Answer",
              type: edit.options?.length
                ? "select"
                : edit.multiline
                  ? "textarea"
                  : edit.type === "number"
                    ? "number"
                    : edit.type === "date"
                      ? "date"
                      : "text",
              options: edit.options?.length
                ? [{ value: "", label: "Not provided" }, ...edit.options]
                : undefined,
            },
            edit.memory
              ? {
                  key: "reusable",
                  label:
                    "Remember for matching questions (country restrictions still apply)",
                  type: "checkbox",
                }
              : {
                  key: "confirmed",
                  label: "Confirmed — allow autofill",
                  type: "checkbox",
                },
          ]}
          onClose={() => setEdit(null)}
          onSave={async (values) => {
            if (edit.memory)
              await api(`/browser-answers/${edit.id}`, "PATCH", {
                answer: String(values.value ?? ""),
                reusable: !!values.reusable,
              });
            else
              await api(`/application-profile/${edit.key}`, "PUT", {
                value: String(values.value ?? ""),
                confirmed: !!values.confirmed,
              });
            await ctx.act(
              "profile-refresh",
              async () => ({}),
              "Application profile saved.",
            );
          }}
        />
      )}
    </Panel>
  );
}
