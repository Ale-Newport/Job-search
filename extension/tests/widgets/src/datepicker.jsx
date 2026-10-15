// REAL react-datepicker 9 (typed dates parsed by date-fns, popup calendar) next to native date and month inputs. Day-less answers
// ("June 2024") are the interesting case: month pickers and <input type=month>.
import DatePicker from "react-datepicker";
import "react-datepicker/dist/react-datepicker.css";
import { useState } from "react";
import { createRoot } from "react-dom/client";

const iso = (d) => (d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : null);
const ym = (d) => (d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}` : null);

function App() {
  const [start, setStart] = useState(null);
  const [graduated, setGraduated] = useState(null);
  const [birth, setBirth] = useState(null);
  const [native, setNative] = useState({ available: "", since: "" });
  window.__state = () => ({ start: iso(start), graduated: ym(graduated), birth: iso(birth), native });
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>react-datepicker demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <div className="field">
        <label className="label" htmlFor="start">Earliest start date</label>
        <DatePicker id="start" name="start" selected={start} onChange={setStart} dateFormat="yyyy-MM-dd" placeholderText="YYYY-MM-DD" autoComplete="off" />
      </div>
      <div className="field">
        <label className="label" htmlFor="graduated">Graduation month</label>
        <DatePicker id="graduated" name="graduated" selected={graduated} onChange={setGraduated} dateFormat="MM/yyyy" showMonthYearPicker placeholderText="MM/YYYY" autoComplete="off" />
      </div>
      <div className="field">
        <label className="label" htmlFor="birth">Date of birth (dd/MM/yyyy)</label>
        <DatePicker id="birth" name="birth" selected={birth} onChange={setBirth} dateFormat="dd/MM/yyyy" placeholderText="DD/MM/YYYY" autoComplete="off" showYearDropdown scrollableYearDropdown />
      </div>
      <div className="field">
        <label className="label" htmlFor="available">Available from (native date)</label>
        <input id="available" name="available" type="date" value={native.available} onChange={(e) => setNative((s) => ({ ...s, available: e.target.value }))} />
      </div>
      <div className="field">
        <label className="label" htmlFor="since">Working in the field since (native month)</label>
        <input id="since" name="since" type="month" value={native.since} onChange={(e) => setNative((s) => ({ ...s, since: e.target.value }))} />
      </div>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
