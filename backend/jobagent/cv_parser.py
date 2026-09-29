"""Deterministic CV extraction: preserve source claims, structure and hyperlink targets."""

from __future__ import annotations

import io
import re
import unicodedata
from collections import Counter

from docx import Document
from docx.oxml.ns import qn

PARSER_VERSION = "cv-structure-2"
SECTION_NAMES = {
    "education": {
        "education",
        "qualifications",
        "academic background",
        "academic qualifications",
        "education and qualifications",
        "educación",
        "formación",
        "formación académica",
    },
    "experience": {
        "experience",
        "professional experience",
        "work experience",
        "employment",
        "employment history",
        "work history",
        "relevant experience",
        "research experience",
        "experiencia",
        "experiencia profesional",
        "experiencia laboral",
    },
    "project": {
        "projects",
        "personal projects",
        "selected projects",
        "academic projects",
        "technical projects",
        "project experience",
        "proyectos",
        "proyectos personales",
    },
    "skill": {
        "skills",
        "technical skills",
        "technologies",
        "technical expertise",
        "technical competencies",
        "programming languages",
        "habilidades",
        "competencias",
        "habilidades técnicas",
    },
    "language": {"languages", "languages and soft skills", "languages soft skills", "language skills", "idiomas"},
    "publication": {"publications", "selected publications", "research publications", "publicaciones"},
    "certification": {
        "certifications",
        "certificates",
        "courses",
        "certifications and courses",
        "certificaciones",
        "cursos",
        "courses and certifications",
    },
    "summary": {"profile", "summary", "professional summary", "professional profile", "about me", "perfil"},
    "award": {"awards", "honours", "honors", "awards and honours", "awards and honors", "achievements", "premios"},
    "volunteering": {"volunteering", "volunteer experience", "voluntary experience", "voluntariado"},
    "interest": {"interests", "hobbies", "interests and hobbies", "intereses"},
    "link": {"links", "portfolio", "profiles", "online profiles", "enlaces"},
}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
PHONE_RE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s.-]*)?(?:\(\d{1,4}\)[\s.-]*)?[\d][\d\s().-]{6,}\d(?!\w)")
BULLET_RE = re.compile(r"^\s*(?:[•●▪◦‣⁃\uF0B7]|[-*]\s|\d+[.)]\s)\s*")
DATE_RANGE_RE = re.compile(
    r"\b(?:19|20)\d{2}\s*(?:[-–—]|to|a)\s*(?:(?:[A-Za-zÀ-ÿ]+\.?\s+)?(?:19|20)\d{2}|present|current|actualidad)\b",
    re.I,
)


def section_name(text: str) -> str | None:
    label = unicodedata.normalize("NFKC", text).casefold().strip(" :#–—-\t")
    label = re.sub(r"\s+", " ", label.replace("&", "and"))
    return next((category for category, labels in SECTION_NAMES.items() if label in labels), None)


def _paragraph_text(paragraph, relationships) -> str:
    """Render visible OOXML text without executing fields or following external links."""

    def visit(element, root=False):
        local = element.tag.rsplit("}", 1)[-1]
        if local in {"del", "moveFrom", "instrText"} or (local == "p" and not root):
            return ""
        if local == "t":
            return element.text or ""
        if local == "tab":
            return "\t"
        if local in {"br", "cr"}:
            return "\n"
        if local == "noBreakHyphen":
            return "‑"
        text = "".join(visit(child) for child in element)
        if local == "hyperlink":
            relationship = relationships.get(element.get(qn("r:id")))
            target = str(relationship.target_ref) if relationship is not None else ""
            if re.match(r"https?://", target, re.I) and target not in text:
                text += f" <{target}>"
            elif target.lower().startswith("mailto:"):
                email = target[7:].split("?", 1)[0]
                if email not in text:
                    text += f" <{email}>"
        return text

    text = visit(paragraph, root=True)
    instructions = " ".join(paragraph.itertext(qn("w:instrText")))
    for target in re.findall(r'HYPERLINK\s+"(https?://[^\"]+)"', instructions, re.I):
        if target not in text:
            text += f" <{target}>"
    style = paragraph.find("./" + qn("w:pPr") + "/" + qn("w:pStyle"))
    style_name = style.get(qn("w:val"), "") if style is not None else ""
    numbered = paragraph.find("./" + qn("w:pPr") + "/" + qn("w:numPr")) is not None
    if text.strip() and (numbered or "listbullet" in style_name.lower()) and not BULLET_RE.match(text):
        text = "• " + text
    return text


