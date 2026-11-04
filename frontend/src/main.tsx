import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// A render error must never leave the window blank: show what failed and offer a reload.
function describe(error: unknown) {
  return error instanceof Error
    ? `${error.name}: ${error.message}\n${error.stack ?? ""}`
    : String(error);
}
function Crash({ detail }: { detail: string }) {
  return (
    <main style={{ padding: 32, fontFamily: "system-ui", color: "inherit" }}>
      <h1 style={{ fontSize: 20 }}>Meridian could not draw this screen</h1>
      <p>Your data is safe. Reload, and if this repeats, send this detail:</p>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{detail}</pre>
      <button type="button" onClick={() => location.reload()}>Reload</button>
    </main>
  );
}
class Boundary extends React.Component<
  { children: React.ReactNode },
  { detail: string }
> {
  state = { detail: "" };
  static getDerivedStateFromError(error: unknown) {
    return { detail: describe(error) };
  }
  render() {
    return this.state.detail ? <Crash detail={this.state.detail} /> : this.props.children;
  }
}
const root = ReactDOM.createRoot(document.getElementById("root")!);
// Errors thrown before React mounts (module load, startup) never reach the boundary.
window.addEventListener("error", (event) => {
  if (!document.getElementById("root")?.hasChildNodes())
    root.render(<Crash detail={describe(event.error ?? event.message)} />);
});
root.render(
  <React.StrictMode>
    <Boundary>
      <App />
    </Boundary>
  </React.StrictMode>,
);
