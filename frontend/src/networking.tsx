import { useState } from "react";
import type { AppContext, Data } from "./types";
import { api, items, label, openExternal, useResource, date } from "./api";
import { Button, Empty, Modal, Panel, Resource, SectionTitle, Tag } from "./components";

export function Networking({ ctx }: { ctx: AppContext }) {
  const [job, setJob] = useState(ctx.target || ""), [add, setAdd] = useState(false),
    [selected, setSelected] = useState<Data | null>(null), [draft, setDraft] = useState<Data | null>(null),
    [body, setBody] = useState(""), [kind, setKind] = useState("recruiter"),
    [conversation, setConversation] = useState(""), [followup, setFollowup] = useState("");
  const contacts = useResource(`/networking/contacts${job ? `?job_id=${job}` : ""}`, ctx.refresh),
    jobs = useResource("/jobs?limit=200", ctx.refresh);
  const loadDraft = (value: Data) => { setDraft(value); setBody(value.body); };
  return <>
    <SectionTitle eyebrow="PROFESSIONAL CONNECTIONS" title="Networking"
      description="Save verified hiring contacts, prepare grounded messages and track your conversations."
      actions={<Button onClick={() => setAdd(true)}>Add contact</Button>} />
    <label className="field">Related opportunity<select value={job} onChange={e => setJob(e.target.value)}>
      <option value="">All contacts</option>{items(jobs.data).map(j => <option key={j.id} value={j.id}>{j.company} · {j.title}</option>)}
    </select></label>
    <Panel title="Discover contacts" description="Use public company recruiting pages or profiles you select. Verify the current role and evidence before preparing outreach.">
      <Button secondary onClick={() => openExternal(contacts.data?.search_url || "https://www.linkedin.com/search/results/people/")}>Research relevant people on LinkedIn</Button>
      <p>Messages are sent through your manual LinkedIn handoff. Confirm sending afterward to record the outreach.</p>
    </Panel>
    <Resource {...contacts} retry={contacts.reload}>
      {items(contacts.data).length ? <div className="table-scroll"><table><thead><tr>
        <th>Person</th><th>Company / role</th><th>Relevance</th><th>Contact status</th><th>Action</th>
      </tr></thead><tbody>{items(contacts.data).map(c => <tr key={c.id}>
        <td>{c.full_name}<small>Verified {date(c.last_verified)}</small></td><td>{c.company}<small>{c.role}</small></td>
        <td>{c.relevance}<small>{c.reasons?.join(" · ")}</small></td><td><Tag>{label(c.status)}</Tag></td>
        <td><Button secondary onClick={() => { setSelected(c); setDraft(null); setBody(""); }}>Open contact</Button></td>
      </tr>)}</tbody></table></div> : <Empty title="Your professional network" description="Add a profile you have verified or research recruiters for a selected opportunity." />}
    </Resource>
    {add && <AddContact ctx={ctx} job={job} close={() => setAdd(false)} />}
    {selected && <Modal title={selected.full_name} onClose={() => setSelected(null)}>
      <p>{selected.role} · {selected.company}</p><p>{selected.reasons?.join(" · ")}</p>
      <Button secondary onClick={() => openExternal(selected.profile_url)}>Open LinkedIn profile</Button>
      <label className="field">Message strategy<select value={kind} onChange={e => setKind(e.target.value)}>
        {['recruiter','hiring_manager','alumni','application_followup','referral_enquiry','interview_followup'].map(k => <option key={k} value={k}>{label(k)}</option>)}
      </select></label>
      <Button disabled={!job} onClick={async () => { const value = await ctx.act("contact-draft", () => api(`/networking/contacts/${selected.id}/draft`, "POST", { job_id: job, kind })); if (value) loadDraft(value); }}>Generate message for selected role</Button>
      {(selected.messages || []).map((m: Data) => <p key={m.id}><Button secondary onClick={() => loadDraft(m)}>{label(m.kind)} · {label(m.state)}</Button></p>)}
      {draft && <>
        <label className="field">Review message<textarea rows={7} maxLength={4000} value={body} onChange={e => setBody(e.target.value)} disabled={draft.state === "CONTACTED"} /></label>
        <div className="panel-actions">
          <Button disabled={draft.state === "CONTACTED"} onClick={() => ctx.act("contact-handoff", async () => {
            await api(`/networking/messages/${draft.id}`, "PATCH", { body });
            const result = await api(`/networking/messages/${draft.id}/handoff`, "POST", { approved: true });
            await navigator.clipboard.writeText(result.body);
            await openExternal(result.url);
          }, "Reviewed message copied. Complete the send on LinkedIn, then confirm below.")}>Approve & continue to LinkedIn</Button>
          <Button secondary disabled={draft.state === "CONTACTED"} onClick={async () => {
            const result = await ctx.act("contact-confirm", () => api(`/networking/messages/${draft.id}/confirm`, "POST", { sent_confirmed: true, evidence: "User confirmed sending this reviewed message on LinkedIn" }), "Outreach recorded.");
            if (result) setDraft({ ...draft, state: "CONTACTED" });
          }}>I sent this message</Button>
        </div>
      </>}
      <label className="field">Import a received conversation<textarea rows={4} value={conversation} onChange={e => setConversation(e.target.value)} /></label>
      <Button secondary disabled={!conversation.trim()} onClick={() => ctx.act("contact-import", () => api(`/networking/contacts/${selected.id}/conversation`, "POST", { text: conversation, received_at: new Date().toISOString() }), "Conversation imported for recruitment classification.")}>Import received text</Button>
      <label className="field">Follow-up date<input type="date" value={followup} onChange={e => setFollowup(e.target.value)} /></label>
      <Button secondary disabled={!followup} onClick={() => ctx.act("contact-followup", () => api(`/networking/contacts/${selected.id}/followups`, "POST", { due_at: new Date(`${followup}T09:00:00`).toISOString(), notes: "Review this conversation before following up" }), "Follow-up saved.")}>Add follow-up</Button>
      {(selected.followups || []).map((f: Data) => <p key={f.id}>{date(f.due_at)} · {f.notes}</p>)}
    </Modal>}
  </>;
}

function AddContact({ ctx, job, close }: { ctx: AppContext; job: string; close: () => void }) {
  const [values, setValues] = useState({ full_name: "", company: "", role: "", profile_url: "", source: "", notes: "", verified: false, recruiting: "" });
  return <Modal title="Save professional contact" onClose={close}><form onSubmit={async e => {
    e.preventDefault(); const result = await ctx.act("contact-save", () => api("/networking/contacts", "POST", { ...values, job_id: job || null, evidence: values.recruiting ? { recruiting_responsibility: values.recruiting } : {} }), "Contact saved."); if (result) close();
  }}>
    {(['full_name','company','role','profile_url','source','notes','recruiting'] as const).map(key => <label className="field" key={key}>{key === 'recruiting' ? 'Evidence of recruiting responsibility' : label(key)}<input required={!['notes','recruiting'].includes(key)} value={values[key]} onChange={e => setValues({ ...values, [key]: e.target.value })} /></label>)}
    <label><input type="checkbox" checked={values.verified} onChange={e => setValues({ ...values, verified: e.target.checked })} /> I verified the current role and source</label>
    <div className="modal-actions"><Button type="submit">Save contact</Button></div>
  </form></Modal>;
}
