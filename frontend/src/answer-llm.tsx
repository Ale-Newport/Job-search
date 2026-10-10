import { useState } from "react";
import { Cpu, KeyRound, RefreshCw, ShieldCheck, Zap } from "lucide-react";
import { api, items, label, useResource } from "./api";
import {
  Button,
  ErrorBox,
  JsonDetails,
  KeyValues,
  Panel,
  Resource,
  Tag,
} from "./components";
import type { AnswerLLMStatus, AppContext, Data } from "./types";

const providers = [
  { id: "none", name: "Not connected" },
  { id: "openai", name: "OpenAI" },
  { id: "anthropic", name: "Anthropic" },
  { id: "gemini", name: "Google Gemini" },
  { id: "ollama", name: "Ollama · local" },
  { id: "openai-compatible", name: "Custom OpenAI-compatible endpoint" },
];

export function AnswerLLMStatusCard({ ctx }: { ctx: AppContext }) {
  const r = useResource<AnswerLLMStatus>("/ai/answers/status", ctx.refresh);
  return (
    <Panel
      title="Application answer LLM"
      description="Open-ended answers use a generative model and relevant candidate evidence."
    >
      <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
        {r.data && (
          <div className="settings-row">
            <div>
              <strong>
                {r.data.configured
                  ? `${label(r.data.provider)} · ${r.data.model}`
                  : "Connect an answer model"}
              </strong>
              <p>{r.data.message}</p>
            </div>
            <Button secondary onClick={() => ctx.navigate("settings", "ai")}>
              Configure answer LLM
            </Button>
          </div>
        )}
      </Resource>
    </Panel>
  );
}

export function ApplicationAnswerLLMSetup({
  ctx,
  settings,
  integrations,
}: {
  ctx: AppContext;
  settings: Data;
  integrations: Data[];
}) {
  const r = useResource<AnswerLLMStatus>("/ai/answers/status", ctx.refresh);
  return (
    <Panel
      title="Application answer LLM"
      description="An LLM is required to generate personalised answers to open-ended application questions."
    >
      <Resource {...r} loading={r.loading && !r.data} retry={r.reload}>
        {r.data && (
          <AnswerLLMForm
            key={JSON.stringify([
              settings.text_provider,
              settings.text_model,
              settings.text_base_url,
              settings.text_temperature,
              settings.text_temperature_supported,
              settings.text_max_output_tokens,
              settings.text_timeout_seconds,
              settings.text_retry_attempts,
              settings.text_concurrency,
              settings.text_quality_review,
              settings.text_fallback_model,
              settings.monthly_budget,
              integrations.map((i) => [i.provider, i.config]),
            ])}
            ctx={ctx}
            settings={settings}
            integrations={integrations}
            status={r.data}
            reload={r.reload}
          />
        )}
      </Resource>
    </Panel>
  );
}

