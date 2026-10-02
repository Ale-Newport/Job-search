entries => {
  // A closed shadow root keeps the observer's labels/text unchanged. No token,
  // callback or profile data is exposed: these badges only explain filled drafts.
  const state = window.__meridianObservedControlsV1;
  if (!state) return;
  document.querySelectorAll('[data-meridian-inference-badge]').forEach(n => n.remove());
  for (const item of entries) {
    if (item.document_id !== state.documentId) continue;
    const node = state.nodes.get(item.node_id);
    if (!node?.isConnected) continue;
    const badge = document.createElement('span');
    badge.dataset.meridianInferenceBadge = 'true';
    badge.style.cssText = 'display:block;position:relative;z-index:1;margin:6px 0;';
    const root = badge.attachShadow({mode:'closed'});
    const note = document.createElement('span');
    note.style.cssText = 'font:12px/1.5 system-ui;color:#684900;background:#fff2cc;border:1px solid #d4a537;border-radius:6px;padding:4px 8px;display:inline-block;white-space:normal;';
    note.textContent = 'Meridian · Inferred — review: ' + item.reason;
    root.append(note);
    // Place after a containing label to avoid changing the control's label.
    (node.closest('label') || node).after(badge);
  }
}
