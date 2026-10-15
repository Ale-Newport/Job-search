"""SAFE-14 (server side), English and Spanish: what a person types into a one-time-code, card, bank, ID-number or
security-answer field is never saved, whatever the client sends and whatever the save mode is ('always save', Reuse).

The scanner (``extension/src/content/sensitive.ts``) already leaves such controls alone; this is the second wall for a value that
reaches ``/feedback`` anyway (a control the server never stored, a client of another version, a label the scanner read
differently). The vocabulary is therefore a SUPERSET of the scanner's, and the labels that stay ordinary there stay savable here."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "extension"))
from harness import el  # noqa: E402
from jobagent.extension.feedback import never_saved_reason, scope_of  # noqa: E402
from test_extension_feedback import ctx, feedback, offline, ready, scan, table  # noqa: E402,F401

# (label, extra element fields, typed value, code): what must never be saved
NEVER = [
    # ---- one-time codes
    ("OTP", {}, "123456", "otp"), ("One-time password", {}, "123456", "otp"), ("2FA code", {}, "123456", "otp"),
    ("MFA code", {}, "123456", "otp"), ("Two-factor code", {}, "123456", "otp"), ("SMS code", {}, "123456", "otp"),
    ("Authentication code", {}, "123456", "otp"), ("Code from your authenticator app", {}, "123456", "otp"),
    ("6-digit code", {}, "123456", "otp"), ("Enter the code we sent you", {}, "123456", "otp"),
    ("Backup code", {}, "abcd1234", "otp"), ("Recovery code", {}, "abcd1234", "otp"), ("Confirmation code", {}, "482913", "otp"),
    ("Passcode", {}, "482913", "otp"), ("Code", {"autocomplete": "one-time-code"}, "482913", "otp"),
    ("Código de verificación", {}, "123456", "otp"), ("Código SMS", {}, "123456", "otp"),
    ("Código de un solo uso", {}, "123456", "otp"), ("Contraseña de un solo uso", {}, "123456", "otp"),
    ("Clave de verificación", {}, "123456", "otp"), ("Código de autenticación", {}, "123456", "otp"),
    ("Código de confirmación", {}, "123456", "otp"), ("Introduce el código que te hemos enviado", {}, "123456", "otp"),
    ("Código de acceso", {}, "123456", "otp"), ("Verificación en dos pasos", {}, "123456", "otp"),
    # ---- payment cards
    ("Card number", {}, "4111111111111111", "card"), ("Credit card number", {}, "4111 1111 1111 1111", "card"),
    ("Debit card", {}, "4111111111111111", "card"), ("CVV", {}, "123", "card"), ("CVC", {}, "123", "card"),
    ("CCV", {}, "123", "card"), ("CVC2", {}, "123", "card"), ("Cardholder name", {}, "A B", "card"),
    ("Name on card", {}, "A B", "card"), ("Card expiry date", {}, "12/30", "card"), ("Expiry", {"autocomplete": "cc-exp"}, "12/30", "card"),
    ("Security code (CVV)", {}, "123", "card"), ("Número de tarjeta", {}, "4111111111111111", "card"),
    ("Tarjeta de crédito", {}, "4111111111111111", "card"), ("Tarjeta de débito", {}, "4111111111111111", "card"),
    ("Titular de la tarjeta", {}, "A B", "card"), ("Fecha de caducidad de la tarjeta", {}, "12/30", "card"),
    ("Código de seguridad (CVV)", {}, "123", "card"), ("Reference", {"name": "cardNumber"}, "4111111111111111", "card"),
    # ---- bank details
    ("IBAN", {}, "ES9121000418450200051332", "bank"), ("Código IBAN", {}, "ES91", "bank"), ("Bank account number", {}, "12345678", "bank"),
    ("Account number", {}, "12345678", "bank"), ("Sort code", {}, "60-16-13", "bank"), ("Routing number", {}, "021000021", "bank"),
    ("BIC/SWIFT", {}, "NWBKGB2L", "bank"), ("SWIFT code", {}, "NWBKGB2L", "bank"), ("Bank details", {}, "x", "bank"),
    ("Account holder name", {}, "A B", "bank"), ("Número de cuenta", {}, "12345678", "bank"), ("Cuenta bancaria", {}, "ES91", "bank"),
    ("Cuenta corriente", {}, "ES91", "bank"), ("Datos bancarios", {}, "x", "bank"), ("Código SWIFT", {}, "BSCHESMM", "bank"),
    ("Titular de la cuenta", {}, "A B", "bank"), ("Reference", {"id": "routing_number"}, "021000021", "bank"),
    # ---- identity numbers
    ("SSN", {}, "123-45-6789", "id"), ("Social Security Number", {}, "123-45-6789", "id"), ("Last 4 digits of your SSN", {}, "1234", "id"),
    ("NIN", {}, "QQ123456C", "id"), ("NINO", {}, "QQ123456C", "id"), ("NI no", {}, "QQ123456C", "id"),
    ("National Insurance No.", {}, "QQ 12 34 56 C", "id"), ("Social Insurance Number (SIN)", {}, "123 456 789", "id"),
    ("Tax ID", {}, "12345678", "id"), ("Tax identification number", {}, "12345", "id"), ("TIN", {}, "12345678", "id"),
    ("Tax reference (UTR)", {}, "1234567890", "id"), ("National ID number", {}, "12345678", "id"), ("ID number", {}, "12345678", "id"),
    ("Identification number", {}, "12345", "id"), ("Passport number", {}, "123456789", "id"), ("Passport no.", {}, "123456789", "id"),
    ("Passport", {}, "123456789", "id"), ("Driving licence number", {}, "SMITH710238", "id"),
    # whatever is typed in a field that asks for the number is taken for the number unless it is plainly an answer ABOUT it
    ("National Insurance number", {}, "ABCDEFGH", "id"), ("Social Security Number", {}, "otp-free", "id"),
    ("ID number", {}, "none123", "id"), ("NIN", {}, "no123456", "id"), ("Tax ID", {}, "Yes 2019", "id"),
    ("Biometric residence permit number", {}, "ZU1234567", "id"), ("Share code", {}, "W12 345 678", "id"),
    ("Número de la Seguridad Social", {}, "281234567840", "id"), ("NSS", {}, "281234567840", "id"), ("DNI", {}, "12345678Z", "id"),
    ("NIE", {}, "X1234567L", "id"), ("NIF", {}, "12345678Z", "id"), ("DNI/NIE", {}, "12345678Z", "id"),
    ("Número de pasaporte", {}, "PAB123456", "id"), ("Pasaporte", {}, "PAB123456", "id"),
    ("Número de identificación fiscal", {}, "12345678Z", "id"), ("Número de documento", {}, "12345678Z", "id"),
    ("Número de documento de identidad", {}, "12345678Z", "id"), ("Documento de identidad", {}, "12345678Z", "id"),
    ("Carné de conducir", {}, "12345678", "id"), ("Número de permiso de conducir", {}, "12345678", "id"),
    ("Número de afiliación a la Seguridad Social", {}, "281234567840", "id"),
    # ---- security answers
    ("Security question", {}, "My first pet", "security_answer"), ("Security answer", {}, "x", "security_answer"),
    ("Secret answer", {}, "x", "security_answer"), ("Memorable word", {}, "x", "security_answer"),
    ("Mother's maiden name", {}, "Smith", "security_answer"), ("What was the name of your first pet?", {}, "Rex", "security_answer"),
    ("Pregunta de seguridad", {}, "x", "security_answer"), ("Respuesta de seguridad", {}, "x", "security_answer"),
    ("Pregunta secreta", {}, "x", "security_answer"), ("Respuesta secreta", {}, "x", "security_answer"),
    ("Apellido de soltera de tu madre", {}, "x", "security_answer"), ("Nombre de tu primera mascota", {}, "Rex", "security_answer"),
    # ---- passwords, by what the label says (a text box called 'Password')
    ("Password", {}, "hunter2", "password"), ("Confirm password", {}, "hunter2", "password"), ("Contraseña", {}, "hunter2", "password"),
]

# What stays ordinary (the scanner's look-alikes and a few more): saving these must keep working
ORDINARY = [
    ("Phone", "+34600111222"), ("Email address", "me@example.test"), ("Full name", "Jane Roe"), ("Years of experience with Swift", "3"),
    ("PIN code", "560001"), ("Postcode", "SW1A 1AA"), ("Código postal", "28001"), ("Do you hold a valid passport?", "Yes"),
    ("Passport (yes/no)", "Yes"), ("Do you currently receive Social Security benefits?", "No"),
    ("Why do you want to work here?", "Because I like the mission"), ("Date of birth", "1999-01-01"), ("Current employer", "Acme"),
    ("Expected salary", "50000"), ("LinkedIn profile", "https://linkedin.com/in/x"), ("How did you hear about us?", "A friend"),
    ("Are you over 18?", "Yes"), ("Address line 1", "1 Baker Street"), ("Notice period", "1 month"), ("Maiden name", "Smith"),
    ("Referral code", "FRIEND10"), ("Favourite colour", "Green"), ("Cuenta de LinkedIn", "https://linkedin.com/in/x"),
    ("Do you have a valid passport?", "Yes, valid until 2031"), ("Describe a time you used a secret to win trust", "I once ..."),
    ("Please describe in your own words, in as much detail as you can, how you would handle a situation in which a colleague "
     "shared their password with you", "I would tell them to change it"),
    ("Describe, in as much detail as you can, how you would handle a call in which a customer reads out their card number, their "
     "account number and the answer to their security question, and what you would do to protect them afterwards", "I would stop them"),
    ("Código de la oferta", "ABC123"), ("Teléfono", "+34600111222"), ("Profesión", "Ingeniero"),
    ("Do you hold a national insurance number?", "Yes"), ("Do you have a NI number?", "Not yet"),
    ("Do you have a NI number?", "Yes, I received it in 2019"), ("National Insurance number", "Prefer not to say"),
    ("Social Security Number", "N/A"), ("Número de la Seguridad Social", "No"), ("¿Tienes DNI?", "Sí"),
]


@pytest.mark.parametrize("label,extra,value,code", NEVER, ids=[f"{n[0]}|{n[3]}" for n in NEVER])
def test_what_is_typed_into_such_a_field_is_never_saved(label, extra, value, code):
    assert never_saved_reason(label, {"label": label, **extra}, value) == code


@pytest.mark.parametrize("label,value", ORDINARY, ids=[o[0][:50] for o in ORDINARY])
def test_ordinary_questions_that_only_mention_such_words_stay_savable(label, value):
    assert never_saved_reason(label, {"label": label}, value) is None


def test_the_scanners_never_lists_are_never_saved_here_either():
    """The labels ``extension/tests/widgets/unit/sensitive.test.mjs`` pins as untouchable. Anything the scanner refuses to read, the
    server refuses to remember (the scanner is the first wall, not the only one)."""
    scanner = {
        "otp": ["Verification code", "Enter the verification code", "One-time password", "OTP", "Security code", "2FA code",
                "Authentication code", "SMS code", "Código de verificación", "Código de seguridad", "Two-factor code"],
        "card": ["Card number", "Credit card number", "CVV", "CVC", "Name on card", "Card expiry date", "Número de tarjeta",
                 "Titular de la tarjeta"],
        "bank": ["IBAN", "Bank account number", "Account number", "Routing number", "Sort code", "SWIFT code", "Bank details",
                 "Número de cuenta", "Account holder name"],
        "id": ["Social Security Number", "SSN", "National Insurance number", "NI number", "National ID number", "ID number",
               "Passport number", "Tax ID", "Tax identification number", "Driving licence number", "DNI", "NIE",
               "Número de documento de identidad", "Número de la seguridad social", "Taxpayer ID", "Aadhaar number",
               "Social insurance number"],
        "security_answer": ["Mother's maiden name", "Security question", "Security answer", "Secret answer", "Memorable word",
                            "Respuesta de seguridad"],
    }
    refused = {}
    for code, labels in scanner.items():
        for label in labels:
            got = never_saved_reason(label, {"label": label}, "A1234567" if code == "id" else "x1")
            if got is None or (got != code and not (code == "otp" and got == "card")):
                refused[label] = got
    # (a 'security code' on a payment form is a card's; the classes agree wherever it matters: it is never saved)
    assert refused == {}


def test_the_card_class_wins_over_the_code_class_when_the_field_is_a_cards_security_code():
    assert never_saved_reason("Security code", {"label": "Security code"}) == "otp"
    assert never_saved_reason("Security code", {"label": "Security code", "name": "cardCvc"}) == "card"
    assert never_saved_reason("Código de seguridad (CVV)", {}) == "card"


@pytest.mark.parametrize("label,extra,value,code", [n for n in NEVER if n[0] in {
    "Verificación en dos pasos", "Número de afiliación a la Seguridad Social", "Datos bancarios", "Pregunta secreta", "NIN",
    "Código SMS", "Contraseña de un solo uso", "Titular de la tarjeta", "CCV", "Backup code", "Cuenta corriente"}],
    ids=lambda v: v if isinstance(v, str) and len(v) > 3 else "")
@pytest.mark.parametrize("remember", ["application", "reuse"])
async def test_the_new_wording_is_refused_with_its_code_and_leaves_no_trace(ctx, label, extra, value, code, remember):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", label, **extra)])
    body = await feedback(ctx, results[1], final_value=value, remember=remember)
    assert body["saved"] is False and body["code"] == "never_saved_" + code, body
    assert body["application_id"] is None and body["scope"] is None and body["promoted_to_profile"] is False
    for name in ("jobs", "applications", "browser_answers", "application_answers"):
        assert table(ctx, name) == [], name
    assert not [f for f in table(ctx, "facts") if value in str(f["value"]) and f["category"] == "application_answer"]
    assert all(value not in row["details"] for row in ctx.db.query("SELECT details FROM extension_events"))


async def test_such_a_field_says_so_in_the_suggestion_in_both_languages(ctx):  # noqa: F811
    await ready(ctx)
    results = await scan(ctx, [el(1, "textbox", "Código SMS"), el(3, "textbox", "Número de la Seguridad Social"),
                               el(5, "textbox", "NIN"), el(7, "textbox", "Datos bancarios"), el(9, "textbox", "Favourite colour")])
    assert results[1]["flags"]["never_saved"] == "otp" and results[5]["flags"]["never_saved"] == "id"
    assert results[7]["flags"]["never_saved"] == "bank" and results[3]["flags"]["never_saved"] == "id"
    assert "never_saved" not in results[9]["flags"]


@pytest.mark.parametrize("label,extra", [("Phone", {"autocomplete": "cc-number"}), ("Postcode", {"name": "cardNumber"}),
                                          ("Reference", {"autocomplete": "one-time-code"}), ("Verification code", {}),
                                          ("Número de cuenta", {}), ("NIN", {})])
def test_such_a_field_is_protected_and_never_reusable_whatever_it_resembles(label, extra):
    """'Always save' and Reuse read ``protected`` / ``reusable``: a card number typed into a control a page called 'Phone' is
    not a Profile phone, and nothing about it may be offered for reuse."""
    element = {"label": label, "role": "textbox", **extra}
    scope = scope_of(label, element, "OTHER", {"company": "Acme", "id": "a1"})
    assert scope.protected is True and scope.reusable is False and scope.profile_key is None
