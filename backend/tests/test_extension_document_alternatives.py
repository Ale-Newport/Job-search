"""CPL-20: the CV the assistant offers for a file field comes with the OTHER documents this very page may be offered, so a click-only
'Use another CV' menu can swap it (never by Tab). ``suggestion.document.alternatives`` is a list of ``{id, filename, version}``.

This file tests the decision (``documents_pick.decide_upload``) and the wire shape; the suggestion carrying it through the bridge is
``test_extension_suggest.py``'s (hand-off to its owner: ``suggest._attach`` copies ``choice['alternatives']`` into the document)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import add_cv, make_app  # noqa: E402
from jobagent.db import now, uid  # noqa: E402
from jobagent.extension import documents_pick as D  # noqa: E402
from jobagent.extension.schemas import SuggestionValue  # noqa: E402


def doc(name, version=1, *, size=1000, mime="application/pdf", job=None, default=False):
    return {"id": f"id-{name}-{version}", "name": name, "filename": name, "mime": mime, "size": size, "kind": "cv", "version": version,
            "default": default, "sha256": "x", "scope": "job" if job else "base"} | ({"job_id": job} if job else {})


FIELD = {"label": "Resume/CV", "role": "upload", "type": "file", "accept": ""}


def decide(cvs, element=FIELD):
    return D.decide_upload("Resume/CV", element, cvs=cvs, letters=[], unapproved={})


def test_the_other_eligible_cvs_are_offered_as_alternatives_in_order():
    cvs = [doc("default.pdf", 3), doc("ai.pdf", 2), doc("swe.docx", 1, mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")]
    choice = decide(cvs)
    assert choice["action"] == "attach" and choice["document"]["filename"] == "default.pdf"
    assert choice["alternatives"] == [{"id": "id-ai.pdf-2", "filename": "ai.pdf", "version": 2},
                                      {"id": "id-swe.docx-1", "filename": "swe.docx", "version": 1}]


def test_one_cv_has_no_alternatives_and_the_key_says_so():
    assert decide([doc("only.pdf")])["alternatives"] == []


def test_an_alternative_the_field_would_refuse_is_not_offered():
    """A swap that cannot be attached (the form takes PDF only, a file over 10 MB) is not a choice."""
    cvs = [doc("default.pdf"), doc("word.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
           doc("huge.pdf", size=D.MAX_DOCUMENT + 1), doc("fine.pdf", 4)]
    choice = decide(cvs, {**FIELD, "accept": ".pdf"})
    assert [a["filename"] for a in choice["alternatives"]] == ["fine.pdf"]


def test_there_are_never_more_than_a_handful_of_alternatives():
    choice = decide([doc(f"cv{i}.pdf", i) for i in range(1, 30)])
    assert len(choice["alternatives"]) == D.MAX_ALTERNATIVES == 6


def test_an_alternative_carries_nothing_but_what_a_menu_needs():
    choice = decide([doc("a.pdf"), doc("b.pdf", 2)])
    assert set(choice["alternatives"][0]) == {"id", "filename", "version"}  # no hash, no path, no size


def test_a_field_that_attaches_nothing_has_no_alternatives_to_offer():
    skipped = D.decide_upload("Transcript", {"label": "Transcript", "role": "upload"}, cvs=[doc("a.pdf"), doc("b.pdf")], letters=[],
                              unapproved={})
    assert skipped["action"] == "skip" and "alternatives" not in skipped
    refused = decide([doc("a.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"), doc("b.pdf")],
                     {**FIELD, "accept": ".pdf"})
    assert refused["action"] == "problem" and "alternatives" not in refused  # the default does not fit: the problem is said, not hidden


def test_a_cover_letter_has_alternatives_too_when_several_were_written():
    letters = [{**doc("letter-new.pdf", 2), "kind": "cover_letter"}, {**doc("letter-old.pdf", 1), "kind": "cover_letter"}]
    choice = D.decide_upload("Cover letter", {"label": "Cover letter", "role": "upload"}, cvs=[], letters=letters, unapproved={})
    assert choice["action"] == "attach" and choice["kind"] == "cover_letter"
    assert choice["alternatives"] == [{"id": "id-letter-old.pdf-1", "filename": "letter-old.pdf", "version": 1}]


def test_the_wire_shape_accepts_the_new_key():
    value = SuggestionValue(display="cv.pdf", operation="ATTACH_FILE", document={
        "id": "v1", "filename": "cv.pdf", "mime": "application/pdf", "size": 10, "sha256": "x",
        "alternatives": [{"id": "v2", "filename": "ai.pdf", "version": 2}]})
    assert value.document["alternatives"][0]["version"] == 2


def test_the_real_vault_gives_the_menu_exactly_the_documents_the_page_may_use(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_TEST", "1")
    app = make_app(tmp_path)
    first = add_cv(app, filename="first.pdf", content=b"%PDF-1")
    second = add_cv(app, filename="second.pdf", content=b"%PDF-2")
    other_job = uid()
    # a CV Meridian wrote for ANOTHER employer's posting: never part of the choice
    app.state.db.execute("INSERT INTO jobs(id,title,company,location,url,canonical_url,identity_key,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (other_job, "Engineer", "Other Co", "", "https://x.example/1", "https://x.example/1",
                                                      "https://x.example/1", now(), now()))
    foreign = add_cv(app, filename="other-co.pdf", content=b"%PDF-3")
    app.state.db.execute("UPDATE documents SET job_id=? WHERE id=?", (other_job, foreign["document_id"]))
    docs = D.approved_documents(app.state.db, app.state.data_dir, None, None)
    choice = D.decide_upload("Resume/CV", FIELD, cvs=docs, letters=[], unapproved={})
    shown = {choice["document"]["id"], *[a["id"] for a in choice["alternatives"]]}
    assert shown == {first["id"], second["id"]} and foreign["id"] not in shown
