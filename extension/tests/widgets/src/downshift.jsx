// REAL Downshift 9 hooks: useCombobox (input + toggle button, listbox rendered only while open) and useSelect (button that is
// itself the combobox, options appear on open). Both are the "headless" pattern many career sites use.
import { useCombobox, useSelect } from "downshift";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { COUNTRY_NAMES } from "./_data.js";

const levels = ["Junior", "Mid-level", "Senior", "Staff"];

function CountryCombobox({ onValue }) {
  const [items, setItems] = useState(COUNTRY_NAMES.slice(0, 25));
  const { isOpen, getLabelProps, getMenuProps, getInputProps, getToggleButtonProps, highlightedIndex, getItemProps } = useCombobox({
    items,
    itemToString: (item) => item ?? "",
    onInputValueChange: ({ inputValue }) => setItems(COUNTRY_NAMES.filter((c) => c.toLowerCase().startsWith((inputValue ?? "").toLowerCase())).slice(0, 25)),
    onSelectedItemChange: ({ selectedItem }) => onValue("country", selectedItem),
  });
  return (
    <div className="field">
      <label className="label" {...getLabelProps()}>Country of residence</label>
      <div className="row"><input {...getInputProps({ name: "country" })} /><button type="button" {...getToggleButtonProps()} aria-label="toggle menu">▾</button></div>
      <ul {...getMenuProps()} className="menu">
        {isOpen && items.map((item, index) => (
          <li key={item} {...getItemProps({ item, index })} className={highlightedIndex === index ? "option hl" : "option"}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function LevelSelect({ onValue }) {
  const { isOpen, selectedItem, getToggleButtonProps, getLabelProps, getMenuProps, highlightedIndex, getItemProps } = useSelect({
    items: levels, onSelectedItemChange: ({ selectedItem: s }) => onValue("level", s),
  });
  return (
    <div className="field">
      <label className="label" {...getLabelProps()}>Seniority level</label>
      <div className="row"><button type="button" className="control" {...getToggleButtonProps({ id: "level" })}>{selectedItem ?? "Select a level"}</button></div>
      <ul {...getMenuProps()} className="menu">
        {isOpen && levels.map((item, index) => (
          <li key={item} {...getItemProps({ item, index })} className={highlightedIndex === index ? "option hl" : "option"}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function App() {
  const [state, setState] = useState({});
  const onValue = (k, v) => setState((s) => ({ ...s, [k]: v }));
  window.__state = () => JSON.parse(JSON.stringify(state));
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>Downshift demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <CountryCombobox onValue={onValue} />
      <LevelSelect onValue={onValue} />
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
