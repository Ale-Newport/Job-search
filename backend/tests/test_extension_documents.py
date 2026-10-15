"""Which of the user's documents a page is offered (SAFE-09), and what an upload field says when it is offered none (CPL-06,
CPL-14): the CV written for ANOTHER employer is never attached, an imported-but-unapproved CV is explained, a cover letter is
attached only when Meridian wrote one for this very job."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import ScriptedModel, add_cv, bridge, el, http, make_app, posting, suggest_body  # noqa: E402
from jobagent.db import now, uid  # noqa: E402
from jobagent.discovery import ingest_job  # noqa: E402
from jobagent.documents import save_version  # noqa: E402
from jobagent.extension import api as extension_api  # noqa: E402
from jobagent.extension import documents_pick as D  # noqa: E402

ACME_URL = "https://boards.greenhouse.io/acmerobotics/jobs/4001"
DESCRIPTION = "Build and run reliable payment services used by millions of customers every day. " * 6


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def add_job(app, url, company, title="Software Engineer"):
    job, _ = ingest_job(app.state.db, {"url": url, "title": title, "company": company, "location": "London",
                                       "description": DESCRIPTION, "source": "greenhouse"})
    return job["id"]


def add_document(app, *, job_id=None, kind="cv", filename="cv.pdf", approved=True, name=None, content=None):
    """A document version exactly as the vault stores it; ``job_id`` is the job a tailored document was written for."""
    db = app.state.db
    document = {"id": uid(), "name": name or filename, "kind": kind, "job_id": job_id, "created_at": now(),
                "updated_at": now()}
    db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", tuple(document.values()))
    return save_version(db, Path(app.state.data_dir), document, content or f"%PDF-1.4 {uid()}".encode(), filename,
                        "application/pdf", "Test Candidate CV", approved=approved)


@pytest.fixture
async def ctx(tmp_path):
    """A page of Acme Robotics whose job is already known to Meridian (the normal state of a job the user found there)."""
    app = make_app(tmp_path)
    ScriptedModel().install(app)
    acme = add_job(app, ACME_URL, "Acme Robotics")
    globex = add_job(app, "https://boards.greenhouse.io/globex/jobs/77", "Globex", title="Data Scientist")
    async with http(bridge(app)) as client:
        created = (await client.post("/v1/sessions", json=posting())).json()
        yield SimpleNamespace(app=app, client=client, db=app.state.db, sid=created["session_id"], acme=acme, globex=globex)
    await app.state.extension_sessions.close()
    await app.state.extension_generation.close()
    await app.state.extension_research.close()


async def offered(ctx, label="Resume", node=3):
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body([el(node, "upload", label)]))
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


async def listing(ctx, session=False):
    url = "/v1/documents" + (f"?session_id={ctx.sid}" if session else "")
    response = await ctx.client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------------------------- SAFE-09
async def test_the_cv_written_for_another_employer_is_never_offered(ctx):
    generic = add_document(ctx.app, filename="generic-cv.pdf", kind="import")
    add_document(ctx.app, filename="globex-tailored-cv.pdf", job_id=ctx.globex, name="Globex - Data Scientist (cv)")  # newer
    suggestion = await offered(ctx)
    assert suggestion["state"] == "VERIFIED" and suggestion["suggestion"]["operation"] == "ATTACH_FILE"
    assert suggestion["suggestion"]["document"]["id"] == generic["id"]
    assert suggestion["suggestion"]["document"]["filename"] == "generic-cv.pdf"
    listed = await listing(ctx)
    assert [d["filename"] for d in listed] == ["generic-cv.pdf"] and listed[0]["default"] is True and listed[0]["scope"] == "base"
    assert (await listing(ctx, session=True))[0]["filename"] == "generic-cv.pdf"  # nor for this page


async def test_with_only_another_jobs_cv_nothing_is_attached_and_the_field_says_so(ctx):
    add_document(ctx.app, filename="globex-tailored-cv.pdf", job_id=ctx.globex)
    suggestion = await offered(ctx)
    assert suggestion["state"] == "SKIP" and suggestion["insert"] == "never" and suggestion["suggestion"] is None
    assert suggestion["why"]["rules"] == ["file", "cv_missing"] and "Import and approve" in suggestion["why"]["summary"]
    assert await listing(ctx) == []
    profile = (await ctx.client.get("/v1/documents")).json()
    assert profile == [] and extension_api.profile_summary(ctx.db)["has_cv"] is False  # a CV for one job is not "your CV"


async def test_the_cv_written_for_this_job_is_preferred_and_listed_with_its_scope(ctx):
    base = add_document(ctx.app, filename="generic-cv.pdf", kind="import")
    mine = add_document(ctx.app, filename="acme-cv.pdf", job_id=ctx.acme, name="Acme Robotics - Software Engineer (cv)")
    suggestion = await offered(ctx)
    assert suggestion["suggestion"]["document"]["id"] == mine["id"]
    assert suggestion["suggestion"]["display"] == "Acme Robotics - Software Engineer (cv)"  # a name the user can recognise
    assert suggestion["suggestion"]["document"]["name"] == "Acme Robotics - Software Engineer (cv)"
    page = await listing(ctx, session=True)
    assert [(d["id"], d["scope"], d["default"]) for d in page] == [(mine["id"], "job", True), (base["id"], "base", False)]
    assert [d["id"] for d in await listing(ctx)] == [base["id"]]  # without a page only the user's own CVs are listed


async def test_several_approved_base_cvs_are_all_listed_and_the_settings_choice_leads(ctx):
    first = add_document(ctx.app, filename="first.pdf", kind="import")
    second = add_document(ctx.app, filename="second.pdf", kind="import")
    assert [d["id"] for d in await listing(ctx)] == [second["id"], first["id"]]  # newest first by default
    import json
    ctx.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('default_document_version_id',?)", (json.dumps(first["id"]),))
    listed = await listing(ctx)
    assert [d["id"] for d in listed] == [first["id"], second["id"]] and [d["default"] for d in listed] == [True, False]
    assert (await offered(ctx))["suggestion"]["document"]["id"] == first["id"]


async def test_a_settings_default_that_is_another_jobs_cv_is_ignored(ctx):
    import json
    base = add_document(ctx.app, filename="generic-cv.pdf", kind="import")
    other = add_document(ctx.app, filename="globex-tailored-cv.pdf", job_id=ctx.globex)
    ctx.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('default_document_version_id',?)", (json.dumps(other["id"]),))
    assert (await offered(ctx))["suggestion"]["document"]["id"] == base["id"]


async def test_a_cv_the_user_attached_to_this_application_wins(ctx):
    add_document(ctx.app, filename="generic-cv.pdf", kind="import")
    chosen = add_document(ctx.app, filename="globex-tailored-cv.pdf", job_id=ctx.globex)  # the user's own explicit choice here
    application = uid()
    ctx.db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (application, ctx.acme, "review", "PREPARING", now(), now()))
    ctx.db.execute("INSERT INTO application_documents VALUES(?,?,?)", (application, chosen["id"], "cv"))
    created = (await ctx.client.post("/v1/sessions", json=posting())).json()  # the page finds its application
    ctx.sid = created["session_id"]
    assert (await offered(ctx))["suggestion"]["document"]["id"] == chosen["id"]


async def test_the_cv_the_answer_engine_is_given_is_one_this_page_may_use(ctx):
    base = add_document(ctx.app, filename="generic-cv.pdf", kind="import")
    add_document(ctx.app, filename="globex-tailored-cv.pdf", job_id=ctx.globex)
    session = ctx.app.state.extension_sessions.sessions[ctx.sid]
    assert ctx.app.state.extension_sessions.service.job_base(session)["resume_version_id"] == base["id"]


def test_the_pure_ordering_rules():
    def row(identifier, job_id=None):
        return {"id": identifier, "job_id": job_id, "name": identifier, "filename": identifier + ".pdf", "mime_type": "x",
                "size": 1, "version": 1, "content_hash": "h"}

    rows = [row("newest-other", "j-other"), row("this-job", "j1"), row("chosen"), row("older-base")]
    order = lambda **kw: [r["id"] for r in D.order_cvs(rows, **{"job_id": "j1", "attached": set(), "chosen": None, **kw})]  # noqa: E731
    assert order() == ["this-job", "chosen", "older-base"]  # the CV of another job is not even a candidate
    assert order(chosen="older-base") == ["this-job", "older-base", "chosen"]
    assert order(attached={"newest-other"}) == ["newest-other", "this-job", "chosen", "older-base"]
    assert order(job_id=None) == ["chosen", "older-base"]


# --------------------------------------------------------------------------------------------- CPL-06
async def test_an_imported_but_unapproved_cv_is_explained_not_ignored_in_silence(ctx):
    pending = add_document(ctx.app, filename="my-cv.pdf", kind="import", approved=False)
    suggestion = await offered(ctx, "job application[resume] resume")
    assert suggestion["state"] == "SKIP" and suggestion["insert"] == "never"
    assert suggestion["why"]["rules"] == ["file", "cv_unapproved"]
    assert "Approve it in Meridian" in suggestion["why"]["summary"]
    status = extension_api.profile_summary(ctx.db)
    assert status["has_cv"] is False and status["unapproved_cvs"] == 1  # the panel can say it once, from /v1/status
    ctx.db.execute("UPDATE document_versions SET approved=1 WHERE id=?", (pending["id"],))
    approved = await offered(ctx)
    assert approved["state"] == "VERIFIED" and approved["suggestion"]["document"]["id"] == pending["id"]
    status = extension_api.profile_summary(ctx.db)
    assert status["has_cv"] is True and status["unapproved_cvs"] == 0


async def test_with_no_cv_at_all_the_field_says_to_import_one(ctx):
    suggestion = await offered(ctx)
    assert suggestion["state"] == "SKIP" and suggestion["why"]["rules"] == ["file", "cv_missing"]
    assert "Import and approve" in suggestion["why"]["summary"]


@pytest.mark.parametrize("label", ["Transcript", "Profile photo", "Portfolio", "Certificate", "Reference letter"])
async def test_other_uploads_say_what_meridian_does_attach(ctx, label):
    add_document(ctx.app, filename="cv.pdf", kind="import")
    suggestion = await offered(ctx, label)
    assert suggestion["state"] == "SKIP" and suggestion["why"]["rules"] == ["file", "file"]
    assert "approved CV" in suggestion["why"]["summary"] and "yourself" in suggestion["why"]["summary"]


async def test_the_status_counts_only_the_users_own_cvs_for_the_summary(ctx):
    add_document(ctx.app, filename="globex.pdf", job_id=ctx.globex, approved=False)  # a draft written for another job
    assert extension_api.profile_summary(ctx.db)["unapproved_cvs"] == 0
    add_document(ctx.app, filename="my-cv.pdf", kind="import", approved=False)
    assert extension_api.profile_summary(ctx.db)["unapproved_cvs"] == 1


# --------------------------------------------------------------------------------------------- CPL-14
async def test_a_cover_letter_written_for_this_job_and_approved_is_attached_like_a_cv(ctx):
    add_document(ctx.app, filename="cv.pdf", kind="import")
    letter = add_document(ctx.app, kind="cover_letter", filename="cover-acme.pdf", job_id=ctx.acme,
                          name="Acme Robotics - Software Engineer (cover_letter)")
    suggestion = await offered(ctx, "Cover letter", node=5)
    assert suggestion["state"] == "VERIFIED" and suggestion["insert"] == "tab"
    assert suggestion["suggestion"]["operation"] == "ATTACH_FILE" and suggestion["suggestion"]["document"]["id"] == letter["id"]
    assert suggestion["why"]["code"] == "document_attach" and "cover letter" in suggestion["why"]["summary"]
    content = await ctx.client.get(f"/v1/documents/{letter['id']}/content")
    assert content.status_code == 200 and content.json()["filename"] == "cover-acme.pdf"
    assert letter["id"] not in [d["id"] for d in await listing(ctx, session=True)]  # a cover letter is never listed as a CV
    cv_field = await offered(ctx, "Resume")
    assert cv_field["suggestion"]["document"]["filename"] == "cv.pdf"  # and never offered for a CV field


@pytest.mark.parametrize("label", ["Cover letter", "Covering letter (optional)", "Upload your motivation letter", "Carta de presentación"])
async def test_the_cover_letter_fields_are_recognised(ctx, label):
    letter = add_document(ctx.app, kind="cover_letter", filename="cover-acme.pdf", job_id=ctx.acme)
    suggestion = await offered(ctx, label, node=5)
    assert suggestion["state"] == "VERIFIED" and suggestion["suggestion"]["document"]["id"] == letter["id"]


async def test_a_cover_letter_of_another_job_is_never_offered(ctx):
    add_document(ctx.app, kind="cover_letter", filename="cover-globex.pdf", job_id=ctx.globex)
    suggestion = await offered(ctx, "Cover letter", node=5)
    assert suggestion["state"] == "SKIP" and suggestion["why"]["rules"] == ["file", "cover_letter_none"]
    assert "only when it wrote one for this job" in suggestion["why"]["summary"]


async def test_an_unapproved_cover_letter_for_this_job_waits_for_approval(ctx):
    letter = add_document(ctx.app, kind="cover_letter", filename="cover-acme.pdf", job_id=ctx.acme, approved=False)
    suggestion = await offered(ctx, "Cover letter", node=5)
    assert suggestion["state"] == "SKIP" and suggestion["why"]["rules"] == ["file", "cover_letter_unapproved"]
    assert "not approved yet" in suggestion["why"]["summary"]
    assert (await ctx.client.get(f"/v1/documents/{letter['id']}/content")).status_code == 404  # never an unapproved version


async def test_a_cover_letter_attached_to_the_application_is_offered(ctx):
    letter = add_document(ctx.app, kind="cover_letter", filename="my-letter.pdf")  # not tied to a job, attached by the user
    application = uid()
    ctx.db.execute("INSERT INTO applications(id,job_id,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (application, ctx.acme, "review", "PREPARING", now(), now()))
    ctx.db.execute("INSERT INTO application_documents VALUES(?,?,?)", (application, letter["id"], "cover_letter"))
    ctx.sid = (await ctx.client.post("/v1/sessions", json=posting())).json()["session_id"]
    assert (await offered(ctx, "Cover letter", node=5))["suggestion"]["document"]["id"] == letter["id"]


async def test_an_unreadable_format_is_a_problem_not_an_attachment(ctx):
    add_document(ctx.app, kind="import", filename="cv.pdf")
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/suggest", json=suggest_body([
        el(3, "upload", "Resume", accept=".docx")]))
    result = response.json()["results"][0]
    assert result["state"] == "NEEDS_YOU" and "does not accept" in result["needs"]["message"]


async def test_the_approved_cv_helper_still_serves_old_callers(ctx):
    base = add_cv(ctx.app, filename="old.pdf")
    from jobagent.extension.suggest import approved_documents
    assert [d["id"] for d in approved_documents(ctx.db, ctx.app.state.data_dir)] == [base["id"]]
