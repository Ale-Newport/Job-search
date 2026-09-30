import { useCallback, useEffect, useState } from "react";
import type { Data, List } from "./types";
let connection: Promise<{ base_url: string; token: string }> | undefined;
export const desktop = () => "__TAURI_INTERNALS__" in window;
export async function showReviewWindow() {
  if (desktop())
    await (await import("@tauri-apps/api/core")).invoke("show_review_window");
}
async function getConnection() {
  if (!connection)
    connection = desktop()
      ? import("@tauri-apps/api/core")
          .then(({ invoke }) =>
            invoke<{ base_url: string; token: string }>("connection_info"),
          )
          .catch((error) => {
            connection = undefined;
            throw error;
          })
      : Promise.resolve({
          base_url: "/api",
          token: "meridian-development-token",
        });
  return connection;
}
export async function api<T = Data>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const c = await getConnection();
  const isForm = body instanceof FormData;
  const response = await fetch(`${c.base_url}${path}`, {
    method,
    signal,
    headers: {
      Authorization: `Bearer ${c.token}`,
      ...(!isForm && body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    const detail =
      error.detail ?? error.message ?? `Request failed (${response.status})`;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  if (response.status === 204) return {} as T;
  return response.json();
}
export async function download(path: string, name: string) {
  if (desktop()) {
    const file = await api<{ source: string; name: string }>(
      "/exports/path",
      "POST",
      { path },
    );
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<boolean>("export_file", {
      source: file.source,
      name: file.name || name,
    });
  }
  const c = await getConnection();
  const url = path.startsWith("/api/")
    ? `${c.base_url}${path.slice(4)}`
    : `${c.base_url}${path}`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${c.token}` },
  });
  if (!response.ok) throw new Error(`Download failed (${response.status})`);
  const blob = await response.blob();
  const objectURL = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectURL;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectURL), 60000);
}
export async function openExternal(url: string) {
  const parsed = new URL(url);
  if (!["https:", "http:"].includes(parsed.protocol))
    throw new Error("Only HTTP and HTTPS links can be opened.");
  if (desktop()) await (await import("@tauri-apps/plugin-opener")).openUrl(url);
  else window.open(url, "_blank", "noopener,noreferrer");
}
export function useResource<T = Data>(path: string | null, refresh = 0) {
  const [data, setData] = useState<T | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [version, setVersion] = useState(0);
  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, "GET", undefined, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, refresh, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, loading, error, reload };
}
export function items<T = Data>(data: List<T> | T[] | Data | null): T[] {
  return Array.isArray(data)
    ? data
    : Array.isArray(data?.items)
      ? data.items
      : [];
}
export const pretty = (value: unknown): string =>
  typeof value === "string"
    ? value
    : value == null
      ? ""
      : JSON.stringify(value, null, 2);
export const label = (text: unknown): string =>
  String(text ?? "")
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
export function date(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d.getTime())
    ? value
    : d.toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      });
}
export function time(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d.getTime())
    ? value
    : d.toLocaleString(undefined, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}
