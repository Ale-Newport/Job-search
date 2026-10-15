// REAL react-phone-number-input 3 (libphonenumber based): a native <select> of countries next to a formatting <input type=tel>.
import "react-phone-number-input/style.css";
import PhoneInput from "react-phone-number-input";
import { useState } from "react";
import { createRoot } from "react-dom/client";

function App() {
  const [national, setNational] = useState();
  const [intl, setIntl] = useState();
  window.__state = () => ({ national: national ?? null, intl: intl ?? null,
    nationalShown: document.querySelector("#rpni_national input[type=tel]").value, intlShown: document.querySelector("#rpni_intl input[type=tel]").value });
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>react-phone-number-input demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <div className="field" id="rpni_national"><label className="label" htmlFor="rpni_national_input">Mobile number (national format)</label>
        <PhoneInput id="rpni_national_input" name="mobile" defaultCountry="GB" value={national} onChange={setNational} international={false} /></div>
      <div className="field" id="rpni_intl"><label className="label" htmlFor="rpni_intl_input">Contact number (international format)</label>
        <PhoneInput id="rpni_intl_input" name="contact" international value={intl} onChange={setIntl} /></div>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
