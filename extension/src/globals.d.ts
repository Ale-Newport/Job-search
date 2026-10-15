declare const __E2E__: boolean;
declare const __SHADOW__: "open" | "closed";
declare const __SAFARI__: boolean;
/** backend/jobagent/automation/observation.js, bundled verbatim by build.mjs. Returns a snapshot of observed controls. */
declare module "meridian-observation" {
  /** `widgets: true` also reports the widget patterns the Playwright filler does not drive (dropzone inputs, listbox buttons, Yes/No pairs). */
  const observe: (options?: { widgets?: boolean }) => ObservationSnapshot;
  export default observe;
  export interface ObservationSnapshot {
    document_id: string; url: string; title: string; text: string;
    elements: Record<string, unknown>[];
    job_context: { title: string; sections: { kind: string; text: string }[] };
    challenge: boolean; mfa: boolean; login: boolean; errors: string[];
    unresolved_required: { label: string; type: string }[];
    scroll: { y: number; height: number; viewport: number };
  }
}
