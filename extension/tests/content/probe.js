// Counts what a page-side observer would see leaking: live event listeners, live Mutation/Resize observers and live
// intervals. Installed with add_init_script BEFORE the content bundle, so every registration of the assistant is counted.
(() => {
  if (window.__probeInstalled) return;
  window.__probeInstalled = true;
  const ids = new WeakMap();
  let next = 0;
  const idOf = (t) => { let v = ids.get(t); if (!v) { v = ++next; ids.set(t, v); } return v; };
  const live = new Map(); // "targetId|type|capture" -> Set(listener)
  const add = EventTarget.prototype.addEventListener;
  const remove = EventTarget.prototype.removeEventListener;
  const capOf = (o) => (typeof o === 'boolean' ? o : !!(o && o.capture));
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    try {
      if (fn) {
        const k = idOf(this) + '|' + type + '|' + capOf(opts);
        let set = live.get(k);
        if (!set) { set = new Set(); live.set(k, set); }
        set.add(fn);
        if (opts && typeof opts === 'object' && opts.once) set.delete(fn); // one-shots remove themselves: not tracked
      }
    } catch (e) { /* never break the page */ }
    return add.call(this, type, fn, opts);
  };
  EventTarget.prototype.removeEventListener = function (type, fn, opts) {
    try {
      const set = live.get(idOf(this) + '|' + type + '|' + capOf(opts));
      if (set) set.delete(fn);
    } catch (e) { /* ignore */ }
    return remove.call(this, type, fn, opts);
  };
  const observers = new Set();
  for (const name of ['MutationObserver', 'ResizeObserver']) {
    const proto = window[name] && window[name].prototype;
    if (!proto) continue;
    const observe = proto.observe;
    const disconnect = proto.disconnect;
    proto.observe = function (...args) { observers.add(this); return observe.apply(this, args); };
    proto.disconnect = function (...args) { observers.delete(this); return disconnect.apply(this, args); };
  }
  const intervals = new Set();
  const si = window.setInterval;
  const ci = window.clearInterval;
  window.setInterval = function (...args) { const id = si.apply(window, args); intervals.add(id); return id; };
  window.clearInterval = function (id) { intervals.delete(id); return ci.call(window, id); };
  const total = () => { let n = 0; for (const set of live.values()) n += set.size; return n; };
  window.__probe = () => ({
    listeners: total(), observers: observers.size, intervals: intervals.size,
    byType: Object.fromEntries([...live.entries()].filter(([, s]) => s.size).map(([k, s]) => [k.split('|').slice(1).join('|'), s.size])),
  });
})();
