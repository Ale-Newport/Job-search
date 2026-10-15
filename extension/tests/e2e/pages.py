"""Application pages made on the fly for scenarios that need MANY labelled controls (a label per case), served by ``servers.FixtureServers``
under the name they were registered with (``fixtures.generated[name] = html``).

A control is a dict: ``{"id", "kind", "label", "options", "extra"}`` with ``kind`` one of text | email | tel | url | number | date | textarea |
select | radio | checkbox and ``extra`` any of ``section_label`` (the control sits under that heading), ``description`` (help text tied to the
control with aria-describedby), ``required``, ``placeholder`` and ``type`` (hidden | password: an inert control), ``readonly``, ``disabled``.
What the page shows is what a real form shows: a label above its input, a legend over a radio group, a label wrapped around a lone checkbox.
The page has the job posting of the other fixtures (a London role at Acme Robotics, as JSON-LD), so the country of the job is known.
"""

from __future__ import annotations

from html import escape

SUPPORTED_EXTRA = {"section_label", "description", "required", "placeholder", "type", "readonly", "enabled"}


def _attrs(control: dict) -> str:
    extra = control.get("extra") or {}
    parts = []
    if extra.get("required"):
        parts.append("required")
    if extra.get("readonly"):
        parts.append("readonly")
    if extra.get("enabled") is False:
        parts.append("disabled")
    if extra.get("placeholder"):
        parts.append(f'placeholder="{escape(extra["placeholder"], quote=True)}"')
    if extra.get("description"):
        parts.append(f'aria-describedby="{control["id"]}_help"')
    return (" " + " ".join(parts)) if parts else ""


def _control(control: dict) -> str:
    cid, kind, label = control["id"], control.get("kind") or "text", escape(control["label"]).replace("\n", "<br>")
    options = control.get("options") or ()
    extra = control.get("extra") or {}
    attrs = _attrs(control)
    help_text = f'<p class="hint" id="{cid}_help">{escape(extra["description"])}</p>' if extra.get("description") else ""
    if kind == "radio":
        members = "".join(f'<label class="inline"><input type="radio" name="{cid}" id="{cid}{"" if i == 0 else f"_{i}"}" value="{escape(o, quote=True)}"{attrs}> {escape(o)}</label>'
                          for i, o in enumerate(options))
        body = f'<fieldset id="{cid}_group"><legend>{label}</legend>{members}{help_text}</fieldset>'
    elif kind == "checkbox":
        body = f'<div class="field"><label class="inline"><input type="checkbox" id="{cid}" name="{cid}"{attrs}> {label}</label>{help_text}</div>'
    elif kind == "select":
        choices = "".join(f'<option value="{escape(o, quote=True)}">{escape(o)}</option>' for o in options)
        body = (f'<div class="field"><label for="{cid}">{label}</label><select id="{cid}" name="{cid}"{attrs}>'
                f'<option value="">Select...</option>{choices}</select>{help_text}</div>')
    elif kind == "textarea":
        body = f'<div class="field"><label for="{cid}">{label}</label><textarea id="{cid}" name="{cid}" rows="3"{attrs}></textarea>{help_text}</div>'
    else:
        input_type = extra.get("type") or kind
        body = f'<div class="field"><label for="{cid}">{label}</label><input type="{input_type}" id="{cid}" name="{cid}"{attrs}>{help_text}</div>'
    heading = extra.get("section_label")
    return f'<section aria-label="{escape(heading, quote=True)}"><h3>{escape(heading)}</h3>{body}</section>' if heading else body


def form_page(controls: list[dict], *, title: str = "Software Engineer - Acme Robotics", lang: str = "en") -> str:
    """A complete application page: the job posting, then one form holding every control in order."""
    fields = "\n".join(_control(control) for control in controls)
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta property="og:site_name" content="Acme Robotics">
<link rel="stylesheet" href="/_base.css">
<script type="application/ld+json">
{{"@context": "https://schema.org/", "@type": "JobPosting", "title": "Software Engineer",
 "description": "<p>Acme Robotics builds warehouse robots that help small businesses ship orders faster.</p>",
 "hiringOrganization": {{"@type": "Organization", "name": "Acme Robotics"}},
 "jobLocation": {{"@type": "Place", "address": {{"@type": "PostalAddress", "addressLocality": "London", "addressCountry": "GB"}}}}}}
</script>
<script src="/_guard.js"></script>
</head>
<body>
<header class="site"><span class="brand">Acme Robotics</span></header>
<main>
<h1>Software Engineer</h1>
<form id="generated_form" action="#" novalidate>
{fields}
</form>
</main>
<script>document.getElementById('generated_form').addEventListener('submit', (event) => event.preventDefault());</script>
</body>
</html>
"""
