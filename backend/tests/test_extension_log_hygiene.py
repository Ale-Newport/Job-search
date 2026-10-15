"""SEC-13: the addresses Meridian researches and the company names it searches for never reach ``logs/backend.log``.

httpx logs every request it makes at INFO, address and query string included. The assistant fetches employer pages and (when
the user allows it) searches the web for a company: with INFO logging on, the backend's log file would list every site
researched. ``main.run()`` is the one place that configures logging for the whole sidecar, so it is the one place that has to
quiet httpx and httpcore for every user of them (research, discovery, the mail providers, the model clients)."""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import jobagent


CHILD = textwrap.dedent('''
    import json, logging, os, sys, threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    os.environ["MERIDIAN_API_TOKEN"] = "test-only-token"
    import httpx
    import jobagent.main as main


    class Quiet(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass


    def fake_run(app, **kwargs):
        # what the sidecar does for the rest of its life: fetch pages with httpx, while the root logger is at INFO
        server = HTTPServer(("127.0.0.1", 0), Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        httpx.get(f"http://127.0.0.1:{port}/careers/secret-company-name?q=Secret+Company+Ltd")
        server.shutdown()
        logging.getLogger("meridian").info("sidecar-alive")
        logging.getLogger("httpx").warning("httpx-warning-kept")
        print("LEVELS", json.dumps({name: logging.getLogger(name).level for name in ("httpx", "httpcore")}), file=sys.stdout)


    main.uvicorn.run = fake_run
    main.create_app = lambda **kwargs: object()
    main.run()
''')


def run_sidecar_logging():
    root = Path(jobagent.__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=120,
                          env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin", "MERIDIAN_TEST": "1",
                               "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"})
    assert done.returncode == 0, done.stderr[-2000:]
    levels = json.loads(next(line for line in done.stdout.splitlines() if line.startswith("LEVELS")).split(" ", 1)[1])
    return done.stderr, levels


def test_the_address_of_every_page_researched_stays_out_of_the_backend_log():
    log, levels = run_sidecar_logging()
    assert "secret-company-name" not in log and "Secret+Company" not in log and "Secret Company" not in log
    assert "HTTP Request" not in log
    assert levels == {"httpx": 30, "httpcore": 30}  # WARNING: a failure is still logged, a successful request is not


def test_the_log_is_still_configured_and_only_the_chatty_loggers_are_quieted():
    """The sidecar keeps logging at INFO as a whole, in its JSON format; an httpx warning or error is still written."""
    log, _ = run_sidecar_logging()
    lines = [json.loads(line) for line in log.splitlines() if line.strip()]
    assert any(line.get("event") == "sidecar-alive" and line.get("logger") == "meridian" for line in lines), log
    assert any(line.get("event") == "httpx-warning-kept" and line.get("logger") == "httpx" for line in lines), log
