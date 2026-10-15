// An in-memory chrome.* for the unit tests: storage (local + session), runtime, tabs with frames and content-script
// state, scripting, action, alarms, permissions, i18n, windows, commands. It records what the worker does.

export const EXT_ID = "ophngbnbiekjhemmabbpildjedfphlbc";

function emitter() {
  const listeners = [];
  return {
    listeners,
    addListener: (fn) => listeners.push(fn),
    removeListener: (fn) => {
      const index = listeners.indexOf(fn);
      if (index >= 0) listeners.splice(index, 1);
    },
    hasListener: (fn) => listeners.includes(fn),
    dispatch: async (...args) => Promise.all(listeners.map((fn) => fn(...args))),
  };
}

function storageArea(onChanged, name) {
  const data = new Map();
  const clone = (value) => (value === undefined ? undefined : structuredClone(value));
  return {
    data,
    async get(keys) {
      if (keys === null || keys === undefined) return Object.fromEntries([...data].map(([k, v]) => [k, clone(v)]));
      if (typeof keys === "string") return data.has(keys) ? { [keys]: clone(data.get(keys)) } : {};
      if (Array.isArray(keys)) return Object.fromEntries(keys.filter((k) => data.has(k)).map((k) => [k, clone(data.get(k))]));
      return Object.fromEntries(Object.entries(keys).map(([k, d]) => [k, data.has(k) ? clone(data.get(k)) : d]));
    },
    async set(values) {
      const changes = {};
      for (const [key, value] of Object.entries(values)) {
        changes[key] = { oldValue: clone(data.get(key)), newValue: clone(value) };
        data.set(key, clone(value));
      }
      onChanged.dispatch(changes, name);
    },
    async remove(keys) {
      const changes = {};
      for (const key of Array.isArray(keys) ? keys : [keys]) {
        if (data.has(key)) changes[key] = { oldValue: clone(data.get(key)) };
        data.delete(key);
      }
      if (Object.keys(changes).length) onChanged.dispatch(changes, name);
    },
    async clear() {
      data.clear();
    },
  };
}

function patternRegex(pattern) {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp(`^${escaped}$`);
}

/**
 * @param {{version?: string, uiLanguage?: string, matches?: string[], safari?: boolean}} [options]
 */
