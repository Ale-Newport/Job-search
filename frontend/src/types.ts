// Extensible API records retain provider-specific provenance without losing known fields.
export type Data = Record<string, any>;
export interface Entity extends Data {
  id: string;
}
export interface Job extends Entity {
  title: string;
  company: string;
  location: string;
  url: string;
  application_url?: string;
  description?: string;
  source?: string;
  ats?: string;
  match_score?: number;
  priority_score?: number;
  status: string;
  skills?: string[];
  remote?: boolean;
  salary_min?: number;
  salary_max?: number;
  currency?: string;
  created_at?: string;
}
export interface Fact extends Entity {
  category: string;
  key: string;
  value: any;
  source?: string;
  verification_status: string;
  locked: boolean;
  notes?: string;
}
export interface Application extends Entity {
  job_id: string;
  status: string;
  title?: string;
  company?: string;
  job?: Job;
  notes?: string;
  mode?: string;
  events?: Data[];
  answers?: Data[];
  documents?: Data[];
  emails?: Data[];
  runs?: Data[];
  tasks?: Data[];
}
export interface Document extends Entity {
  name?: string;
  filename?: string;
  kind?: string;
  created_at?: string;
  versions?: Data[];
  latest_version_id?: string;
}
export interface List<T> {
  items: T[];
  total: number;
}
export type Page =
  | "dashboard"
  | "discover"
  | "jobs"
  | "applications"
  | "review"
  | "companies"
  | "profile"
  | "documents"
  | "email"
  | "analytics"
  | "automation"
  | "activity"
  | "settings";
export interface Selection {
  kind: "job" | "application" | "email" | "run" | "document";
  id: string;
}
export interface AppContext {
  refresh: number;
  navigate: (page: Page, target?: string) => void;
  target?: string;
  select: (selection: Selection | null) => void;
  act: (
    label: string,
    action: () => Promise<any>,
    success?: string,
  ) => Promise<any>;
  busy: string | null;
  toast: (text: string, error?: boolean) => void;
}
