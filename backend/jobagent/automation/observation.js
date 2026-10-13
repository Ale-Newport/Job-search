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
      copy.querySelectorAll('input,select,textarea,button,svg').forEach(control => control.remove());
      return copy.textContent.trim();
    };
    const parentGroup = node.closest('[role="radiogroup"],[role="group"],fieldset');
    const groupRefs = ['radio','checkbox'].includes(node.getAttribute('role'))
      ? (parentGroup?.getAttribute('aria-labelledby') || '').split(/\s+/) : [];
    const refs = (node.getAttribute('aria-labelledby') || '').split(/\s+/).filter(id => !groupRefs.includes(id))
      .map(id => { const target = node.getRootNode().getElementById?.(id); return target ? labelText(target) : ''; }).join(' ').trim();
    return (refs || node.getAttribute('aria-label') ||
      [...(node.labels || [])].filter(n => !['radio','checkbox'].includes(node.type) ||
        !(node.labels.length > 1 && n.parentElement === node.closest('.form-group'))).map(labelText).join(' ') ||
      (['option','treeitem'].includes(node.getAttribute('role')) ? node.innerText : '') ||
      (['submit', 'button'].includes(node.type) ? node.value : '') ||
      (['BUTTON', 'A', 'SUMMARY', 'OPTION'].includes(node.tagName) ? node.innerText : '') ||
      questionContainerText(node) || node.getAttribute('placeholder') || node.getAttribute('title') ||
      node.getAttribute('name') || node.id || node.innerText || '').trim();
  };
  const phoneBacking = node => {
    if (node.type !== 'tel') return null;
    for (let parent = node.parentElement, depth = 0; parent && depth < 5 && !parent.matches('form,body,main'); parent = parent.parentElement, depth++) {
      if (parent.querySelectorAll('input[type="tel"]').length !== 1) break;
      const backing = parent.querySelector('input[type="hidden"][name*="phone"][name*="country"]');
      if (backing) return backing;
    }
    return null;
  };
  const phoneCountryCode = node => {
    if (node.type !== 'tel') return null;
    let parent = node.parentElement;
    for (let depth = 0; parent && depth < 5 && !parent.matches('form,body,main'); depth++, parent = parent.parentElement) {
      if (parent.querySelectorAll('input[type="tel"]').length !== 1) break;
      const backing = parent.querySelector('input[type="hidden"][name*="phone"][name*="country"]');
      const selected = backing?.value?.match(/\(\+(\d{1,4})\)/)?.[1];
      if (selected) return selected;
      const codes = [...parent.querySelectorAll('button[aria-label]')].filter(visible)
        .map(n => n.getAttribute('aria-label').match(/(?:currently selected|selected country)[^+]*\(\+(\d{1,4})\)/i)?.[1]).filter(Boolean);
      if (codes.length === 1) return codes[0];
    }
    return null;
  };
  const fieldFor = node => node.closest('[data-field-path],.ashby-application-form-field-entry');
  const questionFor = node => fieldFor(node)?.querySelector('.ashby-application-form-question-title') ||
    node.closest('.application-question')?.querySelector('.application-label .text');
  const questionText = node => {
    const heading = questionFor(node);
    if (!heading) return '';
    const copy = heading.cloneNode(true);
    copy.querySelectorAll('.required,[aria-hidden="true"]').forEach(n => n.remove());
    return copy.textContent.trim();
  };
  const plainCopy = node => {
    if (!node || node.matches('[role="alert"],.field-error,.error-message,[data-applicant-answer],[data-candidate-answer]')) return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('input,textarea,select,button,script,style,svg,[contenteditable="true"],' +
      '[role="combobox"],[role="listbox"],[role="option"],.select2-container,.select__container,' +
      '[role="alert"],.field-error,.error-message,[data-applicant-answer],[data-candidate-answer]').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };
  const groupQuestion = node => {
    const group = node.closest('[role="radiogroup"],[role="group"],fieldset');
    const refs = (group?.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => plainCopy(node.getRootNode().getElementById?.(id))).filter(Boolean).join(' ');
    if (refs) return refs;
    const field = node.closest('.form-group,.form-field,.field');
    const captions = field ? [...field.querySelectorAll(':scope > label')].filter(n => visible(n)) : [];
    return captions.length === 1 ? plainCopy(captions[0]) : '';
  };
  const meaningfulName = node => (node.name || node.id || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]/g, ' ');
  const descriptionFor = node => {
    const root = node.getRootNode();
    const described = (node.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => plainCopy(root.getElementById?.(id))).filter(Boolean);
    const field = fieldFor(node) || node.closest('.application-question,.application-field,.form-field,.field,.form-group');
    const hints = field ? [...field.querySelectorAll('.ashby-application-form-question-description,' +
      '.application-question-description,.field-description,.description,.help-text,.hint,.form-notes,[data-description]')]
      .filter(hint => !hint.closest('[hidden],[aria-hidden="true"]')).map(plainCopy) : [];
    return [...new Set([...hints, ...described])].join(' ').slice(0, 20000);
  };
  const questionContainerText = node => {
    const container = node.closest('.application-question') || node.closest('.application-field')?.parentElement;
    if (!container || container.tagName === 'FORM') return groupQuestion(node);
    const copy = container.cloneNode(true);
    copy.querySelectorAll('input,textarea,select,button,script,.select2-container').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 20000);
  };
  const required = node => !!node.required || node.getAttribute('aria-required') === 'true' ||
    /(?:^|\s)_required_/.test(questionFor(node)?.className || '');
  const uploadProxy = node => {
    if (node.type !== 'file') return null;
    const ashby = fieldFor(node)?.querySelector('.ashby-application-form-input-file-dropzone-upload');
    const attachment = node.matches('.visually-hidden') && node.parentElement?.querySelector(':scope > button[type="button"]');
    const associated = [...(node.labels || [])].reverse().find(n => visible(n) && n.control === node);
    return ashby || attachment || associated || (!visible(node) ? node.closest('a,button,label') : null);
  };
  const fileQuestion = node => {
    if (node.type !== 'file') return '';
    const identityHint = `${node.name || ''} ${node.id || ''}`.replace(/[_-]/g, ' ');
    return /resume|\bcv\b|cover letter|transcript/i.test(identityHint) ? identityHint.trim() : '';
  };
  const comboProxy = node => node.getAttribute('role') === 'combobox' && !visible(node) ? node.closest('.select__control') : null;
  const dropdownTrigger = node => {
    if (!node.matches('input[role="combobox"][aria-haspopup="listbox"]')) return null;
    // A dedicated two-control widget can expose a separate icon toggle. A
    // nearby form button is not enough evidence to click it.
    const parent = node.parentElement;
    if (!parent || parent.children.length !== 2 ||
      parent.querySelectorAll(':scope > input[role="combobox"]').length !== 1) return null;
    const button = [...parent.children].find(child => child.tagName === 'BUTTON');
    if (!button || button.matches(':disabled,[aria-disabled="true"]') || !visible(button) ||
      button.textContent.trim() || !button.querySelector('svg') ||
      (button.form && button.getAttribute('type') !== 'button') ||
      (button.getAttribute('type') && button.getAttribute('type') !== 'button')) return null;
    const name = `${button.getAttribute('aria-label') || ''} ${button.getAttribute('title') || ''}`.trim();
    if (name && !/^(?:open|show|toggle)(?: (?:the )?(?:options|menu|suggestions|dropdown|list))?$/i.test(name)) return null;
    return button;
  };
  const toggleProxy = node => {
    if (node.tagName !== 'INPUT' || !['radio','checkbox'].includes(node.type)) return null;
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    const clipped = rect.width <= 2 || rect.height <= 2 || style.clipPath !== 'none' ||
      (style.clip !== 'auto' && style.clip !== 'rect(auto, auto, auto, auto)');
    if (visible(node) && !clipped) return null;
    // Native association is essential: a nearby text label is not permission
    // to click a different control or a submit action.
    return [...(node.labels || [])].find(candidate => candidate.control === node && visible(candidate)) || null;
  };
  const selectedText = node => {
    const control = node.closest('.select__control');
    const tags = [...(control?.querySelectorAll('.select__multi-value__label') || [])];
    // React Select can render a single factual selection as a removable chip.
    // Search text is separate; several chips cannot prove one scalar answer.
    const selection = control?.querySelector('.select__single-value') || (tags.length === 1 ? tags[0] : null);
    if (!selection) return '';
    const flag = selection.querySelector('.iti__flag');
    const code = [...(flag?.classList || [])].map(c => /^iti__([a-z]{2})$/.exec(c)?.[1]).find(Boolean);
    // The phone menu shows only the flag and dial code once selected.
    return code ? `${new Intl.DisplayNames(['en'], {type:'region'}).of(code.toUpperCase())} ${selection.textContent}` : selection.textContent;
  };
  const visualCheckbox = node => {
    if (!node.matches('.checkbox') || node.matches('input,[role]') || node.querySelector('input')) return null;
    const option = node.parentElement, caption = option?.querySelector(':scope > .label-text');
    const group = node.closest('.form-field'), heading = group?.querySelector(':scope > label[for]');
    const controlled = heading?.control;
    const indicator = node.querySelector('.checkmark');
    // Legacy widgets sometimes expose no ARIA or native toggle. Require an
    // explicit labeled backing input and a visible per-option state indicator.
    return caption && controlled?.tagName === 'INPUT' && controlled.type === 'text'
      && controlled.tabIndex === -1 && indicator ? {caption, group, controlled, indicator} : null;
  };
  const backedDropdown = node => {
    if (!['button','combobox'].includes(node.getAttribute('role')) || !node.hasAttribute('aria-expanded')) return null;
    const list = node.getRootNode().getElementById?.(node.getAttribute('aria-controls'));
    const backing = node.querySelector('input[type="hidden"][name]');
    return backing && list?.querySelector('[data-dropdown-selection]')
      ? {backing, list, root:node.parentElement} : null;
  };
  const selector = 'input,textarea,select,button,a,summary,[contenteditable="true"],' +
    '[role="button"],[role="checkbox"],[role="radio"],[role="combobox"],' +
    '[role="textbox"],[role="option"],[role="treeitem"],[role="switch"],.checkbox,[data-dropdown-selection]';
  const roots = [document];
  const all = [];
  while (roots.length) {
    const root = roots.shift();
    all.push(...root.querySelectorAll(selector));
    for (const node of root.querySelectorAll('*')) if (node.shadowRoot) roots.push(node.shadowRoot);
  }
  const elements = [];
  const sectionFor = node => {
    const container = node.closest('fieldset,section,[role="group"],[role="region"],.ashby-application-form-section');
    let heading = container?.querySelector('legend,h1,h2,h3,h4,[role="heading"]');
    // Many accessible forms use sibling headings rather than section wrappers.
    // Walk only preceding siblings inside the same form, never another page.
    if (!heading && node.closest('form')) {
      for (let current = node; current && current !== node.closest('form') && !heading; current = current.parentElement) {
        for (let sibling = current.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
          if (sibling.matches('h1,h2,h3,h4,h5,h6,[role="heading"]') && visible(sibling)) { heading = sibling; break; }
        }
      }
    }
    return {section_id: container || heading ? `section-${identity(container || heading)}` : 'page',
      section_label: (heading?.innerText || container?.getAttribute('aria-label') || 'Application details').slice(0, 160)};
  };
  for (const node of all) {
    // Ashby's optional resume parser is separate from the actual resume field.
    if (node.closest('.ashby-application-form-autofill-input-root')) continue;
    if (node.tagName === 'A' && !node.hasAttribute('href') && !node.hasAttribute('role') &&
      !node.onclick && getComputedStyle(node).cursor !== 'pointer') continue;
    const visual = visualCheckbox(node);
    const backed = backedDropdown(node);
    if (node.matches('.checkbox') && !node.matches('input,[role]') && !visual) continue;
    if (node.tagName === 'INPUT' && node.type === 'text' && node.tabIndex === -1 &&
        [...(node.labels || [])].some(l => [...(l.closest('.form-field')?.querySelectorAll('.checkbox') || [])]
          .some(box => visualCheckbox(box)?.controlled === node))) continue;
    const wrapper = node.closest('[role="radio"],[role="checkbox"]');
    if (['radio','checkbox'].includes(node.type) && wrapper && wrapper !== node && visible(wrapper)
      && wrapper.getAttribute('role') === node.type && wrapper.hasAttribute('aria-checked')
      && wrapper.querySelectorAll(`input[type="${node.type}"]`).length === 1) continue;
    const nativeToggle = node.matches('[role="radio"],[role="checkbox"]')
      ? node.querySelector('input[type="radio"],input[type="checkbox"]') : null;
    const toggle = toggleProxy(node);
    const proxy = uploadProxy(node) || comboProxy(node) || toggle;
    if (['hidden', 'password'].includes(node.type) || !(proxy ? visible(proxy) : visible(node))) continue;
    if (node.type === 'checkbox' && node.closest('.ashby-application-form-input-yesno')) continue;
    const tag = node.tagName.toLowerCase();
    const type = node.type || '';
    let role = node.getAttribute('role') || ({button:'button', a:'link', textarea:'textbox',
      select:'combobox', summary:'button'}[tag]) ||
      (['checkbox','radio'].includes(type) ? type : type === 'file' ? 'upload' :
        ['submit','button'].includes(type) ? 'button' : 'textbox');
    if (visual) role = 'checkbox';
    if (backed) role = 'combobox';
    if (node.hasAttribute('data-dropdown-selection')) role = 'option';
    if (tag === 'a' && !node.hasAttribute('href') && !node.hasAttribute('role')) role = 'button';
    const yesno = node.matches('button[data-option][aria-pressed]') && node.closest('.ashby-application-form-input-yesno');
    if (yesno) role = 'radio';
    if (role === 'treeitem' && node.closest('.select2-results')) role = 'option';
    const enabled = !node.matches(':disabled') && !node.closest('[aria-disabled="true"]');
    const readonly = !!node.readOnly || node.getAttribute('aria-readonly') === 'true';
    const choiceContainer = fieldFor(node) || node.closest('[role="group"],[role="radiogroup"],fieldset');
    const groupedCheckbox = role === 'checkbox' && choiceContainer && all.filter(peer =>
      (peer.type === 'checkbox' || peer.getAttribute('role') === 'checkbox') &&
      !(peer.type === 'checkbox' && peer.closest('[role="checkbox"]') !== null) &&
      (fieldFor(peer) || peer.closest('[role="group"],[role="radiogroup"],fieldset')) === choiceContainer).length > 1;
    let operations;
    if (type === 'file') operations = ['UPLOAD'];
    else if (['checkbox','switch'].includes(role)) operations = ['CHECK','UNCHECK'];
    else if (role === 'radio') operations = ['CHECK'];
    else if (tag === 'select') operations = ['SELECT'];
    else if ((tag === 'input' || tag === 'textarea' || node.isContentEditable) &&
      !['submit','button','reset','image'].includes(type)) operations = readonly ? [] : ['TYPE_TEXT'];
    else operations = ['CLICK'];
    if (tag === 'a' && node.hasAttribute('href')) operations.push('OPEN_TAB');
    if (role === 'combobox' && !operations.includes('CLICK')) operations.push('CLICK');
    const clearProxy = node.closest('.select__control')?.querySelector('.select__clear-indicator');
    const nativeClear = (['input','textarea','select'].includes(tag) &&
      (role !== 'combobox' || tag === 'select') &&
      !['file','submit','button','reset','image','hidden','password'].includes(type) && !readonly);
    if (nativeClear || (role === 'combobox' && !readonly && clearProxy && visible(clearProxy))) operations.push('CLEAR');
    const rect = node.getBoundingClientRect();
    const question = questionText(node);
    const dropdown = dropdownTrigger(node);
    elements.push({node_id: identity(node), role, tag, type, explicit_type: node.getAttribute('type'),
      phone_country_code: phoneCountryCode(node),
      phone_national_only: !!phoneBacking(node),
      auxiliary_control: !!node.closest('search,[role="search"],[class*="search-widget"],[class*="jobsearch"],' +
        '[class*="chat-widget"],[class*="chatbot"],[id*="chatbot"],[data-chat-widget]'),
      label: node.hasAttribute('data-dropdown-selection') ? node.getAttribute('data-dropdown-label') || node.innerText.trim()
        : backed ? [...(backed.backing.labels || [])].map(plainCopy).filter(Boolean).join(' ') || groupQuestion(node) || meaningfulName(backed.backing)
        : visual ? visual.caption.textContent.trim() : role === 'combobox' || role === 'upload' ? question || fileQuestion(node) || questionContainerText(node) || label(node) :
        (role === 'textbox' && /^(?:YYYY|MM|DD|YYYY-MM-DD)$/i.test(node.placeholder || '') && !node.labels?.length
          ? [groupQuestion(node), meaningfulName(node)].filter(Boolean).join(' — ') : label(node)), ...sectionFor(node),
      upload_proxy_id: type === 'file' && proxy && visible(proxy) ? identity(proxy) : null,
      interaction_proxy_id: role === 'combobox' && proxy && visible(proxy) ? identity(proxy) : null,
      dropdown_trigger_id: dropdown ? identity(dropdown) : null,
      dropdown_search_id: (() => {
        const legacy = backed?.root.querySelector('.dropdown-search-input');
        if (legacy && visible(legacy)) return identity(legacy);
        const ids = (node.getAttribute('aria-controls') || node.getAttribute('aria-owns') || '').split(/\s+/);
        for (const id of ids) {
          const list = node.getRootNode().getElementById?.(id), parent = list?.parentElement;
          const search = list?.querySelector('input:not([readonly]):not([type="hidden"]):not([type="password"])') ||
            (parent && !parent.matches('body,main,form') ? parent.querySelector('input[type="search"],input[role="searchbox"]') : null);
          if (search && visible(search)) return identity(search);
        }
        return null;
      })(),
      listbox_ids: (node.getAttribute('aria-controls') || node.getAttribute('aria-owns') || '').split(/\s+/).filter(Boolean),
      listbox_id: node.closest('[role="listbox"],.dropdown-container')?.id || '',
      toggle_proxy_id: toggle ? identity(toggle) : null,
      visual_toggle_proxy_id: visual ? identity(visual.caption) : null,
      clear_proxy_id: role === 'combobox' && clearProxy && visible(clearProxy) ? identity(clearProxy) : null,
      form_id: node.form || node.closest('form') ? identity(node.form || node.closest('form')) : 'page',
      navigation_parent_id: tag === 'button' && node.closest('a[href]') ? identity(node.closest('a[href]')) : null,
      accept: node.getAttribute('accept') || '',
      max_length: node.maxLength > 0 ? node.maxLength : null,
      min_length: node.minLength > 0 ? node.minLength : null, pattern: node.getAttribute('pattern') || '',
      validation_message: node.validationMessage || '', validity: node.validity ? {
        valid: node.validity.valid, value_missing: node.validity.valueMissing,
        type_mismatch: node.validity.typeMismatch, pattern_mismatch: node.validity.patternMismatch,
        too_long: node.validity.tooLong, too_short: node.validity.tooShort,
        range_underflow: node.validity.rangeUnderflow, range_overflow: node.validity.rangeOverflow,
        step_mismatch: node.validity.stepMismatch, custom_error: node.validity.customError
      } : null,
      multiple: !!visual || !!node.multiple || !!groupedCheckbox || (role === 'checkbox' && !!(node.name || nativeToggle?.name) &&
        all.filter(peer => peer.type === 'checkbox' && peer.name === (node.name || nativeToggle?.name)
          && peer.form === (node.form || nativeToggle?.form)).length > 1),
      selected_values: tag === 'select' ? [...node.selectedOptions].map(o => o.value) : [],
      step: node.getAttribute('step'), min: node.getAttribute('min'), max: node.getAttribute('max'),
      field_path: fieldFor(node)?.getAttribute('data-field-path') || '',
      expanded: node.getAttribute('aria-expanded') === 'true',
      value: type === 'file' ? [...node.files || []].map(f => f.name).join(', ') :
        String(visual ? visual.caption.textContent.trim() : yesno ? node.getAttribute('data-option') : node.value ?? nativeToggle?.value ?? (node.isContentEditable ? node.innerText : '')),
      checked: visual ? visible(visual.indicator) : yesno ? node.getAttribute('aria-pressed') === 'true' :
        typeof node.checked === 'boolean' ? node.checked : node.getAttribute('aria-checked') === 'true',
      required: required(node),
      enabled, readonly, operations: enabled ? operations : [],
      name: node.name || backed?.backing.name || nativeToggle?.name || '', id: node.id || '', autocomplete: node.autocomplete || '',
      placeholder: node.getAttribute('placeholder') || '',
      href: tag === 'a' ? node.href : null,
      options: backed ? [...backed.list.querySelectorAll('[data-dropdown-selection]')].map(o => ({label:o.getAttribute('data-dropdown-label') || o.textContent.trim(),value:o.getAttribute('data-dropdown-selection')})) : tag === 'select' ? [...node.options].map(o => ({label:o.label, value:o.value,
        disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : [],
      group: visual ? `visual-${identity(visual.group)}` : ['radio','checkbox'].includes(role) ? fieldFor(node)?.getAttribute('data-field-path') ||
        (groupedCheckbox ? `choice-${identity(choiceContainer)}` : '') || node.name || nativeToggle?.name || node.closest('[role="radiogroup"],fieldset')?.textContent?.slice(0,500) || '' : '',
      selected_text: role === 'combobox' ? backed?.backing.value || node.getAttribute('aria-valuetext') ||
        selectedText(node) ||
        (tag === 'input' && node.readOnly && node.getAttribute('aria-expanded') !== 'true' ? node.value : '') ||
        (tag !== 'input' && tag !== 'select' ? node.innerText : '') : '',
      context: question || (role === 'checkbox' && fieldFor(node)?.querySelector('.ashby-application-form-question-description')?.innerText) || node.closest('fieldset')?.querySelector('legend')?.innerText ||
        node.closest('[role="group"],[role="radiogroup"]')?.getAttribute('aria-label') || questionContainerText(node) || '',
      description: descriptionFor(node),
      in_viewport: rect.bottom > 0 && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth
    });
  }
  const text = (document.body?.innerText || '').slice(0, 16000);
  const challenge = /verify (?:that )?you(?:'re| are) human|human verification|complete the captcha|security check|unusual traffic|additional verification required|verificaci[oó]n adicional requerida|access (?:is )?(?:temporarily )?restricted|acceso est[aá] restringido temporalmente|comportamiento del navegador.{0,160}bloqueo/i.test(text)
    || [...document.querySelectorAll('iframe')].some(f => visible(f) && /recaptcha|hcaptcha|challenges.cloudflare/.test(f.src)
      && !(f.closest('.grecaptcha-badge') && /[?&]size=invisible(?:&|$)/.test(f.src)));
  const mfa = /enter (?:the |your )?(?:verification|authentication|security|one.time) code|two.factor authentication|approve (?:the |this )?sign.in/i.test(text);
  const login = [...document.querySelectorAll('input[type="password"]')].some(visible)
    || (/\bauthentication screen\b/i.test(text)
      && [...document.querySelectorAll('input[type="email"]')].some(visible));
  const errors = [...document.querySelectorAll('[role="alert"],.field-error,.error-message,[aria-invalid="true"]')]
    .filter(visible).map(n => n.innerText || label(n)).filter(t => t && !/\bpage is loaded\s*$/i.test(t)
      && !/^\s*(?:loading(?: screen| application| form)?|please wait)[.\s]*$/i.test(t)).slice(0, 30);
  const unresolved_required = all.filter(node => (node.required || node.getAttribute('aria-required') === 'true') &&
    !node.matches(':disabled') && node.type !== 'hidden' && node.type !== 'password' &&
    !visible(node) && !uploadProxy(node) && !comboProxy(node) && !toggleProxy(node) &&
    !(node.getAttribute('aria-hidden') === 'true' && node.closest('.select__container,.select-shell')?.querySelector('[role="combobox"]')) &&
    (node.validity ? !node.validity.valid : !node.value))
    .map(node => ({label:label(node),type:node.type || node.getAttribute('role')}));
  // Keep employer material separate from applicant answers. Never use the full
  // page text as narrative context: forms can expose names, emails and drafts.
  const employerSections = [];
  const employerText = node => {
    const copy = node.cloneNode(true);
    copy.querySelectorAll('form,input,textarea,select,button,script,style,nav,footer,' +
      '[contenteditable="true"],[role="combobox"],[role="listbox"],.select__container,.select2-container,' +
      '[data-field-path],.ashby-application-form-field-entry,.application-question,.application-field,' +
      '[data-applicant-answer],[data-candidate-answer],[role="alert"],.field-error,.error-message').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 24000);
  };
  const jobSelectors = '[itemprop="description"],.ashby-job-posting-description,.posting-description,' +
    '.section-wrapper.page-full-width,[data-automation-id="jobPostingDescription"],' +
    '#job_description,#job-description,.job-description,#content';
  for (const node of document.querySelectorAll(jobSelectors)) {
    if (!visible(node) || node.closest('form,[data-field-path],.application-question,.application-field')) continue;
    // Broad ATS layout wrappers are useful only on the listing side. An
    // application wrapper is not a job description after its form is removed.
    if (node.matches('#content,.section-wrapper.page-full-width') &&
      node.querySelector('form,input,textarea,select,[contenteditable="true"]')) continue;
    const text = employerText(node);
    if (text.length >= 80 && !employerSections.some(s => s.text.includes(text)))
      employerSections.push({kind:'job_description',text});
  }
  const jobContext = {title:document.querySelector('h1,[itemprop="title"],[data-automation-id="jobPostingHeader"]')?.textContent?.trim() || '',
    sections:employerSections.slice(0, 8)};
  const occurrences = new Map();
  for (const element of elements) {
    const node = cache.nodes.get(element.node_id), form = node.form || node.closest('form');
    const radio = element.role === 'radio';
    const base = JSON.stringify([form?.id || form?.name || [...document.forms].indexOf(form),
      element.section_label, radio ? 'radio' : element.role,
      radio ? element.context || element.group : element.label, node.name || '']);
    const count = occurrences.get(base) || 0;
    element.manual_key = radio ? base : JSON.stringify([base, count]);
    if (!radio) occurrences.set(base, count + 1);
  }
  return {document_id:cache.documentId,url:location.href,title:document.title,text,elements,
    job_context:jobContext,challenge,mfa,login,errors,unresolved_required,scroll:{y:scrollY,height:document.documentElement.scrollHeight,viewport:innerHeight}};
}