export function createChrome(options = {}) {
  const version = options.version ?? "0.1.0";
  const atsMatches = options.matches ?? ["https://*.greenhouse.io/*", "https://*.lever.co/*", "https://ats.rippling.com/*"];
  const storageChanged = emitter();
  const log = { badges: new Map(), created: [], updated: [], removed: [], alarms: new Map(), sent: [], injected: [], reloaded: 0, windows: [] };
  const tabs = new Map(); // id -> {id, url, windowId, active}
  const frames = new Map(); // tabId -> Map(frameId -> {frameId, accessible, installed, onMessage: fn|null})
  const injectErrors = new Map(); // tabId -> message
  const granted = new Set(options.granted ?? []);
  let nextTabId = 100;

  const manifest = {
    manifest_version: 3, name: "Meridian Assistant", version,
    content_scripts: [{ matches: atsMatches, js: ["hint.js"], run_at: "document_idle", all_frames: false }],
  };

  const covers = (origin) => [...granted].some((g) => g === origin || patternRegex(g).test(origin));

  const chrome = {
    __log: log,
    __tabs: tabs,
    __frames: frames,
    __granted: granted,
    __injectErrors: injectErrors,
    runtime: {
      id: EXT_ID,
      getManifest: () => manifest,
      getURL: (path) => `chrome-extension://${EXT_ID}/${path}`,
      onMessage: emitter(),
      onInstalled: emitter(),
      reload: () => {
        log.reloaded += 1;
      },
      sendMessage: async () => undefined,
    },
    i18n: { getUILanguage: () => options.uiLanguage ?? "en-US" },
    storage: { local: null, session: null, onChanged: storageChanged },
    alarms: {
      onAlarm: emitter(),
      create: async (name, info) => {
        log.alarms.set(name, info);
      },
      clear: async (name) => log.alarms.delete(name),
    },
    action: {
      onClicked: emitter(),
      setBadgeText: async ({ tabId, text }) => {
        log.badges.set(tabId, { ...(log.badges.get(tabId) ?? {}), text });
      },
      setBadgeBackgroundColor: async ({ tabId, color }) => {
        log.badges.set(tabId, { ...(log.badges.get(tabId) ?? {}), background: color });
      },
      setBadgeTextColor: async ({ tabId, color }) => {
        log.badges.set(tabId, { ...(log.badges.get(tabId) ?? {}), color });
      },
      setTitle: async ({ tabId, title }) => {
        log.badges.set(tabId, { ...(log.badges.get(tabId) ?? {}), title });
      },
    },
    permissions: {
      onAdded: emitter(),
      onRemoved: emitter(),
      contains: async ({ origins = [] }) => origins.every((origin) => covers(origin)),
      request: async ({ origins = [] }) => {
        if (chrome.permissions.__decision === false) return false;
        for (const origin of origins) granted.add(origin);
        return true;
      },
      remove: async ({ origins = [] }) => {
        for (const origin of origins) granted.delete(origin);
        return true;
      },
      getAll: async () => ({ origins: [...granted], permissions: [] }),
    },
    windows: {
      update: async () => undefined,
      // a popup window holds one tab at the url, exactly like the browser's: the worker can find it again
      create: async ({ url, type, width, height, focused }) => {
        if (chrome.windows.__fail) throw new Error("Windows of this type cannot be created here.");
        const tab = { id: nextTabId++, url, windowId: 50 + log.windows.length, active: true };
        tabs.set(tab.id, tab);
        log.windows.push({ url, type, width, height, focused });
        return { id: tab.windowId, tabs: [{ ...tab }] };
      },
    },
    commands: { getAll: async () => [{ name: "_execute_action", shortcut: "⇧⌘Y" }] },
    tabs: {
      onUpdated: emitter(),
      onRemoved: emitter(),
      query: async (query = {}) => {
        let list = [...tabs.values()];
        if (query.url) {
          const matcher = patternRegex(query.url);
          list = list.filter((tab) => matcher.test(tab.url ?? ""));
        }
        return list.map((tab) => ({ ...tab }));
      },
      get: async (id) => {
        if (!tabs.has(id)) throw new Error(`No tab with id: ${id}.`);
        return { ...tabs.get(id) };
      },
      create: async ({ url, active }) => {
        const tab = { id: nextTabId++, url, windowId: 1, active: active !== false };
        tabs.set(tab.id, tab);
        log.created.push({ url, active });
        return { ...tab };
      },
      update: async (id, props) => {
        log.updated.push({ id, ...props });
        Object.assign(tabs.get(id) ?? {}, props);
        return { ...(tabs.get(id) ?? {}) };
      },
      remove: async (id) => {
        log.removed.push(id);
        tabs.delete(id);
      },
      sendMessage: async (tabId, message, opts = {}) => {
        log.sent.push({ tabId, message, frameId: opts.frameId });
        const tabFrames = frames.get(tabId);
        const targets = [...(tabFrames?.values() ?? [])].filter((f) => f.onMessage && (opts.frameId === undefined || f.frameId === opts.frameId));
        if (!targets.length) throw new Error("Could not establish connection. Receiving end does not exist.");
        for (const frame of targets) frame.onMessage(message);
        return undefined;
      },
    },
    scripting: {
      executeScript: async ({ target, func, files }) => {
        const message = injectErrors.get(target.tabId);
        if (message) throw new Error(message);
        const tabFrames = frames.get(target.tabId);
        if (!tabFrames) throw new Error(`No tab with id: ${target.tabId}.`);
        let list = [...tabFrames.values()];
        if (target.frameIds) list = list.filter((f) => target.frameIds.includes(f.frameId));
        else if (!target.allFrames) list = list.filter((f) => f.frameId === 0);
        list = list.filter((f) => f.accessible);
        if (!list.length) throw new Error("Cannot access contents of the page. Extension manifest must request permission to access the respective host.");
        const results = [];
        for (const frame of list) {
          if (files) {
            log.injected.push({ tabId: target.tabId, frameId: frame.frameId, files });
            frame.installed = true;
            if (frame.onInstall) frame.onInstall(frame);
            results.push({ frameId: frame.frameId, result: undefined });
          } else {
            const previous = globalThis.__meridianAssist;
            globalThis.__meridianAssist = frame.installed ? { active: false } : undefined;
            try {
              results.push({ frameId: frame.frameId, result: func() });
            } finally {
              globalThis.__meridianAssist = previous;
            }
          }
        }
        return results;
      },
    },
  };
  chrome.storage.local = storageArea(storageChanged, "local");
  chrome.storage.local.setAccessLevel = async ({ accessLevel }) => {
    log.accessLevel = accessLevel;
  };
  chrome.storage.session = storageArea(storageChanged, "session");
  chrome.storage.session.setAccessLevel = async ({ accessLevel }) => {
    log.sessionAccessLevel = accessLevel;
  };
  chrome.permissions.__decision = true;

  /** Adds a tab with a top frame (and optional child frames) to the mock browser. */
  chrome.__received = [];
  const makeFrame = (id, frameId, accessible) => ({
    frameId, accessible, installed: false, onMessage: null,
    // what injecting content.js does: from now on the frame hears messages and keeps what it hears
    onInstall(frame) {
      frame.onMessage = (message) => chrome.__received.push({ tabId: id, frameId: frame.frameId, message });
    },
  });
  chrome.__addTab = (url, extra = {}) => {
    const id = extra.id ?? nextTabId++;
    tabs.set(id, { id, url, windowId: 1, active: true });
    const list = new Map();
    list.set(0, makeFrame(id, 0, extra.accessible !== false));
    for (const child of extra.frames ?? []) list.set(child.frameId, makeFrame(id, child.frameId, child.accessible !== false));
    frames.set(id, list);
    return id;
  };
  /** A new document in the tab: the content scripts are gone, frame accessibility is whatever the caller says. */
  chrome.__navigate = (tabId, url, extra = {}) => {
    tabs.get(tabId).url = url;
    const list = new Map();
    list.set(0, makeFrame(tabId, 0, extra.accessible !== false));
    for (const child of extra.frames ?? []) list.set(child.frameId, makeFrame(tabId, child.frameId, child.accessible !== false));
    frames.set(tabId, list);
  };
  chrome.__addFrame = (tabId, frameId, accessible = true) => frames.get(tabId).set(frameId, makeFrame(tabId, frameId, accessible));
  chrome.__messagesTo = (tabId, type) => chrome.__received.filter((entry) => entry.tabId === tabId && entry.message.t === type);
  return chrome;
}

export function installChrome(options) {
  const chrome = createChrome(options);
  globalThis.chrome = chrome;
  return chrome;
}
