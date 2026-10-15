// REAL react-select 5 (what Greenhouse job-boards ships, classNamePrefix="select") in every flavour the assistant meets:
// single, multi, creatable, async (type to search), virtualised 1,000 options (react-window), grouped, unprefixed default
// markup, not searchable, options that arrive after the menu opened and options that are re-created while it is open.
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import Select, { components } from "react-select";
import AsyncSelect from "react-select/async";
import CreatableSelect from "react-select/creatable";
import { FixedSizeList } from "react-window";
import { BIG_COUNTRIES, CITIES, COUNTRY_NAMES, delay, opt } from "./_data.js";

const codes = { Spain: "es", "United Kingdom": "gb", "United States": "us" };
const countries = COUNTRY_NAMES.slice(0, 14).concat(["Spain", "United Kingdom", "United States"]).map((c) => opt(c, codes[c] ?? c.slice(0, 3).toLowerCase()));
const yesNo = [opt("Yes"), opt("No")];
const languages = ["English", "Spanish", "French", "German", "Portuguese"].map((l) => opt(l));
const modes = ["Remote", "Hybrid", "On-site"].map((l) => opt(l));
const levels = ["Junior", "Mid-level", "Senior", "Staff"].map((l) => opt(l));
const sources = ["LinkedIn", "Company website", "Referral", "Job board", "Other"].map((l) => opt(l));
const zones = ["Europe/London", "Europe/Madrid", "America/New_York", "Asia/Tokyo"].map((l) => opt(l));
const offices = [
  { label: "Europe", options: [opt("London office"), opt("Madrid office"), opt("Berlin office")] },
  { label: "Americas", options: [opt("New York office"), opt("Toronto office")] },
];
const bigOptions = BIG_COUNTRIES.map((c) => opt(c));

function VirtualMenuList({ children, maxHeight }) {
  const rows = Array.isArray(children) ? children : [children];
  return (
    <FixedSizeList height={Math.min(maxHeight, rows.length * 36)} itemCount={rows.length} itemSize={36} width="100%">
      {({ index, style }) => <div style={style}>{rows[index]}</div>}
    </FixedSizeList>
  );
}

function Field({ id, label, required, children }) {
  return (
    <div className="field select-shell">
      <label className="label" id={`${id}-label`} htmlFor={id}>{label}{required ? <span className="req">*</span> : null}</label>
      {children}
    </div>
  );
}

// options are re-created (new keys, so new DOM nodes) every 150 ms while the menu is open
const RotatingOption = (props) => <components.Option key={window.__tick} {...props} />;
const rotatingComponents = { Option: RotatingOption };
function RotatingSelect({ id }) {
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!open) return undefined;
    const t = setInterval(() => { window.__tick = (window.__tick || 0) + 1; setTick((n) => n + 1); }, 150);
    return () => clearInterval(t);
  }, [open]);
  return (
    <Select
      inputId={id} instanceId={id} name={id} classNamePrefix="select" options={sources} placeholder="Select..." aria-labelledby={`${id}-label`}
      onMenuOpen={() => setOpen(true)} onMenuClose={() => setOpen(false)} onChange={(v) => window.__set(id, v)} components={rotatingComponents}
    />
  );
}

function LateSelect({ id }) {
  const [options, setOptions] = useState([]);
  const [loading, setLoading] = useState(false);
  return (
    <Select
      inputId={id} instanceId={id} name={id} classNamePrefix="select" options={options} isLoading={loading} placeholder="Select..." aria-labelledby={`${id}-label`}
      onMenuOpen={() => { setLoading(true); setTimeout(() => { setOptions(zones); setLoading(false); }, 500); }}
      onChange={(v) => window.__set(id, v)}
    />
  );
}

function App() {
  const [values, setValues] = useState({});
  const ref = useRef(values);
  ref.current = values;
  window.__set = (key, v) => setValues((s) => ({ ...s, [key]: v == null ? null : Array.isArray(v) ? v.map((x) => x.value) : v.value }));
  window.__state = () => JSON.parse(JSON.stringify(ref.current));
  const sel = (id, extra) => ({ inputId: id, instanceId: id, name: id, classNamePrefix: "select", placeholder: "Select...", "aria-labelledby": `${id}-label`, onChange: (v) => window.__set(id, v), ...extra });
  return (
    <form className="application--form" onSubmit={(e) => e.preventDefault()}>
      <h1>Senior Engineer: react-select demo</h1>
      <div className="field"><label className="label" htmlFor="preferred_name">Preferred name</label>
        <input id="preferred_name" name="preferred_name" type="text" onChange={(e) => window.__set("preferred_name", { value: e.target.value })} /></div>
      <Field id="country" label="Country" required><Select {...sel("country", { options: countries })} /></Field>
      <Field id="auth" label="Are you legally authorized to work in the country where this job is located?" required><Select {...sel("auth", { options: yesNo })} /></Field>
      <Field id="languages" label="Languages you speak"><Select {...sel("languages", { options: languages, isMulti: true })} /></Field>
      <Field id="city" label="Current city (type your own if missing)"><CreatableSelect {...sel("city", { options: CITIES.slice(0, 3).map((c) => opt(c)) })} /></Field>
      <Field id="based" label="Where are you based?">
        <AsyncSelect {...sel("based", { cacheOptions: false, defaultOptions: false, loadOptions: async (q) => { await delay(300); return CITIES.filter((c) => c.toLowerCase().includes(q.toLowerCase())).map((c) => opt(c)); } })} />
      </Field>
      <Field id="nationality" label="Nationality (1,000 options)"><Select {...sel("nationality", { options: bigOptions, components: { MenuList: VirtualMenuList } })} /></Field>
      <Field id="office" label="Preferred office"><Select {...sel("office", { options: offices })} /></Field>
      <Field id="seniority" label="Seniority (default react-select markup)">
        <Select inputId="seniority" instanceId="seniority" name="seniority" options={levels} aria-labelledby="seniority-label" onChange={(v) => window.__set("seniority", v)} />
      </Field>
      <Field id="workmode" label="Preferred work mode (not searchable)"><Select {...sel("workmode", { options: modes, isSearchable: false })} /></Field>
      <Field id="timezone" label="Time zone (options load after opening)"><LateSelect id="timezone" /></Field>
      <Field id="source" label="How did you hear about us? (options re-render while open)"><RotatingSelect id="source" /></Field>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
