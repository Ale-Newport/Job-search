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
    const labelText = labelNode => {
      const copy = labelNode.cloneNode(true);
      copy.querySelectorAll('input,select,textarea,button').forEach(control => control.remove());
      return copy.textContent.trim();
    };
    const refs = (node.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => node.getRootNode().getElementById?.(id)?.textContent || '').join(' ').trim();
    return (refs || node.getAttribute('aria-label') ||
      [...(node.labels || [])].map(labelText).join(' ') ||
      (['option','treeitem'].includes(node.getAttribute('role')) ? node.innerText : '') ||
      (['submit', 'button'].includes(node.type) ? node.value : '') ||
      (['BUTTON', 'A', 'SUMMARY', 'OPTION'].includes(node.tagName) ? node.innerText : '') ||
      questionContainerText(node) || node.getAttribute('placeholder') || node.getAttribute('title') ||
      node.getAttribute('name') || node.id || node.innerText || '').trim().slice(0, 500);
  };
  const fieldFor = node => node.closest('[data-field-path],.ashby-application-form-field-entry');
  const questionFor = node => fieldFor(node)?.querySelector('.ashby-application-form-question-title');
  const questionContainerText = node => {
    const container = node.closest('.application-question') || node.closest('.application-field')?.parentElement;
    if (!container || container.tagName === 'FORM') return '';
    const copy = container.cloneNode(true);
    copy.querySelectorAll('input,textarea,select,button,script,.select2-container').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 1000);
  };
  const required = node => !!node.required || node.getAttribute('aria-required') === 'true' ||
    /(?:^|\s)_required_/.test(questionFor(node)?.className || '');
  const uploadProxy = node => {
    if (node.type !== 'file') return null;
    const ashby = fieldFor(node)?.querySelector('.ashby-application-form-input-file-dropzone-upload');
    const attachment = node.matches('.visually-hidden') && node.parentElement?.querySelector(':scope > button[type="button"]');
    return ashby || attachment || (!visible(node) ? node.closest('a,button,label') : null);
  };
  const fileQuestion = node => {
    if (node.type !== 'file') return '';
    const identityHint = `${node.name || ''} ${node.id || ''}`.replace(/[_-]/g, ' ');
    return /resume|\bcv\b|cover letter|transcript/i.test(identityHint) ? identityHint.trim() : '';
  };
  const comboProxy = node => node.getAttribute('role') === 'combobox' && !visible(node) ? node.closest('.select__control') : null;
  const selectedText = node => {
    const selection = node.closest('.select__control')?.querySelector('.select__single-value');
    if (!selection) return '';
    const flag = selection.querySelector('.iti__flag');
    const code = [...(flag?.classList || [])].map(c => /^iti__([a-z]{2})$/.exec(c)?.[1]).find(Boolean);
    // The phone menu shows only the flag and dial code once selected.
    return code ? `${new Intl.DisplayNames(['en'], {type:'region'}).of(code.toUpperCase())} ${selection.textContent}` : selection.textContent;
  };
  const selector = 'input,textarea,select,button,a[href],summary,[contenteditable="true"],' +
    '[role="button"],[role="checkbox"],[role="radio"],[role="combobox"],' +
    '[role="textbox"],[role="option"],[role="treeitem"],[role="switch"]';
  const roots = [document];
  const all = [];
  while (roots.length) {
    const root = roots.shift();
    all.push(...root.querySelectorAll(selector));
    for (const node of root.querySelectorAll('*')) if (node.shadowRoot) roots.push(node.shadowRoot);
  }
  const elements = [];
  const sectionFor = node => {
    const container = node.closest('fieldset,section,[role="group"],[role="region"]');
    const heading = container?.querySelector('legend,h1,h2,h3,h4,[role="heading"]');
    return {section_id: container ? `section-${identity(container)}` : 'page',
      section_label: (heading?.innerText || container?.getAttribute('aria-label') || 'Application details').slice(0, 160)};
  };
  for (const node of all) {
    // Ashby's optional resume parser is separate from the actual resume field.
    if (node.closest('.ashby-application-form-autofill-input-root')) continue;
    const proxy = uploadProxy(node) || comboProxy(node);
    if (['hidden', 'password'].includes(node.type) || !(proxy ? visible(proxy) : visible(node))) continue;
    if (node.type === 'checkbox' && node.closest('.ashby-application-form-input-yesno')) continue;
    const tag = node.tagName.toLowerCase();
    const type = node.type || '';
    let role = node.getAttribute('role') || ({button:'button', a:'link', textarea:'textbox',
      select:'combobox', summary:'button'}[tag]) ||
      (['checkbox','radio'].includes(type) ? type : type === 'file' ? 'upload' :
        ['submit','button'].includes(type) ? 'button' : 'textbox');
    const yesno = node.matches('button[data-option][aria-pressed]') && node.closest('.ashby-application-form-input-yesno');
    if (yesno) role = 'radio';
    if (role === 'treeitem' && node.closest('.select2-results')) role = 'option';
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
    const question = questionFor(node)?.innerText?.trim();
    elements.push({node_id: identity(node), role, tag, type, explicit_type: node.getAttribute('type'),
      label: role === 'combobox' || role === 'upload' ? question || fileQuestion(node) || questionContainerText(node) || label(node) : label(node), ...sectionFor(node),
      upload_proxy_id: type === 'file' && proxy && visible(proxy) ? identity(proxy) : null,
      interaction_proxy_id: role === 'combobox' && proxy && visible(proxy) ? identity(proxy) : null,
      form_id: node.closest('form') ? identity(node.closest('form')) : 'page',
      max_length: node.maxLength > 0 ? node.maxLength : null, step: node.getAttribute('step'), min: node.getAttribute('min'), max: node.getAttribute('max'),
      field_path: fieldFor(node)?.getAttribute('data-field-path') || '',
      expanded: node.getAttribute('aria-expanded') === 'true',
      value: type === 'file' ? [...node.files || []].map(f => f.name).join(', ') :
        String(yesno ? node.getAttribute('data-option') : node.value ?? (node.isContentEditable ? node.innerText : '')),
      checked: yesno ? node.getAttribute('aria-pressed') === 'true' :
        typeof node.checked === 'boolean' ? node.checked : node.getAttribute('aria-checked') === 'true',
      required: required(node),
      enabled, readonly, operations: enabled ? operations : [],
      name: node.name || '', id: node.id || '', autocomplete: node.autocomplete || '',
      href: tag === 'a' ? node.href : null,
      options: tag === 'select' ? [...node.options].map(o => ({label:o.label, value:o.value,
        disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : [],
      group: role === 'radio' ? fieldFor(node)?.getAttribute('data-field-path') || node.name || node.closest('[role="radiogroup"],fieldset')?.textContent?.slice(0,500) || '' : '',
      selected_text: role === 'combobox' ? node.getAttribute('aria-valuetext') ||
        selectedText(node) ||
        (tag !== 'input' && tag !== 'select' ? node.innerText : '') : '',
      context: question || (role === 'checkbox' && fieldFor(node)?.querySelector('.ashby-application-form-question-description')?.innerText) || node.closest('fieldset')?.querySelector('legend')?.innerText ||
        node.closest('[role="group"],[role="radiogroup"]')?.getAttribute('aria-label') || questionContainerText(node) || '',
      description: (fieldFor(node)?.querySelector('.ashby-application-form-question-description')?.innerText || (node.getAttribute('aria-describedby') || '').split(/\s+/).map(id => document.getElementById(id)?.textContent || '').join(' ')).slice(0, 1600),
      in_viewport: rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth
    });
  }
  const text = (document.body?.innerText || '').slice(0, 16000);
  const challenge = /verify (?:that )?you(?:'re| are) human|human verification|complete the captcha|security check|unusual traffic|additional verification required|verificaci[oó]n adicional requerida/i.test(text)
    || [...document.querySelectorAll('iframe')].some(f => visible(f) && /recaptcha|hcaptcha|challenges.cloudflare/.test(f.src)
      && !(f.closest('.grecaptcha-badge') && /[?&]size=invisible(?:&|$)/.test(f.src)));
  const mfa = /enter (?:the |your )?(?:verification|authentication|security|one.time) code|two.factor authentication|approve (?:the |this )?sign.in/i.test(text);
  const login = [...document.querySelectorAll('input[type="password"]')].some(visible);
  const errors = [...document.querySelectorAll('[role="alert"],.field-error,.error-message,[aria-invalid="true"]')]
    .filter(visible).map(n => n.innerText || label(n)).filter(t => t && !/\bpage is loaded\s*$/i.test(t)).slice(0, 30);
  const unresolved_required = all.filter(node => (node.required || node.getAttribute('aria-required') === 'true') &&
    !node.matches(':disabled') && node.type !== 'hidden' && node.type !== 'password' &&
    !visible(node) && !uploadProxy(node) && !comboProxy(node) &&
    !(node.getAttribute('aria-hidden') === 'true' && node.closest('.select__container,.select-shell')?.querySelector('[role="combobox"]')) &&
    (node.validity ? !node.validity.valid : !node.value))
    .map(node => ({label:label(node),type:node.type || node.getAttribute('role')}));
  return {document_id:cache.documentId,url:location.href,title:document.title,text,elements,
    challenge,mfa,login,errors,unresolved_required,scroll:{y:scrollY,height:document.documentElement.scrollHeight,viewport:innerHeight}};
}
