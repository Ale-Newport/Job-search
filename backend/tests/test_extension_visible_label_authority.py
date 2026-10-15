"""SEC-01 item 7: what a person READS decides what a control is, never what the page calls it behind the scenes.

A page can give a control any ``name``, ``id`` or ``autocomplete``. A file input whose visible label says 'Continue' (or says
nothing at all) but whose ``name`` is ``resume`` must not be offered the user's CV, and a field a person reads as 'Reference
code' must not be answered with their email because its ``autocomplete`` says ``email`` (that half lives in ``resolver.py``: its
hand-off patch is tested in the ENG3 files)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import add_cv, el  # noqa: E402
from jobagent.extension.documents_pick import looks_like_cover_letter_field, looks_like_cv_field  # noqa: E402
from test_extension_feedback import ctx, offline, ready, scan  # noqa: E402,F401


def field(label, **extra):
    return {"label": label, "role": "upload", "type": "file", **extra}


@pytest.mark.parametrize("label,extra", [
    ("Continue", {"name": "resume"}), ("Add photo", {"name": "resume", "id": "resume"}), ("Reference", {"id": "cv"}),
    ("", {"name": "resume"}), ("Attach", {"name": "cv_upload", "id": "resume"}), ("Upload", {"name": "curriculum"}),
    ("Drop files here", {"name": "resume"}), ("Skip this step", {"id": "resume-upload"}), ("Transcript", {"name": "resume"}),
    ("Resume", {"name": "cover_letter"}), ("Resume", {"id": "photo"}),
])
def test_a_name_or_an_id_never_makes_a_file_field_a_cv_field(label, extra):
    assert looks_like_cv_field(label, field(label, **extra)) is False


@pytest.mark.parametrize("label", ["Resume", "Resume/CV", "Upload your CV", "CV", "Curriculum vitae", "Currículum", "Lebenslauf",
                                   "Attach resume", "Your resume (PDF)"])
def test_what_the_person_reads_as_a_cv_field_still_is_one(label):
    assert looks_like_cv_field(label, field(label)) is True
    assert looks_like_cv_field(label, field(label, name="x", id="y")) is True


def test_the_question_the_server_read_counts_as_much_as_the_label():
    """The question is what the adapter made of the control (its label, or a known ATS's own name for the field)."""
    assert looks_like_cv_field("Resume/CV", field("Upload")) is True
    assert looks_like_cv_field("Upload", field("Upload", name="resume")) is False


@pytest.mark.parametrize("label,extra,expected", [
    ("Cover letter", {}, True), ("Carta de presentación", {}, True), ("Anschreiben", {"name": "x"}, True),
    ("Continue", {"name": "cover_letter"}, False), ("Reference", {"id": "cover-letter"}, False), ("", {"name": "cover_letter"}, False),
])
def test_a_cover_letter_field_is_read_the_same_way(label, extra, expected):
    assert looks_like_cover_letter_field(label, field(label, **extra)) is expected


# --------------------------------------------------------------------------------------------- through the bridge
async def test_a_disguised_file_field_gets_no_cv_but_an_honest_one_does(ctx):  # noqa: F811
    await ready(ctx)
    add_cv(ctx.app, filename="cv.pdf", content=b"%PDF-1.4 the user's own cv")
    results = await scan(ctx, [el(1, "upload", "Continue", name="resume", id="resume"), el(3, "upload", "", name="resume"),
                               el(5, "upload", "Resume/CV", name="job_application[resume]")])
    for node in (1, 3):
        assert results[node]["state"] == "SKIP" and results[node]["insert"] == "never" and results[node]["suggestion"] is None
        assert "ATTACH_FILE" not in str(results[node])
        assert results[node]["why"]["rules"] == ["file", "file"]  # 'not a document Meridian attaches': the user does it
    assert results[5]["state"] == "VERIFIED" and results[5]["suggestion"]["operation"] == "ATTACH_FILE"


async def test_the_person_visible_label_decides_when_the_page_sends_both(ctx):  # noqa: F811
    """``visible_label`` is the text a person reads; ``label`` the page's accessible name. The visible text wins."""
    await ready(ctx)
    add_cv(ctx.app, filename="cv.pdf", content=b"%PDF-1.4 the user's own cv")
    results = await scan(ctx, [el(1, "upload", "Resume", visible_label="Continue", name="resume"),
                               el(3, "upload", "Upload file", visible_label="Resume/CV", name="file1")])
    assert results[1]["state"] == "SKIP" and results[1]["suggestion"] is None
    assert results[3]["state"] == "VERIFIED" and results[3]["suggestion"]["operation"] == "ATTACH_FILE"


# --------------------------------------------------------------------------------------------- email / phone (resolver)
# The label a person reads says 'Reference code' / 'Notes'; the page's own names for the control say email, phone, a first name. The
# answer engine takes the question from the label: nothing a page calls the control behind the scenes may answer it. (These pass
# with the engine as it is; they pin the behaviour so that no later change lets a name or an autocomplete token override a label.)
DISGUISED = [
    ("Reference code", {"autocomplete": "email"}), ("Reference code", {"name": "email"}),
    ("Reference code", {"name": "phone", "type": "tel"}), ("Reference code", {"id": "email_address"}),
    ("Reference code", {"description": "enter your email"}), ("Reference code", {"placeholder": "you@example.com"}),
    ("Reference code", {"type": "email"}), ("Reference code", {"type": "tel"}),
    ("Reference code", {"type": "email", "autocomplete": "email", "name": "email", "id": "email"}),
    ("Reference code", {"type": "tel", "autocomplete": "tel", "name": "phone", "id": "phone"}),
    ("Notes", {"autocomplete": "tel"}), ("Notes", {"autocomplete": "given-name"}), ("Notes", {"autocomplete": "name"}),
    ("Notes", {"autocomplete": "url"}), ("Notes", {"name": "first_name"}), ("Notes", {"name": "linkedin"}),
    ("Notes", {"autocomplete": "postal-code"}), ("Notes", {"autocomplete": "address-level2"}),
    ("Hobbies", {"autocomplete": "tel"}), ("", {"autocomplete": "email"}), ("", {"name": "email"}),
    ("Reference", {"context": "Your email address"}), ("Reference", {"field_path": "email"}), ("Reference", {"id": "phone_number"}),
]


@pytest.mark.parametrize("label,extra", DISGUISED, ids=[f"{d[0]}|{sorted(d[1].items())}" for d in DISGUISED])
async def test_a_name_or_an_autocomplete_never_answers_a_label_that_asks_something_else(ctx, label, extra):  # noqa: F811
    await ready(ctx)
    answered = (await scan(ctx, [el(1, "textbox", label, **extra)]))[1]
    assert answered["state"] == "NEEDS_YOU" and answered["insert"] == "never" and answered["suggestion"] is None


async def test_the_label_still_decides_when_the_names_of_the_control_say_otherwise(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Phone number", name="email", autocomplete="email"),
                               el(3, "textbox", "Email address", name="phone", autocomplete="tel"),
                               el(5, "textbox", "Email address", autocomplete="email")])
    assert results[1]["suggestion"]["text"] == "+441234567890"
    assert results[3]["suggestion"]["text"] == "test@example.test" and results[5]["suggestion"]["text"] == "test@example.test"


async def test_when_the_page_sends_what_a_person_reads_that_text_is_the_question(ctx):  # noqa: F811
    """An accessible name that says 'Email address' on a control a person reads as 'Reference code' answers nothing; the
    other way round answers what is READ, never as a verified fact."""
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Email address", visible_label="Reference code"),
                               el(3, "textbox", "Reference code", visible_label="Email address"),
                               el(5, "textbox", "Your email", visible_label="Email")])
    assert results[1]["state"] == "NEEDS_YOU" and results[1]["suggestion"] is None and results[1]["question"] == "Reference code"
    assert results[3]["suggestion"]["text"] == "test@example.test" and results[3]["state"] == "INFERRED"
    assert results[5]["state"] == "VERIFIED" and results[5]["suggestion"]["text"] == "test@example.test"  # the two names agree
