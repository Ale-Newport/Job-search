"""What a correction may NEVER teach Meridian (SAFE-14), and which saved answers must not travel to another field (SAFE-12).

A saved answer is remembered by its label alone and offered again as a VERIFIED suggestion: so a one-time code, a card
number or somebody else's phone number must never be saved, a second 'Phone' must not inherit the first one's value, and a
value promoted to the Profile must look like what that Profile field holds."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import el  # noqa: E402
from jobagent.extension.feedback import never_saved_reason, other_party_label, profile_value_problem, scope_of  # noqa: E402
from test_extension_feedback import ctx, feedback, offline, ready, scan, table  # noqa: E402,F401

SECRET_FIELDS = [
    ("Social Security Number", {}, "otp-free", "id"), ("National Insurance number", {}, "QQ123456C", "id"),
    ("Bank account number", {}, "12345678", "bank"), ("IBAN", {}, "GB29NWBK60161331926819", "bank"),
    ("Sort code", {}, "60-16-13", "bank"), ("Card number", {"autocomplete": "cc-number"}, "4111111111111111", "card"),
    ("Number", {"autocomplete": "cc-number"}, "4111111111111111", "card"),
    ("CVV", {"autocomplete": "cc-csc"}, "123", "card"), ("Security code", {"name": "cardCvc"}, "123", "card"),
    ("Passport number", {}, "123456789", "id"), ("Tax ID", {}, "UTR 1234567890", "id"),
    ("Verification code", {"autocomplete": "one-time-code"}, "482913", "otp"),
    ("Enter the code", {"autocomplete": "one-time-code"}, "482913", "otp"),
    ("One-time password", {}, "482913", "otp"), ("Security question", {}, "My first pet", "security_answer"),
    ("Mother's maiden name", {}, "Smith", "security_answer"), ("Answer", {"name": "security_answer"}, "x", "security_answer"),
    ("Passport", {}, "A1234567", "id"),  # a bare 'Passport' with a number typed in is the number itself
    ("DNI", {}, "12345678Z", "id"),
]


@pytest.mark.parametrize("label,extra,value,code", SECRET_FIELDS, ids=[f[0] + "|" + f[3] for f in SECRET_FIELDS])
async def test_a_secret_or_identifier_is_never_saved_whatever_the_client_asks(ctx, label, extra, value, code):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", label, **extra)])
    for remember in ("application", "reuse"):
        body = await feedback(ctx, results[1], final_value=value, remember=remember)
        assert body["saved"] is False and body["code"] == "never_saved_" + code, (remember, body)
        assert body["application_id"] is None and body["scope"] is None and body["promoted_to_profile"] is False
    for name in ("jobs", "applications", "browser_answers", "application_answers"):
        assert table(ctx, name) == [], name  # a refusal leaves no trace, not even a bare application
    assert not [f for f in table(ctx, "facts") if value in str(f["value"]) and f["category"] == "application_answer"]
    audit = ctx.db.query("SELECT details FROM extension_events WHERE kind='edit'")
    assert all(value not in row["details"] for row in audit)  # nor in the audit trail


async def test_a_suggestion_for_such_a_field_says_so_so_that_no_save_is_offered(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Verification code", autocomplete="one-time-code"),
                               el(3, "textbox", "Card number", autocomplete="cc-number"), el(5, "textbox", "Favourite colour")])
    assert results[1]["flags"]["never_saved"] == "otp" and results[3]["flags"]["never_saved"] == "card"
    assert "never_saved" not in results[5]["flags"]


async def test_a_value_sent_for_a_control_the_server_never_stored_is_refused_the_same_way(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "First name")])
    loose = el(9, "textbox", "Card number", autocomplete="cc-number")
    response = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
        "question_id": "not-on-the-page", "frame_key": "top", "outcome": "edited", "final_value": "4111111111111111",
        "remember": "application", "element": loose})
    assert response.status_code == 200 and response.json()["code"] == "never_saved_card"
    assert results and table(ctx, "browser_answers") == []


async def test_a_passport_question_answered_yes_or_no_may_still_be_saved(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Passport")])
    body = await feedback(ctx, results[1], final_value="Yes, valid until 2031", remember="application")
    assert body["saved"] is True and "code" not in body


async def test_an_ordinary_answer_is_still_saved_and_the_response_has_no_code(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Favourite colour")])
    body = await feedback(ctx, results[1], final_value="Green", remember="application")
    assert body["saved"] is True and "code" not in body


@pytest.mark.parametrize("label", ["Are you pregnant?", "How many children do you have?", "Are you a trade union member?",
                                   "What are your political views?"])
async def test_health_adjacent_and_political_answers_are_saved_only_on_explicit_confirmation(ctx, label):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", label)])
    silent = await ctx.client.post(f"/v1/sessions/{ctx.sid}/feedback", json={
        "question_id": results[1]["question_id"], "frame_key": "top", "outcome": "edited", "final_value": "Prefer not to say"})
    assert silent.json()["saved"] is False and "confirm" in silent.json()["message"]  # no `remember` was sent
    confirmed = await feedback(ctx, results[1], final_value="Prefer not to say", remember="reuse")
    assert confirmed["saved"] is True and confirmed["scope"] == "application" and confirmed["promoted_to_profile"] is False


def test_the_pure_classifier():
    never = never_saved_reason
    assert never("Phone", {}, "+34600111222") is None and never("Notice period", {}, "1 month") is None
    assert never("Account", {"name": "bank_account_number"}, "1") == "bank"
    assert never("Value", {"autocomplete": "section-pay billing cc-number"}) == "card"
    assert never("Anything", {"type": "password"}) == "password" and never("CV", {"role": "upload"}) == "file"
    assert never("Passport", {}, "Yes") is None and never("Passport", {}, "P1234567") == "id"
    assert never("Website", {"name": "oneTimeCode"}) == "otp"


# --------------------------------------------------------------------------------------------- SAFE-12
async def test_two_questions_with_one_label_never_share_a_saved_answer(ctx):  # noqa: F811
    """Two bare 'Phone' controls: what is typed in the second must not come back, VERIFIED, in the first."""
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Phone", type="tel"), el(3, "textbox", "Phone", type="tel")])
    for node in (1, 3):
        body = await feedback(ctx, results[node], final_value="+34600111222", remember="application")
        assert body["saved"] is False and body["code"] == "ambiguous_label" and "same name" in body["message"]
    assert table(ctx, "browser_answers") == [] and table(ctx, "applications") == []
    again = await scan(ctx, [el(1, "textbox", "Phone", type="tel"), el(3, "textbox", "Phone", type="tel")])
    assert {again[1]["suggestion"]["text"], again[3]["suggestion"]["text"]} == {"+441234567890"}  # the Profile phone, both


async def test_the_same_label_on_two_separate_steps_is_not_ambiguous(ctx):  # noqa: F811
    """A twin only counts while it is on the page: once the page shows one 'Phone' again, it can be saved."""
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Phone", type="tel")])
    body = await feedback(ctx, results[1], final_value="+34600111222", remember="application")
    assert body["saved"] is True


@pytest.mark.parametrize("label,kind,extra", [("Name", "text", {}), ("Phone", "tel", {}), ("Email", "email", {})])
@pytest.mark.parametrize("remember", ["application", "reuse"])
async def test_somebody_elses_details_under_a_bare_label_are_never_saved(ctx, label, kind, extra, remember):  # noqa: F811
    """'Name' under 'Emergency contact' maps to the applicant's own Profile full_name: it must be neither saved nor promoted."""
    await ready(ctx)
    own = [el(1, "textbox", "Full name"), el(3, "textbox", "Email", type="email"), el(5, "textbox", "Phone", type="tel")]
    stranger = el(7, "textbox", label, type=kind, section_id="emergency", section_label="Emergency contact", **extra)
    before = {f["source"]: f["value"] for f in table(ctx, "facts") if f["source"].startswith("application_profile:")}
    results = await scan(ctx, [stranger])
    body = await feedback(ctx, results[7], final_value="Jane Roe" if label == "Name" else "+34611222333" if label == "Phone"
                          else "jane@roe.example", remember=remember)
    assert body["saved"] is False and body["code"] == "other_party_label" and body["promoted_to_profile"] is False
    after = {f["source"]: f["value"] for f in table(ctx, "facts") if f["source"].startswith("application_profile:")}
    assert after == before and table(ctx, "browser_answers") == []  # the Profile is exactly as it was
    fresh = await scan(ctx, own)
    assert fresh[3]["suggestion"]["text"] == "test@example.test" and fresh[5]["suggestion"]["text"] == "+441234567890"