def extract_docx_text(content: bytes) -> tuple[str, list[str]]:
    document = Document(io.BytesIO(content))
    warnings = []
    parts = []
    seen_parts = set()
    for section in document.sections:
        for header in (section.first_page_header, section.header, section.even_page_header):
            if header.part.partname not in seen_parts:
                seen_parts.add(header.part.partname)
                parts.append((header._element, header.part.rels))
    parts.append((document.element.body, document.part.rels))
    for section in document.sections:
        for footer in (section.first_page_footer, section.footer, section.even_page_footer):
            if footer.part.partname not in seen_parts:
                seen_parts.add(footer.part.partname)
                parts.append((footer._element, footer.part.rels))
    lines = []
    for element, relationships in parts:
        # Descendant paragraphs include table cells, content controls and text boxes in source order.
        for paragraph in element.iter(qn("w:p")):
            if any(parent.tag in {qn("w:del"), qn("w:moveFrom")} for parent in paragraph.iterancestors()):
                continue
            lines.append(_paragraph_text(paragraph, relationships))
    if document.element.xpath(".//w:del | .//w:ins | .//w:moveFrom | .//w:moveTo"):
        warnings.append(
            "Tracked changes found: proposals use the current visible text, excluding deletions. Review the original."
        )
    if document.element.xpath(".//w:drawing | .//w:pict"):
        warnings.append(
            "The DOCX contains drawings or images. Text inside supported text boxes is retained; image-only text requires review."
        )
    return "\n".join(lines), warnings


def _list_items(text: str) -> list[str]:
    """Split explicit lists without breaking AWS (EC2, S3) or qualification parentheses."""
    text = BULLET_RE.sub("", text).strip()
    if ":" in text:
        prefix, rest = text.split(":", 1)
        if prefix.casefold().strip() in {
            "programming",
            "programming languages",
            "languages",
            "frameworks",
            "tools",
            "technologies",
            "databases",
            "cloud",
            "cloud platforms",
            "libraries",
            "technical skills",
            "skills",
        }:
            text = rest.strip()
    result, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and char in "|;,":
            if text[start:index].strip():
                result.append(text[start:index].strip())
            start = index + 1
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def _entry_header(line: str) -> bool:
    if BULLET_RE.match(line):
        return False
    dates = DATE_RANGE_RE.search(line)
    dated_title = bool(dates and len(line[: dates.start()].strip(" -–—|").split()) >= 2)
    return dated_title or ("|" in line and len(line) < 300)


