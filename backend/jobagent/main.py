from __future__ import annotations

import asyncio
import contextlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .backup import export_backup, restore_backup
from .config import APP_NAME, data_directory
from .db import Database, decode_row, now
from .mail import MailService, classify_message, ingest_message, parse_message
from .security import JsonFormatter, RedactingFilter, SecretStore
from .text_ai import TextService

logger = logging.getLogger("meridian")
ALLOWED_ORIGINS = {
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
}
PROVIDERS = {"gmail", "outlook", "imap", "jev", "openai", "anthropic", "gemini", "openai-compatible", "ollama"}


async def notify(title: str, message: str, db=None):
    if db is not None:
        from .core import get_settings

        if not get_settings(db).get("notifications_enabled", True):
            return
    if os.environ.get("MERIDIAN_TEST") or os.environ.get("MERIDIAN_NO_NOTIFICATIONS"):
        return
    script = "on run argv\n display notification (item 2 of argv) with title (item 1 of argv)\nend run"
    try:
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/osascript",
            "-e",
            script,
            title[:100],
            message[:250],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(process.wait(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        logger.info("notification_unavailable")


def create_app(data_dir: Path | None = None, token: str | None = None, secret_store=None, start_scheduler=True):
    root = data_dir or data_directory()
    root = Path(root)
    for child in ("database", "documents", "browser-profile", "cache", "logs", "backups", "models"):
        (root / child).mkdir(parents=True, exist_ok=True, mode=0o700)
    credential = token or os.environ.get("MERIDIAN_API_TOKEN") or secrets.token_urlsafe(32)
    store = secret_store or SecretStore()

    @asynccontextmanager
    async def lifespan(app):
        from .orchestrator import Orchestrator
        from .automation.local_runtime import LayaRuntime
        from .automation.text_runtime import OllamaRuntime

        app.state.automation = Orchestrator(app.state.db, root)
        app.state.laya_runtime = LayaRuntime(root)
        app.state.ollama_runtime = OllamaRuntime(root)
        scheduler = asyncio.create_task(schedule(app)) if start_scheduler else None

        async def start_local_model():
            from .core import get_settings

            settings = get_settings(app.state.db)
            if (
                not settings.get("tracking_first", True)
                and settings.get("browser_engine") in ("laya", "hybrid")
                and settings.get("laya_autostart", True)
            ):
                state = await app.state.laya_runtime.status()
                if state["installed"]:
                    await app.state.laya_runtime.start()

        async def start_text_model():
            from .core import get_settings

            settings = get_settings(app.state.db)
            if settings.get("text_provider") == "ollama" and settings.get("text_base_url", "").rstrip("/") in (
                "",
                "http://127.0.0.1:11434/v1",
                "http://localhost:11434/v1",
            ):
                await app.state.ollama_runtime.start()

        model_start = (
            asyncio.create_task(start_local_model())
            if start_scheduler and not os.environ.get("MERIDIAN_TEST")
            else None
        )
        text_start = (
            asyncio.create_task(start_text_model()) if start_scheduler and not os.environ.get("MERIDIAN_TEST") else None
        )
        yield
        if scheduler:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        for name in ("daily_refresh_task", "mail_sync_task"):
            task = getattr(app.state, name, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if model_start:
            model_start.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await model_start
        if text_start:
            text_start.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await text_start
        await app.state.automation.close()
        await app.state.laya_runtime.close()
        await app.state.ollama_runtime.close()

    app = FastAPI(title=APP_NAME, version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.data_dir = root
    app.state.db = Database(root / "database" / "meridian.sqlite3")
    app.state.secrets = store
    app.state.mail = MailService(app.state.db, store)
    app.state.text_ai = TextService(app.state.db, store)
    app.state.restoring = False
    app.state.active_requests = 0
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "Origin is not permitted"}, status_code=403)
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path != "/api/oauth/callback":
            authorization = request.headers.get("authorization", "")
            if not hmac.compare_digest(authorization, "Bearer " + credential):
                return JSONResponse({"detail": "Local API authorization required"}, status_code=401)
        if app.state.restoring and request.url.path != "/api/health":
            return JSONResponse({"detail": "Backup restore is in progress"}, status_code=503)
        try:
            if int(request.headers.get("content-length", "0")) > 550 * 1024 * 1024:
                return JSONResponse({"detail": "Upload too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid content length"}, status_code=400)
        app.state.active_requests += 1
        try:
            return await call_next(request)
        finally:
            app.state.active_requests -= 1

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(
            {"detail": "This operation conflicts with an existing record or required relationship"}, status_code=409
        )

    @app.exception_handler(RuntimeError)
    async def runtime_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    from .core import router

    app.include_router(router, prefix="/api")
    from .onboarding import router as onboarding_router, record_ai_probe, record as onboarding_record

    app.include_router(onboarding_router, prefix="/api")
    from .tracking import router as tracking_router

    app.include_router(tracking_router, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "name": APP_NAME, "version": "0.1.0", "local": True, "data_dir": str(root)}

    @app.get("/api/integrations")
    async def integrations():
        rows = [decode_row(row) for row in app.state.db.query("SELECT * FROM integrations ORDER BY provider")]
        return {"items": rows, "total": len(rows)}

    @app.put("/api/integrations/{provider}")
    async def integration_update(provider: str, payload: dict = Body(...)):
        from .core import validate_config_tree

        if provider not in PROVIDERS:
            raise ValueError("Unsupported integration")
        config = payload.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("Integration config must be an object")
        validate_config_tree(config)
        config = dict(config)
        # Account identity is established by authentication, never a form field.
        if provider in {"gmail", "outlook", "imap"}:
            config.pop("account_email", None)
        secret_keys = {"password", "api_key", "access_token", "refresh_token", "secret", "client_secret", "token"}
        if any(key.lower() in secret_keys for key in config):
            raise ValueError("Use the secret field for credentials; config is stored in SQLite")
        if "secret" in payload and payload["secret"]:
            secret_name = f"{provider}:client_secret" if provider in ("gmail", "outlook") else f"{provider}:secret"
            await app.state.mail.credential("set", secret_name, payload["secret"])
        existing = app.state.db.one("SELECT * FROM integrations WHERE provider=?", (provider,))
        status = existing["status"] if existing else "configured"
        previous_config = json.loads(existing["config"]) if existing else {}
        previous_account = previous_config.pop("account_email", None)
        changed = not existing or config != previous_config or bool(payload.get("secret"))
        if not changed and previous_account:
            config["account_email"] = previous_account
        # Changing account or OAuth client invalidates the previous authorization.
        if changed:
            status = "configured"
        app.state.db.execute(
            "INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?) ON CONFLICT(provider) DO UPDATE SET config=excluded.config,status=excluded.status",
            (str(uuid4()), provider, json.dumps(config), status),
        )
        if changed:
            app.state.db.execute("UPDATE integrations SET last_sync=NULL,last_error=NULL WHERE provider=?", (provider,))
            if provider == "jev":
                onboarding_record(app.state.db, "ai_browser", None)
            elif provider not in {"gmail", "outlook", "imap"}:
                onboarding_record(app.state.db, "ai_text", None)
        return decode_row(app.state.db.one("SELECT * FROM integrations WHERE provider=?", (provider,)))

    @app.post("/api/integrations/{provider}/connect")
    async def integration_connect(provider: str, request: Request):
        if provider == "imap":
            if app.state.mail.lock.locked():
                raise HTTPException(409, "Wait for the current email operation before reconnecting")
            async with app.state.mail.lock:
                messages = await app.state.mail.fetch("imap")
                for message in messages:
                    ingest_message(app.state.db, message, "imap")
                config = app.state.mail.config("imap")
                if messages:
                    generation, last_uid = messages[-1]["external_id"].rsplit(":", 2)[-2:]
                    config.update(uidvalidity=generation, last_uid=int(last_uid))
                config["account_email"] = config.get("username")
                app.state.db.execute(
                    "UPDATE integrations SET config=?,status='connected',last_error=NULL,last_sync=? WHERE provider='imap'",
                    (json.dumps(config), now()),
                )
            return {"connected": True, "messages": len(messages)}
        redirect = str(request.base_url).rstrip("/") + "/api/oauth/callback"
        if provider == "outlook":
            # Microsoft ignores the ephemeral port for registered localhost redirects.
            port = request.url.port
            redirect = f"http://localhost{':' + str(port) if port else ''}/api/oauth/callback"
        return {"url": app.state.mail.connect(provider, redirect), "redirect_uri": redirect}

    @app.get("/api/oauth/callback", response_class=HTMLResponse)
    async def oauth_callback(state: str = "", code: str = "", error: str = ""):
        try:
            await app.state.mail.callback(state, code, error)
        except ValueError as exc:
            return HTMLResponse(
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Meridian · Email connection</title></head>"
                "<body style='font:18px system-ui;max-width:640px;margin:12vh auto;padding:24px'>"
                "<h1>Email connection needs attention</h1>"
                f"<p>{html.escape(str(exc))}</p>"
                "<p>You can close this tab and return to Meridian → Email.</p></body></html>",
                status_code=400,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        return HTMLResponse(
            "<h1>Email connected</h1><p>Return to Meridian and choose Sync inbox to complete the first sync.</p>",
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.get("/api/email/status")
    async def email_status():
        return app.state.mail.status()

    @app.post("/api/email/sync")
    async def email_sync():
        result = await app.state.mail.sync()
        if result.get("imported"):
            await notify(
                APP_NAME,
                f"{result['imported']} new email messages checked. Review your recruitment inbox.",
                app.state.db,
            )
        return result

    @app.get("/api/emails")
    async def emails(limit: int = 50, offset: int = 0, application_id: str | None = None):
        limit, offset = min(max(limit, 1), 200), max(offset, 0)
        where, params = (" WHERE application_id=?", [application_id]) if application_id else ("", [])
        rows = app.state.db.query(
            "SELECT id,provider,external_id,subject,sender,received_at,classification,confidence,application_id,deadline,metadata FROM email_messages"
            + where
            + " ORDER BY received_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return {
            "items": [decode_row(x) for x in rows],
            "total": app.state.db.one("SELECT COUNT(*) AS n FROM email_messages" + where, params)["n"],
        }

    @app.post("/api/emails/import")
    async def email_import(file: UploadFile = File(...)):
        raw = await file.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Email file exceeds 10 MB")
        return ingest_message(app.state.db, parse_message(raw))

    @app.get("/api/emails/{message_id}")
    async def email_detail(message_id: str):
        row = app.state.db.one("SELECT * FROM email_messages WHERE id=?", (message_id,))
        if not row:
            raise HTTPException(404, "Email not found")
        return decode_row(row)

    @app.post("/api/emails/{message_id}/link")
    async def email_link(message_id: str, payload: dict = Body(...)):
        message = app.state.db.one("SELECT * FROM email_messages WHERE id=?", (message_id,))
        application_id = payload.get("application_id")
        if not message or not app.state.db.one("SELECT id FROM applications WHERE id=?", (application_id,)):
            raise ValueError("Email or application not found")
        app.state.db.execute("UPDATE email_messages SET application_id=? WHERE id=?", (application_id, message_id))
        app.state.db.execute(
            "INSERT OR IGNORE INTO application_emails(application_id,email_id) VALUES(?,?)",
            (application_id, message_id),
        )
        analysis = classify_message(message["subject"], message["body"], message["received_at"])
        if analysis["status"] and analysis["confidence"] >= 0.9:
            app.state.db.event(
                application_id, analysis["status"], f"Manually linked email: {message['subject']}", "email"
            )
        app.state.db.execute(
            "UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE kind='EMAIL_LINK' AND instr(question,?)>0",
            (application_id, now(), message_id),
        )
        return {"linked": True}

    @app.get("/api/automation")
    async def automation_status():
        return app.state.automation.status()

    @app.post("/api/automation/pause")
    async def automation_pause():
        return app.state.automation.pause()

    @app.post("/api/automation/resume")
    async def automation_resume():
        return await app.state.automation.resume()

    @app.post("/api/applications/{application_id}/apply")
    async def application_apply(application_id: str):
        return app.state.automation.launch(application_id)

    @app.post("/api/applications/{application_id}/approve")
    async def application_approve(application_id: str, payload: dict = Body(...)):
        if payload.get("approved") is not True:
            raise ValueError("Explicit approval is required")
        result = await app.state.automation.approve(application_id)
        await notify(
            APP_NAME,
            "Application confirmed" if result.get("status") == "confirmed" else "Application needs attention",
            app.state.db,
        )
        return result

    @app.post("/api/applications/{application_id}/takeover")
    async def takeover(application_id: str):
        app.state.automation.application(application_id)
        return await app.state.automation.browser.takeover()

    @app.post("/api/applications/{application_id}/resume")
    async def application_resume(application_id: str):
        return app.state.automation.launch(application_id, resume=True)

    @app.post("/api/applications/{application_id}/reconcile")
    async def reconcile(application_id: str, payload: dict = Body(...)):
        if app.state.automation.running:
            raise ValueError("Wait for the current browser operation to pause")
        status = payload.get("status")
        if status not in ("CONFIRMED", "PREPARING", "WITHDRAWN") or not str(payload.get("evidence", "")).strip():
            raise ValueError("Choose CONFIRMED, PREPARING or WITHDRAWN and supply your verification evidence")
        app.state.db.event(application_id, status, "Manual reconciliation: " + payload["evidence"], "manual")
        app.state.db.execute(
            "UPDATE automation_runs SET status='reconciled',updated_at=? WHERE application_id=? AND status IN ('interrupted','unconfirmed','submitting')",
            (now(), application_id),
        )
        app.state.db.execute(
            "UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE application_id=? AND kind IN ('SUBMISSION_UNCONFIRMED','INTERRUPTED')",
            (payload["evidence"], now(), application_id),
        )
        return {"status": status}

    @app.get("/api/automation/runs/{run_id}")
    async def run_detail(run_id: str):
        run = app.state.db.one("SELECT * FROM automation_runs WHERE id=?", (run_id,))
        if not run:
            raise HTTPException(404, "Run not found")
        return decode_row(run) | {
            "steps": [
                decode_row(row)
                for row in app.state.db.query(
                    "SELECT * FROM automation_steps WHERE run_id=? ORDER BY created_at", (run_id,)
                )
            ]
        }

    @app.post("/api/browser/open")
    async def browser_open(payload: dict = Body(...)):
        url = payload.get("url", "https://www.linkedin.com/login")
        parsed = urlparse(url)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
            raise ValueError("A valid HTTP(S) browser URL is required")
        if app.state.automation.running or app.state.automation.lock.locked():
            raise ValueError("Pause the current application before opening another browser page")
        return await app.state.automation.browser.open(url)

    @app.get("/api/browser/status")
    async def browser_status():
        return app.state.automation.browser.status()

    @app.post("/api/ai/test")
    async def ai_test(payload: dict = Body(...)):
        from .automation import probe_engine
        from .core import get_settings

        settings = get_settings(app.state.db)
        engine = payload.get("engine", settings.get("browser_engine", "laya"))
        from .usage import decision_settings

        settings = decision_settings(app.state.db, settings | {"browser_engine": engine}, store)
        result = await probe_engine(
            engine=engine, settings=settings, api_key=store.get("jev:secret") if engine in ("jev", "hybrid") else None
        )
        if engine in ("laya", "hybrid"):
            runtime = await app.state.laya_runtime.status()
            result.update({key: runtime[key] for key in ("memory_mb", "memory_measurement", "model")})
        if engine == get_settings(app.state.db).get("browser_engine"):
            record_ai_probe(app.state.db, "browser", result)
        return result

    @app.get("/api/ai/local/status")
    async def local_ai_status():
        return await app.state.laya_runtime.status()

    @app.post("/api/ai/local/install")
    async def local_ai_install():
        return await app.state.laya_runtime.install()

    @app.post("/api/ai/local/start")
    async def local_ai_start():
        return await app.state.laya_runtime.start()

    @app.post("/api/ai/local/stop")
    async def local_ai_stop():
        return await app.state.laya_runtime.stop()

    @app.post("/api/ai/benchmark")
    async def ai_benchmark(payload: dict = Body(...)):
        from .automation import benchmark_engine
        from .core import get_settings

        settings = get_settings(app.state.db)
        engine = payload.get("engine", settings.get("browser_engine", "laya"))
        from .usage import decision_settings

        settings = decision_settings(app.state.db, settings | {"browser_engine": engine}, store)
        result = await benchmark_engine(
            engine=engine, settings=settings, api_key=store.get("jev:secret") if engine in ("jev", "hybrid") else None
        )
        if engine in ("laya", "hybrid"):
            runtime = await app.state.laya_runtime.status()
            result.update({key: runtime[key] for key in ("memory_mb", "memory_measurement", "model")})
        if result.get("runs") and engine == get_settings(app.state.db).get("browser_engine"):
            record_ai_probe(app.state.db, "browser", result["runs"][-1])
        return result

    @app.post("/api/ai/text/test")
    async def text_ai_test():
        from .core import get_settings

        await ensure_text_runtime()
        result = await app.state.text_ai.test(get_settings(app.state.db))
        record_ai_probe(app.state.db, "text", result)
        return result

    async def ensure_text_runtime():
        from .core import get_settings

        settings = get_settings(app.state.db)
        if (
            not os.environ.get("MERIDIAN_TEST")
            and settings.get("text_provider") == "ollama"
            and settings.get("text_base_url", "").rstrip("/")
            in ("", "http://127.0.0.1:11434/v1", "http://localhost:11434/v1")
        ):
            state = await app.state.ollama_runtime.start()
            if not state.get("ready"):
                raise ValueError(state.get("error") or "Download the configured local text model before using it")

    @app.get("/api/ai/text/local/status")
    async def local_text_status():
        return await app.state.ollama_runtime.status()

    @app.post("/api/ai/draft")
    async def ai_draft(payload: dict = Body(...)):
        from .core import get_settings

        job = app.state.db.one("SELECT * FROM jobs WHERE id=?", (payload.get("job_id"),))
        if not job:
            raise ValueError("Select a job before generating an answer")
        await ensure_text_runtime()
        return await app.state.text_ai.draft(
            get_settings(app.state.db), payload.get("question", ""), job, payload.get("fact_ids", [])
        )

    @app.post("/api/backups/export")
    async def backup_export(payload: dict = Body(...)):
        path = await asyncio.to_thread(export_backup, app.state.db, root, payload.get("password", ""))
        return {"name": path.name, "url": "/api/backups/" + path.name + "/download"}

    @app.post("/api/exports/path")
    async def export_path(payload: dict = Body(...)):
        from .documents import document_path

        route = str(payload.get("path", "")).removeprefix("/api")
        document = re.fullmatch(r"/document-versions/([a-zA-Z0-9-]+)/download", route)
        backup = re.fullmatch(r"/backups/([a-zA-Z0-9_.-]+\.meridian)/download", route)
        if document:
            path, version = document_path(app.state.db, root, document.group(1))
            return {"source": str(path), "name": version["filename"]}
        if backup:
            path = (root / "backups" / backup.group(1)).resolve()
            if path.is_relative_to((root / "backups").resolve()) and path.is_file():
                return {"source": str(path), "name": path.name}
        raise HTTPException(404, "Registered export not found")

    @app.get("/api/backups/{name}/download")
    async def backup_download(name: str):
        path = root / "backups" / name
        if Path(name).name != name or not path.is_file() or path.suffix != ".meridian":
            raise HTTPException(404, "Backup not found")
        return FileResponse(path, filename=name, media_type="application/octet-stream")

    @app.post("/api/backups/restore")
    async def backup_restore(file: UploadFile = File(...), password: str = Form(...)):
        from .discovery import discovery_busy

        if (
            app.state.automation.running
            or app.state.automation.lock.locked()
            or app.state.mail.lock.locked()
            or discovery_busy(app.state.db)
            or app.state.text_ai.lock.locked()
            or app.state.active_requests > 1
        ):
            raise ValueError("Pause automation and wait for active work before restoring")
        app.state.automation.pause()
        app.state.restoring = True
        try:
            raw = await file.read(512 * 1024 * 1024 + 1)
            # Keep a recoverable encrypted snapshot using the supplied restore password.
            await asyncio.to_thread(export_backup, app.state.db, root, password)
            return await asyncio.to_thread(restore_backup, app.state.db, root, raw, password)
        finally:
            app.state.restoring = False

    return app


async def schedule(app):
    from .core import get_settings
    from .discovery import run_sources
    from .notifications import collect_notifications

    last_search, last_mail = 0.0, 0.0
    while True:
        await asyncio.sleep(15)
        if app.state.restoring:
            continue
        settings = get_settings(app.state.db)
        try:
            for message in collect_notifications(app.state.db):
                await notify(APP_NAME, message, app.state.db)
        except Exception:
            logger.warning("notification_scan_failed", exc_info=False)
        if not settings.get("scheduler_enabled", False):
            continue
        current = asyncio.get_running_loop().time()
        try:
            if settings.get("tracking_first", True):
                from datetime import datetime
                from .tracking import refresh_daily

                daily = settings.get("daily_last_refresh") or {}
                if daily.get("day") != datetime.now().astimezone().date().isoformat() and current - last_search >= 60:
                    last_search = current
                    await refresh_daily(app.state.db, app.state.data_dir)
            elif (
                not settings.get("automation_paused", True)
                and current - last_search >= max(15, int(settings.get("discovery_interval_minutes", 120))) * 60
            ):
                last_search = current
                await run_sources(app.state.db, app.state.data_dir, due_only=True)
                from .rules import run_rules

                await run_rules(app.state.db, app.state.data_dir, app.state.automation)
            if (
                current - last_mail
                >= max(5, int(settings.get("email_interval_minutes", settings.get("email_poll_minutes", 10)))) * 60
            ):
                last_mail = current
                task = getattr(app.state, "mail_sync_task", None)
                if task is None or task.done():

                    async def sync_mail():
                        try:
                            await app.state.mail.sync()
                        except Exception:
                            logger.warning("scheduled_mail_failed", exc_info=False)

                    app.state.mail_sync_task = asyncio.create_task(sync_mail())
        except Exception:
            logger.warning("scheduler_task_failed", exc_info=False)


def run():
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())
        handler.setFormatter(JsonFormatter())
    if not os.environ.get("MERIDIAN_API_TOKEN"):
        if os.environ.get("MERIDIAN_DEV") == "1":
            os.environ["MERIDIAN_API_TOKEN"] = "meridian-development-token"
        else:
            raise SystemExit("Start Meridian from the desktop app, or use MERIDIAN_DEV=1 for local development")
    uvicorn.run(
        create_app(),
        host="127.0.0.1",
        port=int(os.environ.get("MERIDIAN_PORT", "8765")),
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    run()
