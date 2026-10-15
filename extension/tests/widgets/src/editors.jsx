// Rich-text editors a cover-letter field is made of: REAL Quill 2 (contenteditable .ql-editor kept in sync by a MutationObserver and
// its own delta model), a contenteditable that mirrors itself into React state on `input`, and contenteditable="plaintext-only".
import Quill from "quill";
import "quill/dist/quill.snow.css";
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";

function App() {
  const quillRef = useRef(null);
  const [mirror, setMirror] = useState("");
  const [plain, setPlain] = useState("");
  window.__state = () => ({
    quill: quillRef.current ? quillRef.current.getText().replace(/\n$/, "") : "",
    quillHtml: quillRef.current ? quillRef.current.root.innerHTML : "",
    mirror, plain,
  });
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>Rich text editors demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <div className="field">
        <div className="label" id="quill-label">Cover letter (Quill)</div>
        <div ref={(el) => { if (el && !quillRef.current) { quillRef.current = new Quill(el, { theme: "snow", placeholder: "Write here..." }); quillRef.current.root.setAttribute("aria-labelledby", "quill-label"); } }} id="quill" />
      </div>
      <div className="field">
        <div className="label" id="mirror-label">Why this role? (contenteditable mirrored into React state)</div>
        <div id="mirror" className="editable" contentEditable suppressContentEditableWarning role="textbox" aria-multiline="true" aria-labelledby="mirror-label"
          onInput={(e) => setMirror(e.currentTarget.innerText)} />
      </div>
      <div className="field">
        <div className="label" id="plain-label">Anything else? (contenteditable plaintext-only)</div>
        <div id="plain" className="editable" contentEditable="plaintext-only" suppressContentEditableWarning role="textbox" aria-multiline="true" aria-labelledby="plain-label"
          onInput={(e) => setPlain(e.currentTarget.innerText)} />
      </div>
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