def propose_cv_facts(text: str, source: str) -> tuple[list[dict], dict]:
    proposals, seen, category, section, entry = [], set(), "imported", "Header", []
    blank_before = True
    sections_seen = []

    def add(kind, key, value, line_numbers, context=""):
        value = value.strip()
        identity = (kind, value)
        if not value or identity in seen:
            return
        if len(value) > 20000:
            # Reject oversized evidence rather than silently dropping a factual tail.
            raise ValueError("An extracted fact exceeds 20,000 characters. Split this source into smaller documents.")
        seen.add(identity)
        first, last = min(line_numbers), max(line_numbers)
        location = f"Extracted line {first}" if first == last else f"Extracted lines {first}–{last}"
        note = f"{location}. Section: {section}."
        if context:
            note += f" Context: {context[:500]}."
        note += " Verbatim source evidence; review before verifying. Dates, achievements and metrics are claims from the document."
        proposals.append(
            {
                "category": kind,
                "key": key[:300],
                "value": value,
                "source": source,
                "verification_status": "unverified",
                "locked": False,
                "notes": note,
            }
        )

    def flush():
        if entry:
            first = entry[0][1]
            add(
                category,
                BULLET_RE.sub("", first)[:300],
                "\n".join(value for _, value in entry),
                [number for number, _ in entry],
            )
            entry.clear()

    header_nonempty = 0
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            blank_before = True
            continue
        heading = section_name(line)
        if heading:
            flush()
            category, section = heading, line.strip(" :#")
            sections_seen.append({"category": category, "heading": section, "line": line_number})
            blank_before = True
            continue
        if category in {"skill", "language"}:
            for value in _list_items(line):
                add(category, value, value, [line_number], context=line if value != line else "")
        elif category == "imported" and not sections_seen and header_nonempty < 8:
            header_nonempty += 1
            # Only the contact header supplies personal form fields. Project/recruiter contacts do not.
            if (
                header_nonempty == 1
                and re.fullmatch(r"[^\W\d_][^\d|@:<>()]{1,79}", line, re.UNICODE)
                and 2 <= len(line.split()) <= 5
                and not re.search(r"\b(engineer|developer|resume|curriculum|vitae|currículum|cv|profile)\b", line, re.I)
            ):
                add("personal", "full_name", line, [line_number])
            else:
                for segment in re.split(r"\s*[|•]\s*", line):
                    segment = segment.strip()
                    if not segment:
                        continue
                    urls = URL_RE.findall(segment)
                    emails = EMAIL_RE.findall(segment)
                    phones = [
                        match.group().strip()
                        for match in PHONE_RE.finditer(URL_RE.sub("", EMAIL_RE.sub("", segment)))
                        if 8 <= len(re.sub(r"\D", "", match.group())) <= 16
                        and not DATE_RANGE_RE.search(match.group())
                        and (match.group().lstrip().startswith("+") or len(re.sub(r"\D", "", match.group())) >= 10)
                    ]
                    remainder = segment
                    if urls:
                        for url in urls:
                            remainder = remainder.replace(url, "")
                            if f"<{url}>" not in segment:
                                url = url.rstrip(".,;")
                                while url.endswith(")") and url.count(")") > url.count("("):
                                    url = url[:-1]
                            kind = (
                                "linkedin"
                                if re.search(r"https?://(?:www\.)?linkedin\.com/", url, re.I)
                                else "github"
                                if re.search(r"https?://(?:www\.)?github\.com/", url, re.I)
                                else "portfolio"
                            )
                            add("link", kind, url, [line_number], context=segment)
                    if emails:
                        for email in emails:
                            remainder = remainder.replace(email, "")
                            add("personal", "email", email, [line_number])
                    if phones:
                        for phone in phones:
                            remainder = remainder.replace(phone, "")
                            add("personal", "phone", phone, [line_number])
                    if re.fullmatch(r"[\wÀ-ÿ .'-]+,\s*[A-Z]{2,3}", segment):
                        add("personal", "location", segment, [line_number])
                    elif not (urls or emails or phones) and not re.fullmatch(
                        r"(?:curriculum vitae|résumé|resume|cv)", segment, re.I
                    ):
                        add("imported", segment, segment, [line_number])
                    elif urls or emails or phones:
                        remainder = re.sub(
                            r"\b(email|e-mail|phone|mobile|tel|telephone|linkedin|github|portfolio|website)\b",
                            "",
                            remainder,
                            flags=re.I,
                        ).strip(" <>:;.,()[]\t")
                        if remainder:
                            add("imported", "contact_context", remainder, [line_number], context=segment)
        else:
            # Paragraphs stay attached to the explicit role/qualification/project header.
            # A new short unbulleted title after a blank starts another entry; wrapped bullets do not.
            new_title = (
                blank_before and len(line) < 180 and not BULLET_RE.match(line) and not line.endswith((".", ":", ";"))
            )
            if entry and (_entry_header(line) or new_title):
                flush()
            entry.append((line_number, line))
        blank_before = False
    flush()
    if len(proposals) > 2000:
        raise ValueError("This document proposes more than 2,000 facts. Split it into smaller source documents.")
    return proposals, {
        "parser_version": PARSER_VERSION,
        "source_lines": len(text.splitlines()),
        "sections": sections_seen,
        "facts_by_category": dict(Counter(item["category"] for item in proposals)),
        "fact_count": len(proposals),
    }