function AnswerLLMForm({
  ctx,
  settings,
  integrations,
  status,
  reload,
}: {
  ctx: AppContext;
  settings: Data;
  integrations: Data[];
  status: AnswerLLMStatus;
  reload: () => void;
}) {
  const [values, setValues] = useState<Data>({
    text_provider: settings.text_provider || "none",
    text_model: settings.text_model || "",
    text_base_url: settings.text_base_url || "",
    text_temperature: settings.text_temperature ?? 0.2,
    text_temperature_supported: settings.text_temperature_supported ?? true,
    text_max_output_tokens: settings.text_max_output_tokens ?? 2200,
    text_timeout_seconds: settings.text_timeout_seconds ?? 90,
    text_retry_attempts: settings.text_retry_attempts ?? 2,
    text_concurrency: settings.text_concurrency ?? 2,
    text_quality_review: settings.text_quality_review ?? true,
    text_fallback_model: settings.text_fallback_model || "",
    monthly_budget: settings.monthly_budget ?? 0,
  });
  const [secret, setSecret] = useState("");
  const initialPrices =
    integrations.find((i) => i.provider === values.text_provider)?.config || {};
  const [prices, setPrices] = useState<Data>({
    input_cost_per_million: initialPrices.input_cost_per_million ?? "",
    output_cost_per_million: initialPrices.output_cost_per_million ?? "",
  });
  const [models, setModels] = useState<Array<{ id: string; label?: string }>>(
    [],
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<Data | null>(null);
  const provider = values.text_provider;
  const remote = provider !== "none" && provider !== "ollama";
  const change = (key: string, value: unknown) => {
    setValues((v) => ({ ...v, [key]: value }));
    setResult(null);
    setMessage("");
  };
  function selectProvider(next: string, model = "") {
    const config = integrations.find((i) => i.provider === next)?.config || {};
    setValues((v) => ({
      ...v,
      text_provider: next,
      text_model: model,
      text_base_url: "",
    }));
    setPrices({
      input_cost_per_million: config.input_cost_per_million ?? "",
      output_cost_per_million: config.output_cost_per_million ?? "",
    });
    setSecret("");
    setModels([]);
    setResult(null);
    setMessage("");
    setError("");
  }
  async function persist(requireModel: boolean) {
    if (
      requireModel &&
      provider !== "none" &&
      !String(values.text_model).trim()
    )
      throw new Error(
        "Discover models or enter a supported model identifier before saving.",
      );
    if (
      provider === "openai-compatible" &&
      !String(values.text_base_url).trim()
    )
      throw new Error(
        "Enter the endpoint for your OpenAI-compatible provider.",
      );
    if (
      remote &&
      requireModel &&
      Object.values(prices).some(
        (price) =>
          price === "" || !Number.isFinite(Number(price)) || Number(price) < 0,
      )
    )
      throw new Error(
        "Enter the provider's current input and output token prices so the monthly budget can be enforced.",
      );
    if (provider !== "none" && (secret.trim() || remote)) {
      const previous =
        integrations.find((i) => i.provider === provider)?.config || {};
      const knownPrices = Object.fromEntries(
        Object.entries(prices)
          .filter(([, price]) => price !== "")
          .map(([key, price]) => [key, Number(price)]),
      );
      await api(`/integrations/${provider}`, "PUT", {
        config: { ...previous, ...knownPrices },
        ...(secret.trim() ? { secret: secret.trim() } : {}),
      });
      setSecret("");
    }
    await api("/settings", "PATCH", {
      ...values,
      text_model: String(values.text_model).trim(),
      text_base_url: String(values.text_base_url).trim(),
    });
    reload();
  }
  async function perform(action: "save" | "models" | "test") {
    setBusy(action);
    setError("");
    setMessage("");
    setResult(null);
    try {
      await persist(action !== "models");
      if (action === "models") {
        const response = await api("/ai/answers/models");
        const discovered = items<{ id: string; label?: string }>(response);
        setModels(discovered);
        setMessage(
          discovered.length
            ? `${discovered.length} available models discovered. Select a model and save the configuration.`
            : "This provider returned no models. Check the endpoint and credential, or enter a supported model identifier.",
        );
      } else if (action === "test") {
        const response = await api(
          "/ai/answers/test",
          "POST",
          {},
          undefined,
          300000,
        );
        setResult(response);
        if (
          response.error ||
          response.success === false ||
          response.ok === false ||
          response.quality?.passed === false ||
          response.state === "failed"
        )
          throw new Error(
            response.error ||
              response.message ||
              "The provider did not pass the answer-generation connection test.",
          );
        setMessage(
          "The configured provider returned a response. Review the connection test details below.",
        );
        reload();
      } else {
        setMessage(
          provider === "none"
            ? "Answer LLM disconnected. Open-ended answers will require setup."
            : "Configuration saved. Test the connection to verify this provider and model.",
        );
        await ctx.act("answer-llm-refresh", async () => ({}));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }
  const localModels = status.local?.models || [];
  return (
    <>
      <div className="settings-row">
        <div>
          <strong>
            {status.configured
              ? "Answer model configured"
              : "Answer model setup required"}
          </strong>
          <p>{status.message}</p>
        </div>
        <Tag tone={status.configured ? "green" : "amber"}>
          {status.configured ? "Configured" : "Setup required"}
        </Tag>
      </div>
      <div className="info-card compact">
        <ShieldCheck size={20} />
        <p>
          Relevant verified facts, the exact question and role context are sent
          to the selected provider. API keys are stored in macOS Keychain.
          Connection tests use a synthetic prompt without candidate information.
        </p>
      </div>
      {status.local?.available && localModels.length > 0 && (
        <div className="panel-copy">
          <strong>Local Ollama detected</strong>
          <div className="panel-actions">
            {localModels.map((model) => {
              const id = typeof model === "string" ? model : model.id;
              return (
                <Button
                  secondary
                  key={id}
                  disabled={!!busy}
                  onClick={() => selectProvider("ollama", id)}
                >
                  <Cpu size={14} />
                  Use {typeof model === "string" ? id : model.label || id}
                </Button>
              );
            })}
          </div>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void perform("save");
        }}
      >
        <fieldset
          disabled={!!busy}
          style={{ border: 0, padding: 0, margin: 0 }}
        >
          <div className="form-fields answer-llm-fields">
            <label className="field">
              <span>Answer provider</span>
              <select
                value={provider}
                onChange={(e) => selectProvider(e.target.value)}
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            {provider !== "none" && (
              <>
                <label className="field">
                  <span>Answer model</span>
                  <input
                    aria-label="Answer model"
                    list="answer-llm-models"
                    value={values.text_model}
                    onChange={(e) => change("text_model", e.target.value)}
                    placeholder="Discover available models or enter a model identifier"
                    autoComplete="off"
                  />
                  <datalist id="answer-llm-models">
                    {models.map((model) => (
                      <option key={model.id} value={model.id}>
                        {model.label || model.id}
                      </option>
                    ))}
                  </datalist>
                  <small>
                    Choose a model suited to reliable reasoning and structured
                    answers.
                  </small>
                </label>
                <label className="field">
                  <span>
                    Provider endpoint
                    {provider === "openai-compatible" ? " *" : " (optional)"}
                  </span>
                  <input
                    aria-label="Provider endpoint"
                    type="url"
                    value={values.text_base_url}
                    onChange={(e) => change("text_base_url", e.target.value)}
                    placeholder="Provider default when blank"
                  />
                  <small>
                    Custom remote endpoints require HTTPS. Ollama uses a local
                    endpoint.
                  </small>
                </label>
                {remote && (
                  <label className="field">
                    <span>API key</span>
                    <input
                      aria-label="API key"
                      type="password"
                      value={secret}
                      onChange={(e) => setSecret(e.target.value)}
                      autoComplete="new-password"
                      spellCheck={false}
                    />
                    <small>
                      {provider === status.provider && status.key_present
                        ? "A credential is already stored. Leave blank to retain it."
                        : "Enter your provider credential. It is sent only to the local Keychain service."}
                    </small>
                  </label>
                )}
                <label className="field">
                  <span>Temperature</span>
                  <input
                    aria-label="Temperature"
                    type="number"
                    min={0}
                    max={2}
                    step={0.05}
                    value={values.text_temperature}
                    required
                    onChange={(e) =>
                      change("text_temperature", Number(e.target.value))
                    }
                  />
                  <small>Lower values favour stable, precise answers.</small>
                </label>
                {provider === "openai" && (
                  <label className="field checkbox-field">
                    <span>Model supports temperature</span>
                    <input
                      type="checkbox"
                      checked={values.text_temperature_supported}
                      onChange={(e) =>
                        change("text_temperature_supported", e.target.checked)
                      }
                    />
                    <small>
                      Turn off when the selected reasoning model requires its
                      default temperature.
                    </small>
                  </label>
                )}
                <label className="field">
                  <span>Maximum output tokens</span>
                  <input
                    type="number"
                    min={256}
                    max={32000}
                    step={1}
                    value={values.text_max_output_tokens}
                    required
                    onChange={(e) =>
                      change("text_max_output_tokens", Number(e.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Request timeout (seconds)</span>
                  <input
                    type="number"
                    min={5}
                    max={300}
                    step={1}
                    value={values.text_timeout_seconds}
                    required
                    onChange={(e) =>
                      change("text_timeout_seconds", Number(e.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Retry attempts</span>
                  <input
                    aria-label="Retry attempts"
                    type="number"
                    min={0}
                    max={5}
                    step={1}
                    value={values.text_retry_attempts}
                    required
                    onChange={(e) =>
                      change("text_retry_attempts", Number(e.target.value))
                    }
                  />
                  <small>Transient API failures retry within this limit.</small>
                </label>
                <label className="field">
                  <span>Concurrent question generation</span>
                  <input
                    type="number"
                    min={1}
                    max={6}
                    step={1}
                    value={values.text_concurrency}
                    required
                    onChange={(e) =>
                      change("text_concurrency", Number(e.target.value))
                    }
                  />
                </label>
                <label className="field">
                  <span>Fallback answer model (optional)</span>
                  <input
                    aria-label="Fallback answer model"
                    list="answer-llm-models"
                    value={values.text_fallback_model}
                    onChange={(e) =>
                      change("text_fallback_model", e.target.value)
                    }
                  />
                  <small>
                    A supported model from this provider can be tried after a
                    generation failure.
                  </small>
                </label>
                <label className="field checkbox-field">
                  <span>Review generated answers for quality</span>
                  <input
                    type="checkbox"
                    checked={values.text_quality_review}
                    onChange={(e) =>
                      change("text_quality_review", e.target.checked)
                    }
                  />
                </label>
                {remote && (
                  <>
                    <label className="field">
                      <span>Monthly AI usage limit (USD)</span>
                      <input
                        aria-label="Monthly AI usage limit (USD)"
                        type="number"
                        min={0}
                        step={0.01}
                        value={values.monthly_budget}
                        required
                        onChange={(e) =>
                          change("monthly_budget", Number(e.target.value))
                        }
                      />
                      <small>
                        Zero blocks paid remote inference. This limit is shared
                        with other remote AI usage.
                      </small>
                    </label>
                    {(["input", "output"] as const).map((direction) => (
                      <label className="field" key={direction}>
                        <span>
                          {label(direction)} price per million tokens (USD)
                        </span>
                        <input
                          aria-label={`${label(direction)} price per million tokens (USD)`}
                          type="number"
                          min={0}
                          step="any"
                          value={prices[`${direction}_cost_per_million`]}
                          onChange={(e) =>
                            setPrices((p) => ({
                              ...p,
                              [`${direction}_cost_per_million`]:
                                e.target.value === ""
                                  ? ""
                                  : Number(e.target.value),
                            }))
                          }
                        />
                        <small>
                          Use the selected model's current published rate.
                        </small>
                      </label>
                    ))}
                  </>
                )}
              </>
            )}
          </div>
          <div className="panel-actions">
            <Button type="submit" loading={busy === "save"}>
              <KeyRound size={15} />
              Save answer configuration
            </Button>
            {provider !== "none" && (
              <>
                <Button
                  type="button"
                  secondary
                  loading={busy === "models"}
                  onClick={() => void perform("models")}
                >
                  <RefreshCw size={15} />
                  Save and discover models
                </Button>
                <Button
                  type="button"
                  secondary
                  loading={busy === "test"}
                  onClick={() => void perform("test")}
                >
                  <Zap size={15} />
                  Save and test connection
                </Button>
              </>
            )}
          </div>
        </fieldset>
      </form>
      {busy && (
        <p className="panel-copy" role="status">
          {busy === "models"
            ? "Discovering available models…"
            : busy === "test"
              ? "Testing the configured answer model…"
              : "Saving securely…"}
        </p>
      )}
      {error && <ErrorBox error={error} />}
      {message && (
        <p className="panel-copy" role="status">
          {message}
        </p>
      )}
      {result && (
        <JsonDetails title="Answer model connection test" data={result} open />
      )}
      {status.usage && (
        <details>
          <summary>Current answer LLM usage</summary>
          <KeyValues data={status.usage} />
        </details>
      )}
    </>
  );
}
