// REAL Material UI 5 (controlled): Select (div role=combobox + Menu in a Popover portal), Autocomplete (typing filters, popper listbox),
// TextField (controlled input + helper text), Checkbox, Radio group, Switch and NativeSelect.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import Autocomplete from "@mui/material/Autocomplete";
import Checkbox from "@mui/material/Checkbox";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import FormGroup from "@mui/material/FormGroup";
import FormLabel from "@mui/material/FormLabel";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import NativeSelect from "@mui/material/NativeSelect";
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
import Select from "@mui/material/Select";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import { CITIES, COUNTRY_NAMES } from "./_data.js";

function App() {
  const [s, setS] = useState({ first: "", email: "", country: "", city: null, auth: "", alerts: false, remote: false, notice: "", bio: "", langs: [], consent: false });
  const set = (k) => (v) => setS((p) => ({ ...p, [k]: v }));
  window.__state = () => JSON.parse(JSON.stringify(s));
  return (
    <form onSubmit={(e) => e.preventDefault()} className="mui-demo">
      <h1>Material UI demo application</h1>
      <div className="field">
        <TextField id="first" name="first" label="First name" value={s.first} onChange={(e) => set("first")(e.target.value)} size="small" fullWidth required helperText="As on your passport" />
      </div>
      <div className="field">
        <TextField id="email" name="email" type="email" label="Email address" value={s.email} onChange={(e) => set("email")(e.target.value)} size="small" fullWidth />
      </div>
      <div className="field">
        <FormControl fullWidth size="small">
          <InputLabel id="country-label">Country</InputLabel>
          <Select labelId="country-label" id="country" name="country" label="Country" value={s.country} onChange={(e) => set("country")(e.target.value)}>
            {COUNTRY_NAMES.slice(0, 40).concat(["United Kingdom"]).map((c) => <MenuItem key={c} value={c.toLowerCase().replace(/ /g, "-")}>{c}</MenuItem>)}
          </Select>
        </FormControl>
      </div>
      <div className="field">
        <Autocomplete
          id="city" options={CITIES} value={s.city} onChange={(_, v) => set("city")(v)} size="small"
          renderInput={(params) => <TextField {...params} name="city" label="Where are you based?" />}
        />
      </div>
      <div className="field">
        <FormControl fullWidth size="small">
          <InputLabel htmlFor="notice" variant="standard">Notice period (native select)</InputLabel>
          <NativeSelect inputProps={{ id: "notice", name: "notice" }} value={s.notice} onChange={(e) => set("notice")(e.target.value)}>
            <option value="">Select</option><option value="now">Immediately</option><option value="1m">1 month</option><option value="3m">3 months</option>
          </NativeSelect>
        </FormControl>
      </div>
      <FormControl className="field">
        <FormLabel id="auth-label">Are you legally authorised to work in the UK?</FormLabel>
        <RadioGroup aria-labelledby="auth-label" name="auth" value={s.auth} onChange={(e) => set("auth")(e.target.value)}>
          <FormControlLabel value="yes" control={<Radio />} label="Yes" />
          <FormControlLabel value="no" control={<Radio />} label="No" />
        </RadioGroup>
      </FormControl>
      <div className="field">
        <FormControlLabel control={<Checkbox name="alerts" checked={s.alerts} onChange={(e) => set("alerts")(e.target.checked)} />} label="Send me job alerts" />
      </div>
      <FormControl className="field" component="fieldset">
        <FormLabel component="legend">Languages you work in</FormLabel>
        <FormGroup>
          {["English", "Spanish", "German"].map((l) => (
            // a typical handler: the next array is built from the one rendered last (stale until React re-renders)
            <FormControlLabel key={l} label={l} control={<Checkbox name="langs" value={l} checked={s.langs.includes(l)}
              onChange={() => set("langs")(s.langs.includes(l) ? s.langs.filter((x) => x !== l) : [...s.langs, l])} />} />
          ))}
        </FormGroup>
      </FormControl>
      <div className="field">
        <FormControlLabel control={<Switch name="remote" checked={s.remote} onChange={(e) => set("remote")(e.target.checked)} />} label="Open to remote work" />
      </div>
      <div className="field">
        <TextField id="bio" name="bio" label="Short bio" multiline minRows={3} value={s.bio} onChange={(e) => set("bio")(e.target.value)} fullWidth inputProps={{ maxLength: 300 }}
          helperText={`${s.bio.length}/300`} />
      </div>
      <div className="field">
        <FormControlLabel control={<Checkbox name="consent" checked={s.consent} onChange={(e) => set("consent")(e.target.checked)} />} label="I agree to the privacy policy" />
      </div>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
