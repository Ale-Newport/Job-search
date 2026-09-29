import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { api, time, useResource } from "./api";
import {
  Button,
  Confirm,
  Editor,
  MoreLink,
  Panel,
  Resource,
  Tag,
} from "./components";
import type { AppContext, Data, Page } from "./types";

interface SetupStep {
  id: string;
  status: "pending" | "in_progress" | "complete" | "needs_attention";
  detail: string;
  evidence?: Data;
}
interface SetupState {
  hidden: boolean;
  completed: number;
  total: number;
  steps: SetupStep[];
  browser_confirmation?: Data;
  boundaries_confirmed_at?: string;
}
const steps: { id: string; title: string; page: Page; target?: string }[] = [
  { id: "documents", title: "Import your documents", page: "documents" },
  {
    id: "profile",
    title: "Review your profile",
    page: "profile",
    target: "review",
  },
  {
    id: "search_profile",
    title: "Set your job preferences",
    page: "discover",
    target: "profiles",
  },
  {
    id: "browser_account",
    title: "Confirm your browser account",
    page: "automation",
  },
  { id: "email", title: "Connect application email", page: "email" },
  { id: "ai", title: "Configure and test AI", page: "settings", target: "ai" },
  {
    id: "boundaries",
    title: "Review automation boundaries",
    page: "settings",
    target: "automation",
  },
  {
    id: "search_run",
    title: "Run your first search",
    page: "discover",
    target: "sources",
  },
];
const statusLabels = {
  pending: "Not started",
  in_progress: "In progress",
  complete: "Complete",
  needs_attention: "Needs attention",
};

export function SetupGuide({ ctx }: { ctx: AppContext }) {
  const r = useResource<SetupState>("/onboarding", ctx.refresh);
  useEffect(() => {
    const interval = setInterval(r.reload, 15000);
    return () => clearInterval(interval);
  }, [r.reload]);
  if (r.data?.hidden) return null;
  const completed =
    r.data?.steps.filter((step) => step.status === "complete").length || 0;
  return (
    <Panel className="onboarding-card">
      <div className="onboarding-intro">
        <div className="onboarding-symbol">
          <Sparkles size={24} />
        </div>
        <div>
          <span className="eyebrow">MAKE MERIDIAN YOURS</span>
          <h3>Your setup, one step at a time.</h3>
          <p>
            Complete these steps in any order. Progress follows saved evidence
            and your explicit confirmations. You can use Meridian while setup is
            in progress.
          </p>
        </div>
        <MoreLink
          onClick={() =>
            ctx.act(
              "onboarding-hide",
              () => api("/onboarding", "PATCH", { hidden: true }),
              "Setup guide hidden. Progress is unchanged; reopen it in Settings.",
            )
          }
        >
          Hide guide
        </MoreLink>
      </div>
      <Resource loading={r.loading && !r.data} error={r.error} retry={r.reload}>
        <div className="setup-progress">
          <span>
            {completed} of {steps.length} steps complete
          </span>
          <progress
            aria-label="Setup progress"
            value={completed}
            max={steps.length}
          />
          <button className="text-button" onClick={r.reload}>
            <RefreshCw size={13} /> Refresh progress
          </button>
        </div>
        <div className="onboarding-steps">
          {steps.map((step, index) => {
            const state = r.data?.steps.find((entry) => entry.id === step.id);
            const status = state?.status || "pending";
            return (
              <button
                key={step.id}
                data-step={step.id}
                data-status={status}
                onClick={() => ctx.navigate(step.page, step.target)}
              >
                <span
                  className={`step-number ${status === "complete" ? "done" : ""}`}
                >
                  {status === "complete" ? <Check size={13} /> : index + 1}
                </span>
                <div>
                  <strong>{step.title}</strong>
                  <Tag
                    tone={
                      status === "complete"
                        ? "green"
                        : status === "needs_attention"
                          ? "amber"
                          : "neutral"
                    }
                  >
                    {statusLabels[status]}
                  </Tag>
                  <p>{state?.detail || "Waiting for setup evidence."}</p>
                </div>
                <ArrowRight size={14} />
              </button>
            );
          })}
        </div>
      </Resource>
    </Panel>
  );
}