async def test_a_question_that_names_the_other_person_is_kept_for_this_application_only(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(7, "textbox", "Emergency contact name", section_label="Emergency contact")])
    body = await feedback(ctx, results[7], final_value="Jane Roe", remember="reuse")
    assert body["saved"] is True and body["scope"] == "application" and body["promoted_to_profile"] is False
    assert not [f for f in table(ctx, "facts") if f["source"].startswith("application_profile:") and f["value"] == "Jane Roe"]


@pytest.mark.parametrize("label,kind,value,code", [
    ("Email", "email", "TYPED VALUE 42", "invalid_value"), ("Phone", "tel", "TYPED VALUE 42", "invalid_value"),
    ("LinkedIn", "url", "TYPED VALUE 42", "invalid_value"), ("Postcode", "text", "TYPED VALUE 42 AND MORE", "invalid_value"),
    ("First name", "text", "TYPED VALUE 42", "invalid_value"), ("Email", "email", "not-an-email", "invalid_value"),
])
async def test_a_value_that_is_not_what_the_profile_field_holds_is_refused_not_written(ctx, label, kind, value, code):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", label, type=kind)])
    before = {f["source"]: f["value"] for f in table(ctx, "facts") if f["source"].startswith("application_profile:")}
    for remember in ("reuse", "application"):
        body = await feedback(ctx, results[1], final_value=value, remember=remember)
        assert body["saved"] is False and body["code"] == code and body["promoted_to_profile"] is False
    after = {f["source"]: f["value"] for f in table(ctx, "facts") if f["source"].startswith("application_profile:")}
    assert after == before


