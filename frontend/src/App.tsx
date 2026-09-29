import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity as ActivityIcon,
  ArrowRight,
  BellDot,
  BriefcaseBusiness,
  Building2,
  CheckCheck,
  ChevronRight,
  Compass,
  FileText,
  GraduationCap,
  LayoutDashboard,
  LoaderCircle,
  Mail,
  Menu,
  Moon,
  Pause,
  Play,
  Search,
  Settings2,
  ShieldCheck,
  Sun,
  TrendingUp,
  X,
  Zap,
} from "lucide-react";
import type { AppContext, Data, Page, Selection } from "./types";
import { api, items, label, useResource } from "./api";
import {
  Activity,
  Analytics,
  Applications,
  Companies,
  Dashboard,
  Discover,
  Documents,
  Email,
  Jobs,
  Profile,
} from "./pages";
import { Automation, Settings } from "./settings";
import { Detail } from "./details";
import {
  Button,
  Empty,
  IconButton,
  Modal,
  Resource,
  SearchInput,
  Tag,
} from "./components";
const navigation = [
  { id: "dashboard", label: "Overview", icon: LayoutDashboard },
  { id: "discover", label: "Discover", icon: Compass },
  { id: "jobs", label: "Jobs", icon: BriefcaseBusiness },
  { id: "applications", label: "Applications", icon: LayoutDashboard },
  { id: "review", label: "Review queue", icon: ShieldCheck },
  { id: "companies", label: "Companies", icon: Building2 },
  { id: "profile", label: "Profile", icon: GraduationCap },
  { id: "documents", label: "Documents", icon: FileText },
  { id: "email", label: "Email", icon: Mail },
  { id: "analytics", label: "Analytics", icon: TrendingUp },
  { id: "automation", label: "Automation", icon: Zap },
  { id: "activity", label: "Activity", icon: ActivityIcon },
  { id: "settings", label: "Settings", icon: Settings2 },
] as const;
export default function App() {
  const [page, setPage] = useState<Page>("dashboard"),
    [selection, setSelection] = useState<Selection | null>(null),
    [refresh, setRefresh] = useState(0),
    [busy, setBusy] = useState<string | null>(null),
    [notice, setNotice] = useState<{ text: string; error: boolean } | null>(
      null,
    ),
    [search, setSearch] = useState(false),
    [theme, setThemeState] = useState(
      () => localStorage.getItem("meridian-theme") || "system",
    ),
    [sidebar, setSidebar] = useState(false);
  const health = useResource("/health", refresh),
    automation = useResource("/automation", refresh),
    tasks = useResource("/human-tasks", refresh);
  const reloadHealth = health.reload,
    reloadAutomation = automation.reload,
    reloadTasks = tasks.reload;
  const navigate = useCallback((p: Page) => {
    setPage(p);
    setSelection(null);
    setSidebar(false);
    window.scrollTo({ top: 0 });
  }, []);
  const toast = useCallback(
    (text: string, error = false) => setNotice({ text, error }),
    [],
  );
  const act = useCallback(
    async (name: string, action: () => Promise<any>, success?: string) => {
      setBusy(name);
      try {
        const result = await action();
        setRefresh((v) => v + 1);
        if (success) setNotice({ text: success, error: false });
        return result ?? {};
      } catch (e) {
        setNotice({
          text: e instanceof Error ? e.message : String(e),
          error: true,
        });
        return null;
      } finally {
        setBusy(null);
      }
    },
    [],
  );
  const ctx = useMemo<AppContext>(
    () => ({ refresh, navigate, select: setSelection, act, busy, toast }),
    [refresh, navigate, act, busy, toast],
  );
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () =>
      (document.documentElement.dataset.theme =
        theme === "system" ? (media.matches ? "dark" : "light") : theme);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [theme]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch((s) => !s);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), notice.error ? 15000 : 6500);
    return () => clearTimeout(t);
  }, [notice]);
  useEffect(() => {
    const interval = setInterval(() => {
      reloadHealth();
      reloadAutomation();
      reloadTasks();
    }, 20000);
    return () => clearInterval(interval);
  }, [reloadHealth, reloadAutomation, reloadTasks]);
  useEffect(() => {
    if (!health.error) return;
    const timer = setTimeout(() => setRefresh((v) => v + 1), 3000);
    return () => clearTimeout(timer);
  }, [health.error, refresh]);
  const setTheme = useCallback((value: string) => {
    setThemeState(value);
    localStorage.setItem("meridian-theme", value);
  }, []);
  const attention = items(tasks.data).filter(
      (t) =>
        !["resolved", "completed"].includes(String(t.status).toLowerCase()),
    ).length,
    paused = automation.data?.paused ?? true;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebar ? "shown" : ""}`}>
        <div className="brand" data-tauri-drag-region>
          <span className="brand-symbol">
            <Compass size={25} strokeWidth={1.7} />
          </span>
          <div>
            <strong>meridian</strong>
            <span>YOUR CAREER WORKSPACE</span>
          </div>
        </div>
        <button
          className="workspace-switch"
          onClick={() => navigate("profile")}
        >
          <span className="workspace-avatar">M</span>
          <span>
            <strong>Personal workspace</strong>
            <small>Local to this Mac</small>
          </span>
          <ChevronRight size={15} />
        </button>
        <nav aria-label="Main navigation">
          <span className="nav-caption">WORKSPACE</span>
          {navigation.map((n, i) => (
            <div key={n.id}>
              {(i === 6 || i === 10) && (
                <span className="nav-caption">
                  {i === 6 ? "YOUR FOUNDATION" : "CONTROL CENTER"}
                </span>
              )}
              <button
                className={`nav-item ${page === n.id ? "active" : ""}`}
                aria-current={page === n.id ? "page" : undefined}
                onClick={() => navigate(n.id)}
              >
                <n.icon size={18} strokeWidth={1.7} />
                <span>{n.label}</span>
                {n.id === "review" && attention > 0 && (
                  <span className="nav-count">{attention}</span>
                )}
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className={`connection-dot ${health.error ? "offline" : ""}`} />
          <span>
            {health.error
              ? "Local service unavailable"
              : "Private. Local. Yours."}
          </span>
          <IconButton
            title="Toggle appearance"
            onClick={() =>
              setTheme(
                document.documentElement.dataset.theme === "dark"
                  ? "light"
                  : "dark",
              )
            }
          >
            {document.documentElement.dataset.theme === "dark" ? (
              <Sun size={16} />
            ) : (
              <Moon size={16} />
            )}
          </IconButton>
        </div>
      </aside>
      {sidebar && (
        <button
          className="sidebar-scrim"
          aria-label="Close navigation"
          onClick={() => setSidebar(false)}
        />
      )}
      <main className="main">
        <header className="topbar">
          <div className="breadcrumbs">
            <IconButton
              title="Open navigation"
              className="mobile-menu"
              onClick={() => setSidebar(!sidebar)}
            >
              <Menu size={19} />
            </IconButton>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{navigation.find((n) => n.id === page)?.label}</strong>
          </div>
          <div className="topbar-actions">
            <button className="global-search" onClick={() => setSearch(true)}>
              <Search size={16} />
              <span>Search anything</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className={`automation-state ${paused ? "" : "is-active"}`}>
              <span className="status-dot" />
              {paused ? "Paused" : "Active"}
            </span>
            <Button
              secondary={!paused}
              danger={!paused}
              loading={busy === "global-pause"}
              disabled={!automation.data}
              onClick={() =>
                act(
                  "global-pause",
                  () =>
                    api(
                      `/automation/${paused ? "resume" : "pause"}`,
                      "POST",
                      {},
                    ),
                  paused ? "Automation resumed." : "Automation paused.",
                )
              }
            >
              {paused ? <Play size={14} /> : <Pause size={14} />}
              <span>{paused ? "Resume" : "Pause automation"}</span>
            </Button>
            <IconButton
              title={`${attention} items need attention`}
              onClick={() => navigate("review")}
            >
              <BellDot size={19} />
              {attention > 0 && <span className="notification-badge" />}
            </IconButton>
          </div>
        </header>
        <div className="page-content">
          {health.error && (
            <div className="offline-banner">
              <span className="connection-dot offline" />
              <div>
                <strong>The local service is unavailable.</strong>
                <span> {health.error} Reconnecting automatically…</span>
              </div>
              <Button secondary onClick={health.reload}>
                Retry
              </Button>
            </div>
          )}
          {page === "dashboard" ? (
            <Dashboard ctx={ctx} />
          ) : page === "jobs" ? (
            <Jobs ctx={ctx} />
          ) : page === "discover" ? (
            <Discover ctx={ctx} />
          ) : page === "applications" ? (
            <Applications ctx={ctx} />
          ) : page === "review" ? (
            <Applications ctx={ctx} review />
          ) : page === "companies" ? (
            <Companies ctx={ctx} />
          ) : page === "profile" ? (
            <Profile ctx={ctx} />
          ) : page === "documents" ? (
            <Documents ctx={ctx} />
          ) : page === "email" ? (
            <Email ctx={ctx} />
          ) : page === "analytics" ? (
            <Analytics ctx={ctx} />
          ) : page === "automation" ? (
            <Automation ctx={ctx} />
          ) : page === "activity" ? (
            <Activity ctx={ctx} />
          ) : (
            <Settings ctx={ctx} theme={theme} setTheme={setTheme} />
          )}
          <footer className="page-footer">
            <span>
              Meridian <span className="footer-dot">·</span> Thoughtful
              progress, every day.
            </span>
            <span>
              <ShieldCheck size={12} />
              Stored on your Mac
            </span>
          </footer>
        </div>
      </main>
      {notice && (
        <div
          className={`toast ${notice.error ? "error" : ""}`}
          role={notice.error ? "alert" : "status"}
        >
          {notice.error ? <ShieldCheck size={19} /> : <CheckCheck size={19} />}
          <span>{notice.text}</span>
          <IconButton
            title="Dismiss notification"
            onClick={() => setNotice(null)}
          >
            <X size={16} />
          </IconButton>
        </div>
      )}
      {busy && (
        <div className="background-work" role="status">
          <LoaderCircle className="spin" size={14} />
          Working…
        </div>
      )}
      {selection && (
        <Detail
          key={`${selection.kind}:${selection.id}`}
          selection={selection}
          ctx={ctx}
          onClose={() => setSelection(null)}
        />
      )}{" "}
      {search && <GlobalSearch ctx={ctx} onClose={() => setSearch(false)} />}
    </div>
  );
}
function GlobalSearch({
  ctx,
  onClose,
}: {
  ctx: AppContext;
  onClose: () => void;
}) {
  const [q, setQ] = useState(""),
    [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(q), 200);
    return () => clearTimeout(timer);
  }, [q]);
  const r = useResource(
    query.trim() ? `/search?q=${encodeURIComponent(query)}` : null,
    ctx.refresh,
  );
  let results: Data[] = items(r.data);
  if (!results.length && r.data && !Array.isArray(r.data.items))
    results = Object.entries(r.data).flatMap(([kind, value]) =>
      Array.isArray(value)
        ? value.map((v) => ({ ...v, kind: v.kind || kind }))
        : [],
    );
  const open = (item: Data) => {
    const kind = String(
      item.kind || item.type || item.entity_type || "",
    ).replace(/s$/, "");
    if (["job", "application", "email", "document"].includes(kind)) {
      ctx.select({ kind: kind as Selection["kind"], id: item.id });
      onClose();
    } else {
      ctx.navigate(
        kind === "company"
          ? "companies"
          : kind === "fact" || kind === "skill" || kind === "answer"
            ? "profile"
            : "jobs",
      );
      onClose();
    }
  };
  return (
    <Modal title="Search your workspace" onClose={onClose}>
      <div className="command-search">
        <SearchInput
          value={q}
          onChange={setQ}
          placeholder="Jobs, companies, applications, emails, skills…"
        />
      </div>
      {!query.trim() ? (
        <div className="command-shortcuts">
          <span className="eyebrow">GO TO</span>
          {navigation.map((n) => (
            <button
              key={n.id}
              onClick={() => {
                ctx.navigate(n.id);
                onClose();
              }}
            >
              <n.icon size={17} />
              <span>{n.label}</span>
              <ArrowRight size={14} />
            </button>
          ))}
        </div>
      ) : (
        <Resource {...r} retry={r.reload}>
          {results.length ? (
            <div className="command-results">
              {results.map((v, i) => (
                <button key={`${v.id}-${i}`} onClick={() => open(v)}>
                  <span className="file-icon">
                    <Search size={16} />
                  </span>
                  <span className="grow">
                    <strong>
                      {v.title ||
                        v.subject ||
                        v.name ||
                        v.key ||
                        v.label ||
                        v.id}
                    </strong>
                    <small>
                      {v.subtitle ||
                        v.company ||
                        v.description ||
                        v.snippet ||
                        v.value}
                    </small>
                  </span>
                  <Tag>{label(v.kind || v.type || v.entity_type)}</Tag>
                  <ArrowRight size={14} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title="No results found"
              description="Try another company name, job title, skill, or phrase."
            />
          )}
        </Resource>
      )}
      <div className="command-footer">
        <span>
          <kbd>esc</kbd> to close
        </span>
        <span>Searches only your local workspace</span>
      </div>
    </Modal>
  );
}
