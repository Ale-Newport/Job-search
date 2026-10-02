import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleDashed,
  LoaderCircle,
  Plus,
  Search,
  X,
} from "lucide-react";
import type { AppContext, Data } from "./types";
import { api, label, pretty } from "./api";

export function Button({
  children,
  secondary = false,
  danger = false,
  loading = false,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  secondary?: boolean;
  danger?: boolean;
  loading?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={`button ${secondary ? "secondary" : ""} ${danger ? "danger" : ""} ${className}`}
    >
      {loading ? <LoaderCircle size={15} className="spin" /> : null}
      {children}
    </button>
  );
}
export function IconButton({
  children,
  title,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { title: string }) {
  return (
    <button
      {...props}
      className={`icon-button ${props.className || ""}`}
      title={title}
      aria-label={title}
    >
      {children}
    </button>
  );
}
export function Tag({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={`tag ${tone}`}>{children}</span>;
}
export function Status({ value }: { value?: string }) {
  const s = (value || "unknown").toLowerCase();
  const positive = new Set([
    "applied",
    "verified",
    "offer",
    "connected",
    "success",
    "succeeded",
    "complete",
    "completed",
    "confirmed",
    "interview",
    "final_interview",
    "recruiter_screen",
  ]);
  return (
    <Tag
      tone={
        positive.has(s)
          ? "green"
          : /error|reject|failed|blocked/.test(s)
            ? "red"
            : /review|human|pending|unverified|interrupt|assessment/.test(s)
              ? "amber"
              : "neutral"
      }
    >
      <span className="status-dot" />
      {label(s)}
    </Tag>
  );
}
export function Score({ value }: { value?: number }) {
  return (
    <span className={`score ${Number(value) >= 80 ? "strong" : ""}`}>
      {value == null ? "—" : Math.round(Number(value))}
      <small>{value == null ? "" : "%"}</small>
    </span>
  );
}
export function Empty({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon || <CircleDashed size={25} />}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Loading({
  label: text = "Loading your workspace…",
}: {
  label?: string;
}) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={20} className="spin" />
      {text}
    </div>
  );
}
export function ErrorBox({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  return (
    <div className="error-box" role="alert">
      <AlertCircle size={18} />
      <div>
        <strong>Something needs attention</strong>
        <p>{error}</p>
      </div>
      {retry && (
        <Button secondary onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
export function Resource({
  loading,
  error,
  retry,
  children,
}: {
  loading: boolean;
  error: string;
  retry?: () => void;
  children: ReactNode;
}) {
  return error ? (
    <ErrorBox error={error} retry={retry} />
  ) : loading ? (
    <Loading />
  ) : (
    <>{children}</>
  );
}
export function SectionTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}
export function Panel({
  id,
  title,
  description,
  children,
  action,
  className = "",
}: {
  id?: string;
  title?: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section id={id} className={`panel ${className}`}>
      {title && (
        <div className="panel-heading">
          <div>
            <h3>{title}</h3>
            {description && <p>{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="search-input">
      <Search size={17} />
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && (
        <IconButton title="Clear search" onClick={() => onChange("")}>
          <X size={14} />
        </IconButton>
      )}
    </div>
  );
}
export function Modal({
  title,
  description,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const firstInput = ref.current?.querySelector<HTMLElement>(
      'input:not([type="file"]):not([disabled]),textarea:not([disabled]),select:not([disabled])',
    );
    (firstInput || ref.current)?.focus();
    const listener = (e: KeyboardEvent) => {
      const dialogs = document.querySelectorAll("[role=dialog]");
      if (dialogs[dialogs.length - 1] !== ref.current) return;
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const targets = ref.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]',
        );
        if (!targets?.length) return;
        const first = targets[0],
          last = targets[targets.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", listener);
    return () => {
      document.removeEventListener("keydown", listener);
      document.body.style.overflow = before;
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <div className="modal-heading">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <IconButton title="Close" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </div>
        {children}
      </div>
    </div>
  );
}
export interface Field {
  key: string;
  label: string;
  type?:
    | "text"
    | "date" | "month"
    | "textarea"
    | "number"
    | "select"
    | "multiselect"
    | "checkbox"
    | "json"
    | "password"
    | "list"
    | "url";
  options?: (string | { value: string; label: string })[];
  required?: boolean;
  hint?: string;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
}
export function Editor({
  title,
  description,
  fields,
  initial = {},
  onSave,
  onClose,
  submit = "Save changes",
}: {
  title: string;
  description?: string;
  fields: Field[];
  initial?: Data;
  onSave: (data: Data) => Promise<any>;
  onClose: () => void;
  submit?: string;
}) {
  const [values, setValues] = useState<Data>(() =>
      Object.fromEntries(
        fields.map((f) => [
          f.key,
          f.type === "multiselect"
            ? (initial[f.key] ?? [])
            : f.type === "json"
              ? JSON.stringify(initial[f.key] ?? {}, null, 2)
              : f.type === "list"
                ? Array.isArray(initial[f.key])
                  ? initial[f.key].join(", ")
                  : (initial[f.key] ?? "")
                : (initial[f.key] ?? (f.type === "checkbox" ? false : "")),
        ]),
      ),
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const parsed: Data = {};
      for (const field of fields) {
        const v = values[field.key];
        parsed[field.key] =
          field.type === "json"
            ? JSON.parse(v || "{}")
            : field.type === "number"
              ? v === ""
                ? null
                : Number(v)
              : field.type === "list"
                ? String(v)
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean)
                : v;
      }
      await onSave(parsed);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={title} description={description} onClose={onClose}>
      <form onSubmit={save} className="editor">
        <div className="form-fields">
          {fields.map((f) => (
            <label
              key={f.key}
              className={`field ${f.type === "checkbox" ? "checkbox-field" : ""}`}
            >
              <span>
                {f.label}
                {f.required && <sup> *</sup>}
              </span>
              {f.type === "checkbox" ? (
                <input
                  aria-label={f.label}
                  type="checkbox"
                  required={f.required}
                  checked={!!values[f.key]}
                  onChange={(e) =>
                    setValues({ ...values, [f.key]: e.target.checked })
                  }
                />
              ) : f.type === "select" || f.type === "multiselect" ? (
                <select
                  aria-label={f.label}
                  required={f.required}
                  multiple={f.type === "multiselect"}
                  size={
                    f.type === "multiselect"
                      ? Math.min(f.options?.length || 3, 5)
                      : undefined
                  }
                  value={values[f.key]}
                  onChange={(e) =>
                    setValues({
                      ...values,
                      [f.key]:
                        f.type === "multiselect"
                          ? Array.from(e.target.selectedOptions).map(
                              (option) => option.value,
                            )
                          : e.target.value,
                    })
                  }
                >
                  {f.type !== "multiselect" && (
                    <option value="" disabled={f.required}>
                      {f.required ? "Select…" : "Not specified"}
                    </option>
                  )}
                  {f.options?.map((o) => (
                    <option
                      key={typeof o === "string" ? o : o.value}
                      value={typeof o === "string" ? o : o.value}
                    >
                      {typeof o === "string" ? label(o) : o.label}
                    </option>
                  ))}
                </select>
              ) : f.type === "textarea" || f.type === "json" ? (
                <textarea
                  aria-label={f.label}
                  rows={f.type === "json" ? 10 : 4}
                  className={f.type === "json" ? "code" : ""}
                  required={f.required}
                  value={values[f.key]}
                  onChange={(e) =>
                    setValues({ ...values, [f.key]: e.target.value })
                  }
                />
              ) : (
                <input
                  aria-label={f.label}
                  type={f.type === "list" ? "text" : f.type || "text"}
                  required={f.required}
                  min={f.min}
                  max={f.max}
                  step={f.step}
                  placeholder={f.placeholder}
                  autoComplete={
                    f.type === "password" ? "new-password" : undefined
                  }
                  value={values[f.key]}
                  onChange={(e) =>
                    setValues({ ...values, [f.key]: e.target.value })
                  }
                />
              )}{" "}
              {f.hint && <small>{f.hint}</small>}
            </label>
          ))}
        </div>
        {error && <ErrorBox error={error} />}
        <div className="modal-footer">
          <Button type="button" secondary onClick={onClose}>
            Cancel
          </Button>
          <Button loading={busy} type="submit">
            <Check size={15} />
            {submit}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
export function Confirm({
  title,
  description,
  onConfirm,
  onClose,
  danger = false,
  button = "Confirm",
}: {
  title: string;
  description: string;
  onConfirm: () => Promise<any>;
  onClose: () => void;
  danger?: boolean;
  button?: string;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <p className="modal-description">{description}</p>
      {error && <ErrorBox error={error} />}
      <div className="modal-footer">
        <Button secondary onClick={onClose}>
          Cancel
        </Button>
        <Button
          danger={danger}
          loading={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onConfirm();
              onClose();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {button}
        </Button>
      </div>
    </Modal>
  );
}
export function KeyValues({
  data,
  exclude = [],
}: {
  data: Data;
  exclude?: string[];
}) {
  return (
    <dl className="key-values">
      {Object.entries(data)
        .filter(
          ([k, v]) =>
            !exclude.includes(k) && v !== undefined && v !== null && v !== "",
        )
        .map(([k, v]) => (
          <div key={k}>
            <dt>{label(k)}</dt>
            <dd>
              {typeof v === "boolean" ? (
                v ? (
                  "Yes"
                ) : (
                  "No"
                )
              ) : typeof v === "object" ? (
                <pre>{pretty(v)}</pre>
              ) : (
                String(v)
              )}
            </dd>
          </div>
        ))}
    </dl>
  );
}
export function JsonDetails({
  title = "Technical details",
  data,
  open = false,
}: {
  title?: string;
  data: unknown;
  open?: boolean;
}) {
  return (
    <details className="json-details" open={open || undefined}>
      <summary>{title}</summary>
      <pre>{pretty(data)}</pre>
    </details>
  );
}
export function Pagination({
  page,
  total,
  limit = 50,
  onChange,
}: {
  page: number;
  total: number;
  limit?: number;
  onChange: (p: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="pagination">
      <span>
        {total.toLocaleString()} results · Page {page + 1} of {pages}
      </span>
      <div className="actions">
        <Button
          secondary
          disabled={page === 0}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </Button>
        <Button
          secondary
          disabled={page + 1 >= pages}
          onClick={() => onChange(page + 1)}
        >
          Next <ChevronRight size={14} />
        </Button>
      </div>
    </div>
  );
}
export function AddButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <Button onClick={onClick}>
      <Plus size={16} />
      {children}
    </Button>
  );
}
export function MoreLink({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-button" onClick={onClick}>
      {children}
      <ArrowUpRight size={14} />
    </button>
  );
}
export function UploadButton({
  ctx,
  path,
  label: text,
  accept,
  success,
  onResult,
}: {
  ctx: AppContext;
  path: string;
  label: string;
  accept: string;
  success?: string;
  onResult?: (r: Data) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        className="visually-hidden"
        type="file"
        ref={ref}
        accept={accept}
        aria-label={text}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (file) {
            const form = new FormData();
            form.append("file", file);
            const result = await ctx.act(
              `upload:${path}`,
              () => api(path, "POST", form),
              success,
            );
            if (result) onResult?.(result);
          }
          e.target.value = "";
        }}
      />
      <Button
        loading={ctx.busy === `upload:${path}`}
        onClick={() => ref.current?.click()}
      >
        <Plus size={16} />
        {text}
      </Button>
    </>
  );
}
