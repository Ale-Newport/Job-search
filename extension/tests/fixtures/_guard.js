// Test instrumentation shared by every fixture. It only OBSERVES: it never changes page behaviour.
//   window.__submits  every click on a submit/next/apply control and every form submit, with isTrusted
//   window.__events   every synthetic (isTrusted === false) input/change/beforeinput/click that reaches the page,
//                     i.e. exactly the writes performed by the extension (a real user generates trusted events)
// The e2e tests assert: nothing the extension did ever targets a password, consent or submit control, and no
// synthetic click ever reaches a submit control.
(() => {
  if (window.__guard) return;
  window.__guard = true;
  window.__submits = [];
  window.__events = [];
  const name = (el) => (el && (el.id || el.name || el.getAttribute?.('aria-label') || el.tagName)) || '';
  const describe = (el) => ({
    name: name(el), tag: el?.tagName || '', type: el?.type || '', id: el?.id || '',
    group: el?.name || '', form: el?.form?.id || '',
  });
  const isProgress = (el) => !!el?.closest?.('[data-submit],[data-next],button[type=submit],input[type=submit]');
  addEventListener('click', (e) => {
    const control = e.target?.closest?.('button,input[type=submit],input[type=button],a[role=button],[role=button]');
    if (control && isProgress(control)) {
      window.__submits.push({ kind: 'click', trusted: e.isTrusted, name: name(control), t: Date.now() });
    }
    if (!e.isTrusted) window.__events.push({ type: 'click', trusted: false, ...describe(e.target) });
  }, true);
  addEventListener('submit', (e) => {
    window.__submits.push({ kind: 'submit', trusted: e.isTrusted, name: name(e.target), t: Date.now() });
  }, true);
  for (const type of ['input', 'change', 'beforeinput', 'paste', 'drop']) {
    addEventListener(type, (e) => {
      if (!e.isTrusted) window.__events.push({ type, trusted: false, ...describe(e.target) });
    }, true);
  }
  // Convenience for the tests: current values by id, never including passwords' content in logs.
  window.__values = () => Object.fromEntries([...document.querySelectorAll('input,select,textarea,[contenteditable=true]')]
    .filter((el) => el.id).map((el) => [el.id, el.type === 'checkbox' || el.type === 'radio' ? el.checked
      : el.isContentEditable ? el.innerText : el.type === 'file' ? [...el.files].map((f) => f.name).join(',') : el.value]));
})();
