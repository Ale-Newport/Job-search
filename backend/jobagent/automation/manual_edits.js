(() => {
  if (globalThis.__meridianEdits) return;
  const data = __DATA__, observe = __OBSERVE__;
  const records = new Map(), timers = new Map();
  let automation = new Set(), revision = 0, lastCombo = null;
  const owned = new Set();
  const selected = new WeakSet();
  const keys = new WeakMap();
  const describe = node => {
    const snapshot = observe();
    const id = globalThis.__meridianObservedControlsV1.ids.get(node);
    return {snapshot, element:snapshot.elements.find(e => e.node_id === id)};
  };
  const resolve = event => {
    const path = event.composedPath();
    const option = path.find(n => n instanceof Element && n.matches('[role="option"],[role="treeitem"]'));
    if (option) {
      const list = option.closest('[role="listbox"],.select2-results');
      const combos = [...document.querySelectorAll('[role="combobox"]')].filter(n =>
        n.getAttribute('aria-expanded') === 'true' &&
        (!list?.id || [n.getAttribute('aria-controls'),n.getAttribute('aria-owns')].includes(list.id)));
      return combos.length === 1 ? combos[0] : lastCombo;
    }
    const node = path.find(n => n instanceof Element && n.matches('input,textarea,select,[contenteditable="true"],[role="combobox"],[role="checkbox"],[role="radio"],button[data-option]'));
    if (node?.matches('input[type="search"],input[role="searchbox"]')) {
      const owners = [...document.querySelectorAll('[role="combobox"][aria-expanded="true"]')].filter(combo =>
        (combo.getAttribute('aria-controls') || combo.getAttribute('aria-owns') || '').split(/\s+/).some(id => {
          const list = document.getElementById(id), parent = list?.parentElement;
          return list?.contains(node) || parent && !parent.matches('body,main,form') && parent.contains(node);
        }));
      if (owners.length === 1) return owners[0];
    }
    return node;
  };
  const emit = (node, commit=false, changed=false) => {
    if (node && !node.isConnected && keys.has(node)) {
      const current = observe().elements.find(e => e.manual_key === keys.get(node));
      node = current && globalThis.__meridianObservedControlsV1.nodes.get(current.node_id);
    }
    if (!node?.isConnected || ['password','hidden'].includes(node.type)) return;
    const {snapshot,element} = describe(node);
    // Sign-in fields are credentials, not candidate-profile corrections.
    if (!element || snapshot.login || snapshot.mfa || snapshot.challenge) return;
    keys.set(node,element.manual_key);
    let answer = element.selected_text || element.value || '';
    if (element.role === 'radio') {
      const chosen = snapshot.elements.find(e => e.manual_key === element.manual_key && e.checked);
      answer = chosen?.label || '';
    } else if (['checkbox','switch'].includes(element.role)) answer = element.checked ? 'Yes' : 'No';
    const previous = records.get(element.manual_key);
    changed = changed || previous?.changed || false;
    const pending = element.role === 'combobox' && element.tag !== 'select' &&
      (element.expanded || (!element.selected_text && document.activeElement === node && !selected.has(node)));
    const record = {token:data.token,element,answer:String(answer),revision:++revision,
      changed,pending,commit:commit && changed && !pending,frame_url:location.href};
    records.set(element.manual_key,record);
    globalThis[data.binding](JSON.stringify(record));
  };
  const onEvent = event => {
    if (!event.isTrusted) return;
    const node = resolve(event);
    if (!node) return;
    const {element} = describe(node);
    if (!element) return;
    if (event.type === 'blur' && !records.has(element.manual_key)) return;
    const direct = event.composedPath().find(n => n instanceof Element &&
      ['option','treeitem'].includes(n.getAttribute('role')));
    const directKey = direct && describe(direct).element?.manual_key;
    // A native change can arrive on blur, after fill() has returned. Ownership
    // ends on fresh user intent, not when the engine's await finishes.
    if (event.type === 'keydown' || event.type === 'pointerdown' &&
        !automation.has(element.manual_key) && !automation.has(directKey)) {
      owned.delete(element.manual_key);
      if (directKey) owned.delete(directKey);
    }
    // Engine actions suppress only their exact control (and linked menu), so
    // typing in another control while an upload/click runs is still captured.
    if (event.type !== 'keydown' && (automation.has(element.manual_key) || automation.has(directKey)) &&
        !records.has(element.manual_key)) return;
    if (owned.has(element.manual_key) || owned.has(directKey)) return;
    if (event.type === 'keydown' && ['Tab','Shift','Control','Meta','Alt','Escape'].includes(event.key)) return;
    node.setAttribute('data-meridian-user-edited', '');
    if (event.type === 'input') selected.delete(node);
    if (event.type === 'click' && direct) selected.add(node);
    if (element.role === 'combobox') lastCombo = node;
    const changed = ['input','change'].includes(event.type) || event.type === 'click' &&
      (direct || ['checkbox','radio','switch'].includes(element.role));
    emit(node,false,changed);
    if (timers.has(element.manual_key)) clearTimeout(timers.get(element.manual_key));
    if (changed || event.type === 'blur') timers.set(element.manual_key,
      setTimeout(() => emit(node,true,changed), event.type === 'blur' ? 0 : 650));
  };
  for (const name of ['pointerdown','keydown','input','change','click','blur']) document.addEventListener(name,onEvent,true);
  globalThis.__meridianEdits = {
    automate(keys) {automation = new Set(keys); for (const key of keys) owned.add(key);},
    flush() {const current = observe(); for (const record of records.values()) {
      const element = current.elements.find(e => e.manual_key === record.element.manual_key);
      const node = element && globalThis.__meridianObservedControlsV1.nodes.get(element.node_id);
      if (node) emit(node,true,record.changed);
    }},
    remove() {for (const name of ['pointerdown','keydown','input','change','click','blur']) document.removeEventListener(name,onEvent,true);
      for (const timer of timers.values()) clearTimeout(timer);
      for (const node of document.querySelectorAll('[data-meridian-user-edited]')) node.removeAttribute('data-meridian-user-edited');}
  };
})();
