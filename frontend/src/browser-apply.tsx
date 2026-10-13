import { useState } from "react";
import { api, desktop, items, useResource } from "./api";
import { Button, ErrorBox, Modal } from "./components";
import type { AppContext } from "./types";

export function BrowserApply({ ctx, onClose }: { ctx: AppContext; onClose: () => void }) {
  const documents = useResource("/documents?limit=500", ctx.refresh);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [document, setDocument] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function readChrome() {
    setBusy(true); setError("");
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const tab = await invoke<{ url: string; title: string }>("current_chrome_tab");
      setUrl(tab.url); setTitle(tab.title.slice(0, 400));
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <Modal title="Auto Apply from Chrome" onClose={onClose}
    description="Start from any job or application URL, including companies outside your watchlist.">
    <form onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setError("");
      try {
        const result = await api("/applications/from-browser", "POST", {
          url, title, company, ...(document ? { document_version_id: document } : {}),
        });
        await ctx.act("refresh", () => Promise.resolve({}));
        onClose(); ctx.select({ kind: "application", id: result.application_id });
      } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
      finally { setBusy(false); }
    }}>
      <div className="form-fields">
        {desktop() && <Button type="button" secondary loading={busy} onClick={readChrome}>Use current Chrome tab</Button>}
        <label className="field"><span>Application URL</span><input type="url" required value={url} maxLength={4000} onChange={(e) => setUrl(e.target.value)} /></label>
        <label className="field"><span>Job title</span><input required value={title} maxLength={400} onChange={(e) => setTitle(e.target.value)} /></label>
        <label className="field"><span>Company</span><input required value={company} maxLength={300} onChange={(e) => setCompany(e.target.value)} /></label>
        <label className="field"><span>Approved CV</span><select value={document} onChange={(e) => setDocument(e.target.value)}>
          <option value="">Choose later</option>
          {items(documents.data).filter((d) => d.latest_version?.approved).map((d) => <option key={d.id} value={d.latest_version_id || d.latest_version.id}>{d.name || d.filename}</option>)}
        </select></label>
        <p>Meridian opens this URL in its application browser. Sign in there if needed; your current Chrome login is not copied. Verified answers and your selected CV are filled, then preparation stops for review before submission.</p>
        {error && <ErrorBox error={error} />}
      </div>
      <div className="modal-footer"><Button secondary type="button" onClick={onClose}>Cancel</Button><Button type="submit" loading={busy}>Fill and review</Button></div>
    </form>
  </Modal>;
}
