// REAL react-dropzone 20: the file input is hidden (display:none) and the visible UI is a dashed drop area plus a button
// that calls open(). Dropped/selected files are read in onDrop and shown; the form posts them.
import { useCallback, useState } from "react";
import { createRoot } from "react-dom/client";
import { useDropzone } from "react-dropzone";

function Zone({ name, title, accept, multiple, onFiles, files, asLabel }) {
  const Root = asLabel ? "label" : "div";
  const onDrop = useCallback((accepted) => onFiles(name, accepted), [name, onFiles]);
  const { getRootProps, getInputProps, open, isDragActive } = useDropzone({ onDrop, accept, multiple, noClick: true, noKeyboard: true });
  return (
    <div className="field">
      <label className="label" id={`${name}-label`}>{title}</label>
      <Root {...getRootProps({ className: `dropzone${isDragActive ? " active" : ""}` })}>
        <input {...getInputProps({ name })} aria-labelledby={`${name}-label`} />
        <p>Drag and drop your file here</p>
        <button type="button" onClick={open}>Choose file</button>
        <ul className="files">{files.map((f) => <li key={f}>{f}</li>)}</ul>
      </Root>
    </div>
  );
}

function App() {
  const [files, setFiles] = useState({ resume: [], cover: [] });
  const onFiles = useCallback((name, list) => setFiles((s) => ({ ...s, [name]: list.map((f) => f.name) })), []);
  window.__state = () => JSON.parse(JSON.stringify(files));
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <h1>react-dropzone demo application</h1>
      <div className="field"><label className="label" htmlFor="nick">Preferred name</label><input id="nick" name="nick" type="text" /></div>
      <Zone name="resume" title="Resume / CV" accept={{ "application/pdf": [".pdf"] }} multiple={false} onFiles={onFiles} files={files.resume} />
      <Zone asLabel name="cover" title="Cover letter" accept={{ "application/pdf": [".pdf"], "text/plain": [".txt"] }} multiple={false} onFiles={onFiles} files={files.cover} />
      <button type="submit">Submit application</button>
    </form>
  );
}

createRoot(document.getElementById("root")).render(<App />);
