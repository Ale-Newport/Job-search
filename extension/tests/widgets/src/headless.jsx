// REAL Headless UI 2 (Tailwind UI's accessible primitives): Combobox (typing filters), Listbox (button + popup in a portal),
// RadioGroup rendered as selectable cards, Checkbox and Switch. Options live in portals attached to <body>.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Checkbox, Combobox, ComboboxButton, ComboboxInput, ComboboxOption, ComboboxOptions, Field, Label, Listbox, ListboxButton, ListboxOption, ListboxOptions, Radio, RadioGroup, Switch } from "@headlessui/react";
import { COUNTRY_NAMES } from "./_data.js";

const people = ["Remote", "Hybrid", "On-site"];
const levels = ["Junior", "Mid-level", "Senior", "Staff"];
const plans = [{ id: "full", title: "Full-time", note: "40 hours" }, { id: "part", title: "Part-time", note: "20 hours" }, { id: "contract", title: "Contractor", note: "Flexible" }];

function CountryCombobox({ state, set }) {
  const [query, setQuery] = useState("");
  const shown = query === "" ? COUNTRY_NAMES.slice(0, 30) : COUNTRY_NAMES.filter((c) => c.toLowerCase().includes(query.toLowerCase()));
  return (
    <Field className="field">
      <Label className="label">Country of residence</Label>
      <Combobox value={state.country ?? null} onChange={(v) => set("country", v)} onClose={() => setQuery("")} name="country">
        <div className="relative">
          <ComboboxInput id="country" aria-label="Country of residence" className="control" displayValue={(c) => c ?? ""} onChange={(e) => setQuery(e.target.value)} />
          <ComboboxButton className="chevron" aria-label="Show countries">▾</ComboboxButton>
        </div>
        <ComboboxOptions anchor="bottom start" className="menu" style={{ width: "var(--input-width)" }}>
          {shown.map((c) => <ComboboxOption key={c} value={c} className="option">{c}</ComboboxOption>)}
          {shown.length === 0 ? <div className="option">Nothing found</div> : null}
        </ComboboxOptions>
      </Combobox>
    </Field>
  );
}

function WorkModeListbox({ state, set }) {
  return (
    <Field className="field">
      <Label className="label">Preferred work mode</Label>
      <Listbox value={state.mode ?? null} onChange={(v) => set("mode", v)} name="mode">
        <ListboxButton id="mode" className="control" aria-label="Preferred work mode">{state.mode ?? "Select..."}</ListboxButton>
        <ListboxOptions anchor="bottom start" className="menu" style={{ width: "var(--button-width)" }}>
          {people.map((p) => <ListboxOption key={p} value={p} className="option">{p}</ListboxOption>)}
        </ListboxOptions>
      </Listbox>
    </Field>
  );
}

function SeniorityListbox({ state, set }) {
  return (
    <Field className="field">
      <Label className="label">Seniority</Label>
      <Listbox value={state.level ?? null} onChange={(v) => set("level", v)} name="level">
        <ListboxButton id="level" className="control">{state.level ?? "Choose a level"}</ListboxButton>
        <ListboxOptions anchor="bottom start" className="menu">
          {levels.map((p) => <ListboxOption key={p} value={p} className="option">{p}</ListboxOption>)}
        </ListboxOptions>
      </Listbox>
    </Field>
  );
}

function App() {
  const [state, setState] = useState({ plan: null, alerts: false, remote: false });
  const set = (k, v) => setState((s) => ({ ...s, [k]: v }));
  window.__state = () => JSON.parse(JSON.stringify(state));
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>Headless UI demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <CountryCombobox state={state} set={set} />
      <WorkModeListbox state={state} set={set} />
      <SeniorityListbox state={state} set={set} />
      <fieldset className="field">
        <legend className="label">Employment type</legend>
        <RadioGroup value={state.plan} onChange={(v) => set("plan", v)} name="plan" aria-label="Employment type" className="cards">
          {plans.map((p) => (
            <Radio key={p.id} value={p.id} className="card" aria-label={p.title}><strong>{p.title}</strong><span>{p.note}</span></Radio>
          ))}
        </RadioGroup>
      </fieldset>
      <Field className="field">
        <Checkbox name="alerts" checked={state.alerts} onChange={(v) => set("alerts", v)} className="box" aria-label="Send me job alerts">{state.alerts ? "✓" : ""}</Checkbox>
        <Label className="inline"> Send me job alerts</Label>
      </Field>
      <Field className="field">
        <Switch name="remote" checked={state.remote} onChange={(v) => set("remote", v)} className="switch" aria-label="Open to remote work">{state.remote ? "on" : "off"}</Switch>
        <Label className="inline"> Open to remote work</Label>
      </Field>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
