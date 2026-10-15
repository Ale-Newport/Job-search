options => {
  // Fixed application-owned code. Model output never becomes JavaScript or a selector.
  // `options.widgets` is OFF for the Playwright filler (it calls this without arguments, and its upload/dropdown paths
  // validate only the proxies they know). The browser extension passes {widgets:true} to also recognise controls the
  // filler does not drive yet: file inputs hidden inside a plain dropzone, <button aria-haspopup="listbox"> dropdowns
  // and Yes/No button pairs. Everything else is for every caller; without the option the output differs from the previous
  // version of this script only in two NAMES: a custom radio / grouped checkbox (role=radio|checkbox without an aria-label)
  // is named by its own text, and the question of a radio/checkbox group in a table layout is the cell before it.
  const widgets = !!options && typeof options === 'object' && options.widgets === true;
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
  for (const [id, node] of cache.nodes) {
    // A page-defined `isConnected` getter that throws must not make every later scan of the page fail: such a node is dropped.
    let connected = false;
    try { connected = node.isConnected; } catch (error) { connected = false; }
    if (!connected) cache.nodes.delete(id);
  }
  const visible = node => {
    if (node.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  // Text the person types into a rich-text editor (contenteditable / role=textbox) is an ANSWER, never part of a question: every
  // text read out of a question container, label or description drops editors first.
  const editors = '[contenteditable]:not([contenteditable="false"]),[role="textbox"]';
  const labelText = labelNode => {
    const copy = labelNode.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button,svg,' + editors).forEach(control => control.remove());
    return copy.textContent.trim();
  };
  // The name of a custom radio/checkbox card is its own text ("Hybrid"), not the question of the group it belongs to.
  const ownChoiceText = node => {
    if (node.tagName === 'INPUT') return '';
    const copy = node.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button,svg,[aria-hidden="true"]').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 1000);
  };
  const label = (node, own) => {
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
      (own ? ownChoiceText(node) : '') ||
      questionContainerText(node) || node.getAttribute('placeholder') || node.getAttribute('title') ||
      node.getAttribute('name') || node.id || (node.matches(editors) ? '' : node.innerText) || '').trim();
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
    copy.querySelectorAll('input,textarea,select,button,script,style,svg,' + editors + ',' +
      '[role="combobox"],[role="listbox"],[role="option"],.select2-container,.select__container,' +
      '[role="alert"],.field-error,.error-message,[data-applicant-answer],[data-candidate-answer]').forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim();
  };
  // Table layouts (iCIMS): the question of a radio/checkbox group is the cell before the answer cell, or the row above it.
  const cellQuestion = node => {
    if (!['radio','checkbox'].some(kind => node.type === kind || node.getAttribute('role') === kind) && !(widgets && node.type === 'file')) return '';
    const isCell = el => /^(?:TD|TH)$/.test(el.tagName) || ['cell','gridcell'].includes(el.getAttribute('role')) ||
      getComputedStyle(el).display === 'table-cell';
    const isRow = el => el.tagName === 'TR' || el.getAttribute('role') === 'row' || getComputedStyle(el).display === 'table-row';
    const controls = 'input,select,textarea,button,[role="radio"],[role="checkbox"],[role="combobox"]';
    let cell = null;
    for (let el = node.parentElement, depth = 0; el && depth < 6 && !el.matches('form,body,main,fieldset,[role="radiogroup"],[role="group"]');
      el = el.parentElement, depth++) if (isCell(el)) { cell = el; break; }
    const row = cell?.parentElement;
    if (!cell || !row || !isRow(row)) return '';
    // nearest preceding cell that only holds text; cells with controls are other options (matrix layouts) or other questions
    for (let previous = cell.previousElementSibling; previous; previous = previous.previousElementSibling) {
      if (previous.querySelector(controls)) continue;
      const text = plainCopy(previous);
      if (text) return text.slice(0, 500);
    }
    // one cell per row: the question is the text-only row above
    const above = row.children.length === 1 ? row.previousElementSibling : null;
    return above && isRow(above) && !above.querySelector(controls) ? plainCopy(above).slice(0, 500) : '';
  };
  const groupQuestion = node => {
    const group = node.closest('[role="radiogroup"],[role="group"],fieldset');
    const refs = (group?.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map(id => plainCopy(node.getRootNode().getElementById?.(id))).filter(Boolean).join(' ');
    if (refs) return refs;
    const field = node.closest('.form-group,.form-field,.field');
    const captions = field ? [...field.querySelectorAll(':scope > label')].filter(n => visible(n)) : [];
    return captions.length === 1 ? plainCopy(captions[0]) : cellQuestion(node);
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
    copy.querySelectorAll('input,textarea,select,button,script,.select2-container,' + editors).forEach(n => n.remove());
    return copy.textContent.replace(/\s+/g, ' ').trim().slice(0, 20000);
  };
  const required = node => !!node.required || node.getAttribute('aria-required') === 'true' ||
    /(?:^|\s)_required_/.test(questionFor(node)?.className || '');
  // widgets only: a display:none / visibility:hidden file input inside a plain dropzone (react-dropzone, Dropzone.js, FilePond)
  // has no <a>/<button>/<label> ancestor; the visible drop area that holds nothing but this file input is its proxy.
  const dropzoneProxy = node => {
    if (!widgets || visible(node)) return null;
    const zone = node.closest('[class*="dropzone" i],[class*="drop-zone" i],[class*="drop_zone" i],[class*="drop-area" i],' +
      '[class*="droparea" i],[class*="filepond--root" i],[data-testid*="dropzone" i],[data-dropzone],[role="presentation"]');
    if (!zone || !visible(zone) || zone.closest('form,body,main') === zone) return null;
    const box = zone.getBoundingClientRect();
    if (box.width < 24 || box.height < 24) return null;
    // a wrapper of the whole page or form is not a drop target: it may hold nothing but this one file input
    return zone.querySelectorAll('input:not([type="hidden"]),select,textarea').length === 1 ? zone : null;
  };
  const uploadProxy = node => {
    if (node.type !== 'file') return null;
    const ashby = fieldFor(node)?.querySelector('.ashby-application-form-input-file-dropzone-upload');
    const attachment = node.matches('.visually-hidden') && node.parentElement?.querySelector(':scope > button[type="button"]');
    const associated = [...(node.labels || [])].reverse().find(n => visible(n) && n.control === node);
    return ashby || attachment || associated || (!visible(node) ? node.closest('a,button,label') : null) || dropzoneProxy(node);
  };
  // widgets only: what a person reads as the name of a file input (its <label>, aria-labelledby, aria-label), not the name/id
  // hint below that the Playwright filler's engine reads ("job application[resume] resume"); name and id travel in their own fields.
  const GENERIC_FILE_BUTTON = /^(?:(?:browse|choose|upload|attach|select|add|drop|examinar|seleccionar|adjuntar|subir|elegir|cargar)(?:\s+(?:a|an|your|the|un|el|tu)?\s*(?:files?|documents?|archivos?|documentos?|here|aqu[i\u00ed]))?\s*(?:\.{3}|\u2026)?)?$/i;
  const declaredFileLabel = node => {
    if (!widgets || node.type !== 'file') return '';
    const root = node.getRootNode();
    const named = (node.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => { const target = root.getElementById?.(id); return target && target !== node ? labelText(target) : ''; }).join(' ');
    const text = (named.trim() || node.getAttribute('aria-label') || [...(node.labels || [])].map(labelText).join(' '))
      .replace(/\s+/g, ' ').replace(/\s*[*\u2731]+\s*$/, '').trim();
    // "Browse...", "Choose file": the caption of the button, not the name of the question (iCIMS keeps that in the cell before)
    return GENERIC_FILE_BUTTON.test(text) ? cellQuestion(node) : text;
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
  // widgets only: <button aria-haspopup="listbox"> (Workday, Headless UI Listbox) is a dropdown when it is a labelled control of
  // a form field: never a menu/navigation/toolbar button, never a submit button, and the name is the question, not the value shown.
  const listboxButton = node => {
    if (!widgets || node.tagName !== 'BUTTON' || node.hasAttribute('role') || node.getAttribute('aria-haspopup') !== 'listbox') return null;
    const type = node.getAttribute('type');
    if (type ? type.toLowerCase() !== 'button' : !!node.form) return null;
    if (node.closest('header,footer,nav,aside,search,[role="banner"],[role="contentinfo"],[role="navigation"],' +
      '[role="toolbar"],[role="menubar"],[role="search"]')) return null;
    const root = node.getRootNode();
    // the chevron of a combobox input (tabindex=-1, or sharing its popup) is part of that input, not a field of its own
    const popup = node.getAttribute('aria-controls');
    if (node.getAttribute('tabindex') === '-1' || node.parentElement?.querySelector(':scope > input[role="combobox"],:scope > * > input[role="combobox"]') ||
      (popup && [...root.querySelectorAll('[role="combobox"]')].some(peer => peer !== node && peer.getAttribute('aria-controls') === popup))) return null;
    // a button that names itself ("label button") shows its value in that name: only the OTHER referenced elements count
    const named = (node.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
      .map(id => { const target = root.getElementById?.(id); return target && target !== node ? labelText(target) : ''; })
      .join(' ').replace(/\s+/g, ' ').trim();
    const legend = node.closest('fieldset')?.querySelector(':scope > legend');
    const name = named || (node.getAttribute('aria-label') || '').trim() || [...(node.labels || [])].map(labelText).join(' ').trim() ||
      groupQuestion(node) || (legend ? plainCopy(legend) : '');
    return name ? {label: name.replace(/\s+/g, ' ').trim()} : null;
  };
  // widgets only: two sibling <button aria-pressed> reading exactly Yes/No (or Si/No) in one field are a radio group,
  // like Ashby's .ashby-application-form-input-yesno buttons.
  const pressedPair = node => {
    if (!widgets || node.tagName !== 'BUTTON' || !node.hasAttribute('aria-pressed') || node.getAttribute('role') ||
      node.matches('.ashby-application-form-input-yesno button')) return null;
    const parent = node.parentElement;
    if (!parent || parent.matches('form,body,main,html') ||
      node.closest('header,footer,nav,aside,[role="banner"],[role="contentinfo"],[role="navigation"],[role="toolbar"],[role="menubar"]')) return null;
    const answer = button => (button.innerText || button.textContent || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/\s+/g, ' ').trim().toLowerCase();
    const buttons = [...parent.children].filter(child => child.tagName === 'BUTTON' && child.hasAttribute('aria-pressed') && !child.getAttribute('role'));
    if (!buttons.includes(node)) return null;
    const words = buttons.map(answer).sort().join('|');   // exactly two buttons: Yes and No (or Si and No)
    if (words !== 'no|yes' && words !== 'no|si') return null;
    return {group: `pair-${identity(parent)}`,
      value: node.getAttribute('data-option') || node.getAttribute('data-value') || node.getAttribute('value') || node.innerText.trim()};
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
    // (a loop, not push(...list): a page with more than ~120,000 matching nodes would overflow the argument limit)
    for (const node of root.querySelectorAll(selector)) all.push(node);
    for (const node of root.querySelectorAll('*')) if (node.shadowRoot) roots.push(node.shadowRoot);
  }
  const elements = [];
  // Every control of a section asks for the same heading: look it up once per container (a page of one big <section> with a
  // hundred fields otherwise searches that whole subtree a hundred times).
  const headings = new Map();
  const headingOf = container => {
    if (!headings.has(container)) headings.set(container, container.querySelector('legend,h1,h2,h3,h4,[role="heading"]'));
    return headings.get(container);
  };
  // The heading a control sits under is the NEAREST PRECEDING heading-like block whose section still includes the control, not the
  // first heading of its container: a <section> with "Personal information" and, further down, "Emergency contact" titles the
  // fields after the second one with the second. Heading-like: h1-h6, [role=heading], legend, caption, summary, a table header
  // row, and (only when they stand alone above SEVERAL questions, never as the caption of one) a title/heading/header block or a
  // bold line (<p><strong>..</strong></p>, <div class="section-title">).
  const HEADINGISH = 'legend,h1,h2,h3,h4,h5,h6,[role="heading"],caption,summary,th,strong:not(:empty),b:not(:empty),[class*="title" i],[class*="heading" i],' +
    '[class*="header" i],[class*="legend" i],[class*="caption" i],[class*="bold" i],[style*="font-weight" i]';
  const HEADING_CONTROLS = 'input:not([type="hidden"]),select,textarea,[role="combobox"],[role="radio"],[role="checkbox"],' + editors;
  const NOT_A_HEADING = 'label,button,a,option,nav,[role="navigation"],[role="option"],[role="listbox"],[role="button"],[role="alert"],' +
    '.field-error,.error-message';
  const HEADING_WORDS = new Set(['title', 'heading', 'headline', 'header', 'legend', 'caption', 'subtitle', 'subheading', 'bold', 'semibold']);
  const CAPTION_WORDS = new Set(['label', 'question', 'field', 'input', 'option', 'choice', 'answer', 'hint', 'help', 'description', 'desc',
    'error', 'required', 'placeholder', 'tooltip']);
  const classWords = el => (el.getAttribute('class') || '').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const squash = n => (n.textContent || '').replace(/\s+/g, ' ').trim();
  const headingCache = new Map(), scopeCache = new Map(), fieldCache = new Map(), headingLists = new Map();
  // the first ancestor of a heading block that holds controls: what the heading titles
  const headingScope = el => {
    if (scopeCache.has(el)) return scopeCache.get(el);
    let scope = null;
    if (el.tagName === 'LEGEND' || el.tagName === 'SUMMARY') scope = el.parentElement;
    else if (el.tagName === 'TH' || el.tagName === 'CAPTION') scope = el.closest('table');
    else {
      let own = el;
      for (let depth = 0; depth < 4 && own.parentElement && !own.parentElement.querySelector(HEADING_CONTROLS); depth++) own = own.parentElement;
      scope = own.parentElement;
    }
    scopeCache.set(el, scope);
    return scope;
  };
  // how many distinct questions a scope holds (2 means "many": all a title needs to be above several of them)
  const questionsIn = scope => {
    if (fieldCache.has(scope)) return fieldCache.get(scope);
    const seen = new Set();
    for (const control of scope.querySelectorAll(HEADING_CONTROLS)) {
      seen.add(['radio', 'checkbox'].includes(control.type) && control.name ? `${control.type}:${control.name}` : control);
      if (seen.size > 1) break;
    }
    fieldCache.set(scope, seen.size);
    return seen.size;
  };
  const headingText = el => {
    if (headingCache.has(el)) return headingCache.get(el);
    let text = '';
    try {
      const tag = el.tagName;
      const semantic = /^(?:H[1-6]|LEGEND|CAPTION)$/.test(tag) || el.getAttribute('role') === 'heading';
      const loose = !semantic && tag !== 'SUMMARY' && tag !== 'TH';
      const raw = squash(el);   // cheap rejects first: a page can hold thousands of bold bits and class names that say "header"
      let plausible = raw.length >= 2 && raw.length <= 120 && /\p{L}/u.test(raw);
      if (plausible && loose) {
        const words = classWords(el);
        const boldTag = tag === 'STRONG' || tag === 'B';
        // a bare <strong> inside a sentence is emphasis: only a bold LINE (its block holds nothing else) can be a heading
        plausible = !words.some(w => CAPTION_WORDS.has(w)) &&
          (boldTag ? !!el.parentElement && squash(el.parentElement) === raw : words.some(w => HEADING_WORDS.has(w)) || el.hasAttribute('style'));
      }
      if (plausible && (tag === 'SUMMARY' || !el.closest(NOT_A_HEADING)) && !el.querySelector(HEADING_CONTROLS) && visible(el)) {
        const line = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (line.length < 2 || line.length > 120) text = '';
        else if (!loose) {
          if (tag !== 'TH') text = line;
          else { const row = el.closest('tr'); if (row && !row.querySelector(HEADING_CONTROLS)) text = line; }
        } else {
          const scope = headingScope(el);
          const boldLine = tag === 'STRONG' || tag === 'B' || classWords(el).some(w => w === 'bold' || w === 'semibold') ||
            /^(?:bold|[6-9]00)$/.test(getComputedStyle(el).fontWeight);
          if (scope && questionsIn(scope) > 1 && (classWords(el).some(w => HEADING_WORDS.has(w)) || boldLine)) text = line;
        }
      }
    } catch (error) { text = ''; }
    headingCache.set(el, text);
    return text;
  };
  const nearestHeading = (node, limit) => {
    let list = headingLists.get(limit);   // the candidates inside this section/form only, in document order
    if (!list) { list = [...limit.querySelectorAll(HEADINGISH)]; headingLists.set(limit, list); }
    let lo = 0, hi = list.length;   // the candidates that precede the control form a prefix of the list
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (list[mid].compareDocumentPosition(node) & 4) lo = mid + 1; else hi = mid;
    }
    for (let i = lo - 1, steps = 0; i >= 0 && steps < 40; i--, steps++) {
      const el = list[i];
      if (el === node || el.contains(node) || node.contains(el) || !headingText(el)) continue;
      const scope = headingScope(el);
      if (scope && scope.contains(node)) return el;   // not the heading of a section that closed before the control
    }
    return null;
  };
  const sectionFor = node => {
    const container = node.closest('fieldset,section,[role="group"],[role="region"],.ashby-application-form-section');
    const form = node.closest('form');
    let heading = container || form ? nearestHeading(node, container || form) : null;
    if (!heading && container) heading = headingOf(container);
    // Many accessible forms use sibling headings rather than section wrappers.
    // Walk only preceding siblings inside the same form, never another page.
    if (!heading && form) {
      for (let current = node; current && current !== form && !heading; current = current.parentElement) {
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
    const listbox = listboxButton(node);
    const pair = pressedPair(node);
    if (listbox) role = 'combobox';
    if (pair) role = 'radio';
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
        : visual ? visual.caption.textContent.trim() : listbox ? listbox.label : role === 'combobox' || role === 'upload' ? question || declaredFileLabel(node) || fileQuestion(node) || questionContainerText(node) || label(node) :
        (role === 'textbox' && /^(?:YYYY|MM|DD|YYYY-MM-DD)$/i.test(node.placeholder || '') && !node.labels?.length
          ? [groupQuestion(node), meaningfulName(node)].filter(Boolean).join(' — ') : label(node, role === 'radio' || (role === 'checkbox' && !!groupedCheckbox))), ...sectionFor(node),
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
        String(visual ? visual.caption.textContent.trim() : yesno ? node.getAttribute('data-option') : pair ? pair.value : node.value ?? nativeToggle?.value ?? (node.isContentEditable ? node.innerText : '')),
      checked: visual ? visible(visual.indicator) : yesno || pair ? node.getAttribute('aria-pressed') === 'true' :
        typeof node.checked === 'boolean' ? node.checked : node.getAttribute('aria-checked') === 'true',
      required: required(node),
      enabled, readonly, operations: enabled ? operations : [],
      name: node.name || backed?.backing.name || nativeToggle?.name || '', id: node.id || '', autocomplete: node.autocomplete || '',
      placeholder: node.getAttribute('placeholder') || '',
      href: tag === 'a' ? node.href : null,
      options: backed ? [...backed.list.querySelectorAll('[data-dropdown-selection]')].map(o => ({label:o.getAttribute('data-dropdown-label') || o.textContent.trim(),value:o.getAttribute('data-dropdown-selection')})) : tag === 'select' ? [...node.options].map(o => ({label:o.label, value:o.value,
        disabled:o.disabled || !!o.closest('optgroup[disabled]')})) : [],
      group: visual ? `visual-${identity(visual.group)}` : pair ? pair.group : ['radio','checkbox'].includes(role) ? fieldFor(node)?.getAttribute('data-field-path') ||
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
      editors + ',[role="combobox"],[role="listbox"],.select__container,.select2-container,' +
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
