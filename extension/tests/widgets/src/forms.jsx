// Form libraries and input behaviours that rewrite what they are given: react-hook-form (uncontrolled inputs registered by ref),
// Formik (controlled), number / currency masks, a textarea with a live character counter and maxLength, an input that
// re-formats on every input, and an input that validates on blur and RE-RENDERS (remounting the element) when invalid.
import { Field, Form, Formik } from "formik";
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useForm } from "react-hook-form";

const money = (raw) => {
  const digits = String(raw).replace(/[^\d]/g, "");
  return digits ? Number(digits).toLocaleString("en-US") : "";
};

function Hook() {
  const { register, getValues, watch } = useForm({ defaultValues: { first: "", last: "", about: "" } });
  window.__rhf = () => getValues();
  watch();
  return (
    <fieldset className="box">
      <legend>react-hook-form (uncontrolled inputs registered by ref)</legend>
      <div className="field"><label className="label" htmlFor="rhf_first">First name</label><input id="rhf_first" type="text" {...register("first", { required: true })} /></div>
      <div className="field"><label className="label" htmlFor="rhf_last">Last name</label><input id="rhf_last" type="text" {...register("last")} /></div>
      <div className="field"><label className="label" htmlFor="rhf_about">About you</label><textarea id="rhf_about" {...register("about")} /></div>
    </fieldset>
  );
}

function FormikPart() {
  return (
    <Formik initialValues={{ email: "", city: "" }} onSubmit={() => {}}>
      {({ values }) => {
        window.__formik = () => values;
        return (
          <Form>
            <fieldset className="box">
              <legend>Formik (controlled Field)</legend>
              <div className="field"><label className="label" htmlFor="fk_email">Email</label><Field id="fk_email" name="email" type="email" /></div>
              <div className="field"><label className="label" htmlFor="fk_city">City</label><Field id="fk_city" name="city" type="text" /></div>
            </fieldset>
          </Form>
        );
      }}
    </Formik>
  );
}

function Masks() {
  const [salary, setSalary] = useState("");
  const [postcode, setPostcode] = useState("");
  const [initials, setInitials] = useState("");
  const [bio, setBio] = useState("");
  const [years, setYears] = useState("");
  const [site, setSite] = useState("");
  const [siteError, setSiteError] = useState("");
  const [siteKey, setSiteKey] = useState(0);
  const siteValue = useRef("");
  const [cust, setCust] = useState("");
  const [lateOptions, setLateOptions] = useState([]);
  const [late, setLate] = useState("");
  window.__masks = () => ({ salary, postcode, initials, bio, years, site, siteKey, late, cust });
  return (
    <fieldset className="box">
      <legend>Masks, counters and re-rendering inputs</legend>
      <div className="field"><label className="label" htmlFor="salary">Expected salary (GBP, mask 1,234,567)</label>
        <input id="salary" type="text" inputMode="numeric" value={salary} onChange={(e) => setSalary(money(e.target.value))} /></div>
      <div className="field"><label className="label" htmlFor="postcode">Postcode (upper-cased on input)</label>
        <input id="postcode" type="text" value={postcode} onChange={(e) => setPostcode(e.target.value.toUpperCase())} /></div>
      <div className="field"><label className="label" htmlFor="initials">Initials (letters only, max 4)</label>
        <input id="initials" type="text" value={initials} onChange={(e) => setInitials(e.target.value.replace(/[^a-z]/gi, "").slice(0, 4))} /></div>
      <div className="field"><label className="label" htmlFor="years">Years of experience</label>
        <input id="years" type="number" min="0" max="60" value={years} onChange={(e) => setYears(e.target.value)} /></div>
      <div className="field"><label className="label" htmlFor="bio">Short bio (max 140)</label>
        <textarea id="bio" maxLength={140} value={bio} onChange={(e) => setBio(e.target.value)} />
        <div className="counter" id="bio_counter">{bio.length}/140</div></div>
      <div className="field"><label className="label" htmlFor="cust">Customer number (the page drops a leading 49)</label>
        <input id="cust" type="text" value={cust} onChange={(e) => setCust(e.target.value.replace(/^49/, ""))} /></div>
      <div className="field"><label className="label" htmlFor="late">Region (options are loaded when the select gets focus)</label>
        <select id="late" name="late" value={late} onChange={(e) => setLate(e.target.value)}
          onFocus={() => { if (!lateOptions.length) setTimeout(() => setLateOptions(["North", "South", "East", "West"]), Number(new URLSearchParams(location.search).get("late") || 300)); }}>
          <option value="">Select a region</option>
          {lateOptions.map((o) => <option key={o} value={o.toLowerCase()}>{o}</option>)}
        </select></div>
      <div className="field"><label className="label" htmlFor="site">Website (validated on blur, re-rendered when invalid)</label>
        <input key={siteKey} id="site" type="text" defaultValue={site} onChange={(e) => { siteValue.current = e.target.value; setSite(e.target.value); }}
          onBlur={(e) => { const v = e.target.value; if (v && !/^https?:\/\//.test(v)) { setSiteError("Must start with http:// or https://"); setSiteKey((k) => k + 1); } else setSiteError(""); }} />
        <div className="error" id="site_error">{siteError}</div></div>
    </fieldset>
  );
}

function App() {
  return (
    <div>
      <h1>Form libraries and masks demo application</h1>
      <form onSubmit={(e) => e.preventDefault()}>
        <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
        <Hook />
        <Masks />
        <button type="submit">Submit application</button>
      </form>
      <FormikPart />
    </div>
  );
}

window.__state = () => ({ rhf: window.__rhf(), formik: window.__formik(), masks: window.__masks() });
createRoot(document.getElementById("root")).render(<App />);
