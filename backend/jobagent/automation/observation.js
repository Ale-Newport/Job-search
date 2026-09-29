() => {
  // Fixed application-owned code. Model output never becomes JavaScript or a selector.
  const key = '__meridianObservedControlsV1';
  if (!window[key]) window[key] = {
    documentId: crypto.randomUUID?.() || `${performance.timeOrigin}-${Math.random()}`,
    ids: new WeakMap(), nodes: new Map(), next: 1
  };
  const cache = window[key];
  const identity = node => {
    if (!cache.ids.has(node)) cache.ids.set(node, cache.next++);
    const id = cache.ids.get(node);
    cache.nodes.set(id, node);
    return id;
  };
  for (const [id, node] of cache.nodes) if (!node.isConnected) cache.nodes.delete(id);
  const visible = node => {
    if (node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const label = node => {
    const refs = (node.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => node.getRootNode().getElementById?.(id)?.textContent || '').join(' ').trim();
    return (refs || node.getAttribute('aria-label') ||
      [...(node.labels || [])].map(n => n.innerText).join(' ') ||
      (['submit', 'button'].includes(node.type) ? node.value : '') ||
      (['BUTTON', 'A', 'SUMMARY', 'OPTION'].includes(node.tagName) ? node.innerText : '') ||
      node.getAttribute('placeholder') || node.getAttribute('title') ||
      node.getAttribute('name') || node.id || node.innerText || '').trim().slice(0, 500);
  };
  const selector = 'input,textarea,select,button,a[href],summary,[contenteditable="true"],' +
    '[role="button"],[role="checkbox"],[role="radio"],[role="combobox"],' +
    '[role="textbox"],[role="option"],[role="switch"]';
  const roots = [document];
  const all = [];
  while (roots.length) {
    const root = roots.shift();
    all.push(...root.querySelectorAll(selector));
    for (const node of root.querySelectorAll('*')) if (node.shadowRoot) roots.push(node.shadowRoot);
  }
  const elements = [];
  for (const node of all) {
    if (['hidden', 'password'].includes(node.type) || !visible(node)) continue;
    const tag = node.tagName.toLowerCase();
    const type = node.type || '';
    let role = node.getAttribute('role') || ({button:'button', a:'link', textarea:'textbox',
      select:'combobox', summary:'button'}[tag]) ||
      (['checkbox','radio'].includes(type) ? type : type === 'file' ? 'upload' :
        ['submit','button'].includes(type) ? 'button' : 'textbox');
    const enabled = !node.matches(':disabled') && !node.closest('[aria-disabled="true"]');
    const readonly = !!node.readOnly || node.getAttribute('aria-readonly') === 'true';
    let operations;
    if (type === 'file') operations = ['UPLOAD'];
    else if (['checkbox','switch'].includes(role)) operations = ['CHECK','UNCHECK'];
    else if (role === 'radio') operations = ['CHECK'];
    else if (tag === 'select') operations = ['SELECT'];
    else if ((tag === 'input' || tag === 'textarea' || node.isContentEditable) &&
      !['submit','button','reset','image'].includes(type)) operations = readonly ? [] : ['TYPE_TEXT'];
    else operations = ['CLICK'];
    if (tag === 'a') operations.push('OPEN_TAB');
    if (role === 'combobox' && !operations.includes('CLICK')) operations.push('CLICK');
    const rect = node.getBoundingClientRect();
    elements.push({node_id: identity(node), role, tag, type, label: label(node),
      value: type === 'file' ? [...node.files || []].map(f => f.name).join(', ') :
        String(node.value ?? (node.isContentEditable ? node.innerText : '')),
      checked: typeof node.checked === 'boolean' ? node.checked : node.getAttribute('aria-checked') === 'true',
      required: !!node.required || node.getAttribute('aria-required') === 'true',
      enabled, readonly, operations: enabled ? operations : [],
      name: node.name || '', id: node.id || '', autocomplete: node.autocomplete || '',
      href: tag === 'a' ? node.href : null,
      options: tag === 'select' ? [...node.options].map(o => ({label:o.label, value:o.value,
        disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : [],
      group: role === 'radio' ? node.name || node.closest('[role="radiogroup"],fieldset')?.textContent?.slice(0,500) || '' : '',
      selected_text: role === 'combobox' ? node.getAttribute('aria-valuetext') ||
        (tag !== 'input' && tag !== 'select' ? node.innerText : '') : '',
      context: node.closest('fieldset')?.querySelector('legend')?.innerText ||
        node.closest('[role="group"],[role="radiogroup"]')?.getAttribute('aria-label') || '',
      in_viewport: rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth
    });
  }
  const text = (document.body?.innerText || '').slice(0, 16000);
  const challenge = /verify (?:that )?you(?:'re| are) human|human verification|complete the captcha|security check|unusual traffic/i.test(text)
    || [...document.querySelectorAll('iframe')].some(f => visible(f) && /recaptcha|hcaptcha|challenges.cloudflare/.test(f.src));
  const mfa = /enter (?:the |your )?(?:verification|authentication|security|one.time) code|two.factor authentication|approve (?:the |this )?sign.in/i.test(text);
  const login = [...document.querySelectorAll('input[type="password"]')].some(visible);
  const errors = [...document.querySelectorAll('[role="alert"],.field-error,.error-message,[aria-invalid="true"]')]
    .filter(visible).map(n => n.innerText || label(n)).filter(Boolean).slice(0, 30);
  const unresolved_required = all.filter(node => (node.required || node.getAttribute('aria-required') === 'true') &&
    !node.matches(':disabled') && node.type !== 'hidden' && node.type !== 'password' &&
    !visible(node) && (node.validity ? !node.validity.valid : !node.value))
    .map(node => ({label:label(node),type:node.type || node.getAttribute('role')}));
  return {document_id:cache.documentId,url:location.href,title:document.title,text,elements,
    challenge,mfa,login,errors,unresolved_required,scroll:{y:scrollY,height:document.documentElement.scrollHeight,viewport:innerHeight}};
}