@pytest.mark.parametrize("label,kind,value", [("Email", "email", "new.address@example.org"), ("Phone", "tel", "+34 600 111 222"),
                                              ("LinkedIn", "url", "https://www.linkedin.com/in/test-candidate"),
                                              ("Postcode", "text", "SW1A 1AA")])
async def test_a_valid_value_is_still_promoted_to_the_profile_when_the_user_asks(ctx, label, kind, value):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", label, type=kind)])
    body = await feedback(ctx, results[1], final_value=value, remember="reuse")
    assert body["saved"] is True and body["scope"] == "profile" and body["promoted_to_profile"] is True


async def test_an_employer_format_the_profile_cannot_hold_is_kept_for_the_application_only(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Expected salary")])
    body = await feedback(ctx, results[1], final_value="Competitive, around 60k", remember="reuse")
    assert body["saved"] is True and body["scope"] == "application" and body["promoted_to_profile"] is False
    assert "this application only" in body["message"].lower()


def test_profile_value_problems():
    problem = profile_value_problem
    assert problem("email", "a@b.example") is None and problem("email", "a@b")[0] == "refuse"
    assert problem("phone", "+44 7700 900123") is None and problem("phone", "12")[0] == "refuse"
    assert problem("first_name", "Zoë") is None and problem("first_name", "Agent 47")[0] == "refuse"
    assert problem("expected_salary_uk", "60000") is None and problem("expected_salary_uk", "60k")[0] == "format"
    assert problem("earliest_start_month", "2026-09") is None and problem("earliest_start_month", "Sept")[0] == "format"
    assert problem("availability", "2026-09-01") is None and problem("availability", "01/09/2026")[0] == "format"
    assert problem("notice_period", "2 weeks") is None and problem("notice_period", "two months")[0] == "format"
    assert problem("not_a_field", "x") is None and problem("email", "") is None


def test_scope_of_treats_other_parties_as_application_scoped_and_never_profile_mapped():
    """The answer-engine handoff: ``scope_of('Name', {'section_label': 'Emergency contact'}, 'OTHER_PARTY', ...)`` used to
    return factual / reusable / profile_key=full_name."""
    application = {"id": "", "company": "Acme", "location": "London"}
    for question, element, intent in (("Name", {"section_label": "Emergency contact"}, "OTHER_PARTY"),
                                      ("Phone", {"section_label": "Referee details"}, "OTHER"),
                                      ("Email", {"group": "Next of kin"}, "OTHER"), ("Name", {}, "OTHER_PARTY")):
        scope = scope_of(question, element, intent, application)
        assert (scope.kind, scope.reusable, scope.profile_key) == ("other_party", False, None), (question, element)
    own = scope_of("Phone", {"section_label": "Application details"}, "CONTACT", application)
    assert (own.kind, own.reusable, own.profile_key) == ("factual", True, "phone")
    assert other_party_label("Name", {"section_label": "Referee details"}, "OTHER") == "bare"
    assert other_party_label("Referee name", {}, "OTHER") == "named" and other_party_label("Name", {}, "IDENTITY") is None
