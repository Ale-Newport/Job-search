"""Addresses the assistant stores or sends: page URLs without anything that looks like a secret (SEC-10), and the rules for
which web addresses a PAGE may make Meridian fetch (SEC-12).

An apply link often carries who the applicant is or proves they were invited (``?email=..&key=..&sig=..``,
``candidate_id``, a magic-link ``ticket``). The page's address is stored in ``extension_sessions``, in the job row a saved
answer creates (tracker, exports) and goes into the prompt of a draft, so every parameter that names a person or proves
something is dropped first. Parameters that identify the JOB (``gh_jid``, ``jid``, ``job``, ``id``, ``req``...) stay:
two postings of one board that differ only by such a parameter must remain two postings.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# A parameter NAME is split into words (``candidate_id`` -> candidate, id; ``accessToken`` -> access, token); one of these
# words anywhere in it makes the parameter private.
SECRET_WORDS = frozenset({
    "key", "keys", "sig", "sign", "signature", "signed", "secret", "otp", "jwt", "ticket", "invite", "invitation", "email",
    "mail", "emailaddress", "expires", "expiry", "expire", "exp", "sas", "candidate", "applicant", "token", "tokens", "tkn",
    "auth", "oauth", "authorization", "password", "passwd", "pass", "pwd", "pin", "code", "session", "sess", "sid", "sso",
    "saml", "assertion", "nonce", "hash", "hmac", "credential", "credentials", "bearer", "apikey", "csrf", "xsrf", "phone",
    "tel", "mobile", "ssn", "dob", "birthday", "user", "username", "uid", "member", "account", "acct", "login", "magic",
    "firstname", "lastname", "fullname", "name", "surname", "address", "referral", "referrer", "cv", "resume",
})
# These look like secret words but name a posting: kept whatever else they contain.
JOB_NAMES = frozenset({"gh_jid", "jid", "job", "jobid", "job_id", "jobkey", "job_key", "jk", "id", "req", "reqid", "req_id",
                       "requisition", "requisitionid", "position", "positionid", "posting", "postingid", "opening", "pid",
                       "p", "jobcode", "job_code", "ghjid", "vacancy", "vacancyid"})
# Parameters that say HOW the applicant arrived (a campaign, a referrer, a recruiter's or employee's link): they name no job,
# they can name a person (``ref=jane.doe``, ``source=agency-x``) and they are kept in nothing the assistant stores or sends.
TRACKING_NAMES = frozenset({"ref", "refs", "src", "source", "sources", "gh_src", "lever-source", "lever_source", "leversource",
                            "fbclid", "gclid", "dclid", "msclkid", "mc_eid", "mc_cid", "mkt_tok", "_hsenc", "_hsmi", "igshid",
                            "trk", "trkid", "trackingid", "tracking_id", "campaign", "cmpid", "origin_source",
                            "sourceid", "source_id", "sourcetype", "source_type"})
TRACKING_PREFIXES = ("utm_", "utm-", "refer", "ref_", "ref-", "ga_", "hsa_", "pk_")
NOT_TRACKING = frozenset({"reference", "references", "referencenumber", "reference_number", "reference_id", "referenceid",
                          "reference-number", "reference-id", "referencecode", "reference_code"})  # these name a posting
EMAIL_VALUE = re.compile(r"[^@\s/]+@[^@\s/]+\.[A-Za-z]{2,}")
JWT_VALUE = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
OPAQUE_VALUE = re.compile(r"(?=[A-Za-z0-9_\-+/=]{40,}$)(?=.*[0-9])(?=.*[A-Za-z])[A-Za-z0-9_\-+/=]+")


def _words(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [word for word in re.split(r"[^a-z0-9]+", spaced.lower()) if word]


def is_tracking_parameter(name: str) -> bool:
    """Is this parameter about how the applicant got here (``utm_*``, ``ref``, ``refer*``, ``referrer``, ``source``...)?"""
    lowered = (name or "").strip().lower()
    if lowered in NOT_TRACKING or lowered in JOB_NAMES:
        return False
    return lowered in TRACKING_NAMES or lowered.startswith(TRACKING_PREFIXES) or lowered == "utm"


def is_secret_parameter(name: str, value: str = "") -> bool:
    """Would storing this query parameter keep something private (who the applicant is, a credential, a signature, how they
    were referred)?"""
    lowered = (name or "").strip().lower()
    if is_tracking_parameter(lowered):
        return True
    if lowered in JOB_NAMES:
        return False
    words = _words(name or "")
    if any(word in SECRET_WORDS for word in words) or "".join(words) in SECRET_WORDS:
        return True
    text = str(value or "")
    return bool(EMAIL_VALUE.search(text) or JWT_VALUE.fullmatch(text) or OPAQUE_VALUE.fullmatch(text))


def scrub_url(url: str) -> str:
    """``url`` without its fragment and without any secret-looking query parameter. Anything that is not a web address with a
    host is returned unchanged (the caller validates addresses)."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return url
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not is_secret_parameter(k, v)]
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), ""))
    except ValueError:
        return url


def is_standard_https(url: str) -> bool:
    """Is this an ``https`` address on the standard port (443 or none)? A research candidate that a PAGE names must be."""
    try:
        parts = urlsplit(url)
        return parts.scheme == "https" and parts.port in {None, 443} and bool(parts.hostname)
    except ValueError:
        return False