export function BrowserAccountConfirmation({ ctx }: { ctx: AppContext }) {
  const r = useResource<SetupState>("/onboarding", ctx.refresh);
  const [editing, setEditing] = useState(false);
  const step = r.data?.steps.find((entry) => entry.id === "browser_account");
  const confirmation = r.data?.browser_confirmation;
  return (
    <div className="setup-confirmation">
      <strong>Account sign-in confirmation</strong>
      <p>
        A running browser does not prove you are signed in. After checking the
        account yourself in the dedicated browser, record your confirmation
        here.
      </p>
      <Resource loading={r.loading && !r.data} error={r.error} retry={r.reload}>
        <p>{step?.detail}</p>
        {confirmation?.confirmed_at && (
          <small>
            Confirmed by you: {confirmation.provider} ·{" "}
            {time(confirmation.confirmed_at)}
          </small>
        )}
        <div className="actions">
          <Button secondary onClick={() => setEditing(true)}>
            {step?.status === "complete"
              ? "Update sign-in confirmation"
              : "Confirm I am signed in"}
          </Button>
          {step?.status === "complete" && (
            <Button
              secondary
              loading={ctx.busy === "browser-confirmation"}
              onClick={() =>
                ctx.act(
                  "browser-confirmation",
                  () =>
                    api("/onboarding/browser-account", "POST", {
                      provider: confirmation?.provider || "browser",
                      confirmed: false,
                    }),
                  "Sign-in confirmation cleared.",
                )
              }
            >
              Clear confirmation
            </Button>
          )}
        </div>
      </Resource>
      {editing && (
        <Editor
          title="Confirm browser sign-in"
          description="This records your own check. Meridian cannot verify an account login from an open page alone. Do not enter your password."
          fields={[
            {
              key: "provider",
              label: "Account website",
              placeholder: "LinkedIn, Indeed, or employer website",
              required: true,
            },
            {
              key: "confirmed",
              label:
                "I checked this account and I am signed in in Meridian’s dedicated browser",
              type: "checkbox",
              required: true,
            },
          ]}
          initial={{ provider: confirmation?.provider || "", confirmed: false }}
          submit="Record my confirmation"
          onClose={() => setEditing(false)}
          onSave={async (values) => {
            if (!values.confirmed)
              throw new Error(
                "Check the confirmation only after verifying your sign-in.",
              );
            await api("/onboarding/browser-account", "POST", values);
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Your manual sign-in confirmation was recorded.",
            );
          }}
        />
      )}
    </div>
  );
}

export function BoundaryConfirmation({ ctx }: { ctx: AppContext }) {
  const r = useResource<SetupState>("/onboarding", ctx.refresh);
  const [confirm, setConfirm] = useState(false);
  const step = r.data?.steps.find((entry) => entry.id === "boundaries");
  return (
    <div className="setup-confirmation">
      <div className="row-between">
        <strong>Confirm your review</strong>
        <ShieldCheck size={18} />
      </div>
      <p>
        Check the mode, schedule, approved domains, limits, and rules above and
        below. Saving a setting does not confirm that you have reviewed all
        boundaries.
      </p>
      <Resource loading={r.loading && !r.data} error={r.error} retry={r.reload}>
        <p>{step?.detail}</p>
        <Button secondary onClick={() => setConfirm(true)}>
          {step?.status === "complete"
            ? "Confirm boundaries again"
            : "I have reviewed these boundaries"}
        </Button>
      </Resource>
      {confirm && (
        <Confirm
          title="Confirm automation boundaries"
          description="Record that you reviewed the current automation settings and rules. This does not enable Auto mode or resume automation. Changing these boundaries will require a new review."
          button="Record my review"
          onClose={() => setConfirm(false)}
          onConfirm={async () => {
            await api("/onboarding/boundaries/confirm", "POST", {});
            await ctx.act(
              "refresh",
              () => Promise.resolve({}),
              "Review recorded for the current boundaries.",
            );
          }}
        />
      )}
    </div>
  );
}
