"""Purpose-led prose built only from active candidate evidence.

Job text selects the subject and relevant evidence; it is never copied into a
claim about the candidate. The result is a draft, not a newly verified fact.
"""

from __future__ import annotations

import re
from html import unescape

from ..automation.answers import normalize


THEMES = {
    "machine_learning": {
        "pattern": r"machine learning|computer vision|neural|\bcnn\b|\bml\b|model training|model evaluation|predictive",
        "focus": "turn machine-learning ideas into useful, well-evaluated systems",
        "area": "machine learning",
    },
    "data": {
        "pattern": r"\bdata\b|analytics|analysis|etl|sql|warehouse|pandas|numpy|dataset|\bcsv\b",
        "focus": "make data easier to use and turn analysis into practical decisions",
        "area": "data engineering and analysis",
    },
    "backend": {
        "pattern": r"back[ -]?end|\bapi\b|\bapis\b|distributed|concurrency|microservice|server|fastify|java\b",
        "focus": "build dependable services and improve how systems work together",
        "area": "backend development",
    },
    "frontend": {
        "pattern": r"front[ -]?end|\breact\b|\bvue\b|interface|user experience|responsive|bootstrap|javascript|tailwind",
        "focus": "build clear, responsive interfaces that make complex functionality easier to use",
        "area": "frontend development",
    },
    "ai": {
        "pattern": r"\bai\b|artificial intelligence|\bllm\b|generative|\bnlp\b|language model|content generation|automation",
        "focus": "apply AI to practical problems and integrate it into usable software",
        "area": "applied AI",
    },
    "cloud": {
        "pattern": r"\bcloud\b|infrastructure|\baws\b|\bec2\b|\bs3\b|kubernetes|deployment|devops|mlops|reliability",
        "focus": "connect software development with reliable deployment and operation",
        "area": "cloud and infrastructure",
    },
    "software": {
        "pattern": r"software|programming|engineering|developer|development|coding",
        "focus": "develop useful software and work through practical engineering problems",
        "area": "software development",
    },
}

PURPOSES = {"MOTIVATION", "COMPANY_INTEREST", "ROLE_INTEREST", "CAREER_GOALS", "ADDITIONAL_CONTEXT", "COVER_LETTER"}

DOMAINS = (
    (r"\b(?:rail|trains|train travel|travel platform|travel products|transport|journeys)\b", "travel and transport"),
    (
        r"\b(?:fraud|insurer|insurance platform|insurance industry|claims processing)\b",
        "insurance and fraud prevention",
    ),
    (r"\b(?:banking|financial services|trading platform|investment management)\b", "financial services"),
    (r"\b(?:patient|clinical|healthcare|health care|medical|drug discovery)\b", "healthcare"),
    (r"\b(?:educational platform|educational product|learning platform|teaching tools)\b", "education"),
    (r"\b(?:climate|carbon|renewable|sustainability)\b", "climate and sustainability"),
)

# These describe work, rather than particular employers or exact form questions.
RESPONSIBILITIES = (
    ("search", r"\bsearch\b|information retrieval", "improve search relevance"),
    ("recommendations", r"recommendation|recommender", "build useful recommendation systems"),
    (
        "evaluation",
        r"evaluat|experimentation|experiment design|a/b test",
        "evaluate ideas and models against practical results",
    ),
    (
        "pipelines",
        r"data pipeline|\betl\b|data ingestion|data transformation",
        "make data pipelines dependable and easier to use",
    ),
    (
        "analysis",
        r"exploratory data|\beda\b|analytics|business intelligence",
        "turn data analysis into useful decisions",
    ),
    (
        "generative",
        r"\bllm\b|language model|generative|agentic|\brag\b",
        "turn language-model capabilities into useful product features",
    ),
    (
        "services",
        r"\bapis?\b|backend service|distributed system|microservice",
        "build reliable services and integrations",
    ),
    (
        "interfaces",
        r"user interface|user experience|responsive|frontend",
        "make software easier to use through clear interfaces",
    ),
    (
        "operations",
        r"deployment|production system|reliability|observability|infrastructure",
        "bring software into reliable everyday operation",
    ),
    (
        "performance",
        r"latency|performance optimi|scalability|high throughput",
        "improve system performance as usage grows",
    ),
)

ACTIVITIES = (
    (
        "llm",
        r"fine[- ]tun.{0,35}(?:llm|language model)|(?:llm|language model).{0,35}fine[- ]tun",
        "fine-tuning language models",
    ),
    ("rag", r"\brag\b|retrieval[- ]augmented", "retrieval-augmented generation"),
    ("evaluation", r"evaluation metrics|bleu|rouge|perplexity|model evaluation", "evaluating model outputs"),
    ("pipelines", r"automat.{0,30}data pipeline|data pipeline.{0,30}automat", "automating data pipelines"),
    ("vision", r"computer vision|object detection|\bcnn\b|\byolo\b", "computer vision"),
    (
        "content",
        r"ai content generation|generat.{0,30}(?:educational|learning)|educational.{0,30}(?:ai|generat)",
        "AI-assisted educational content generation",
    ),
    ("nlp", r"\bnlp\b|natural language processing|\bspacy\b", "natural-language processing"),
    (
        "models",
        r"(?:train|develop|design).{0,35}(?:deep learning|machine learning|neural).{0,15}model",
        "developing machine-learning models",
    ),
    ("backend", r"\bapi\b|backend|back end", "backend development"),
    ("frontend", r"responsive.{0,25}(?:vue|interface|website)|frontend|front end", "frontend development"),
    ("data", r"analys.{0,30}(?:data|pandas)|conducted eda", "data analysis"),
)


def value(record, key, default=None):
    return record.get("fields", {}).get(key, {}).get("value", default)


def plain(value_):
    if isinstance(value_, list):
        return ", ".join(str(x) for x in value_)
    return str(value_ or "")


def clean_context(value_):
    """Treat scraped descriptions as text, with no instruction-following path."""
    text = re.sub(r"<[^>]+>", " ", unescape(plain(value_)))
    return re.sub(r"\s+", " ", text).strip()


def role_context(job):
    title = normalize(clean_context(job.get("title")))
    description = normalize(clean_context(job.get("description")))
    domain_text = " ".join(
        part
        for part in re.split(r"[.!?\n•]+", plain(job.get("description")))
        if not re.search(
            r"benefit|private healthcare|health insurance|dental|equal opportunit|"
            r"diversity|disabilit|reasonable adjustment|parental leave",
            part,
            re.I,
        )
    )
    scored = []
    for key, theme in THEMES.items():
        # Repetition in an advertisement must not drown out its actual title.
        title_hits = set(re.findall(theme["pattern"], title))
        description_hits = set(re.findall(theme["pattern"], description))
        score = len(title_hits) * 4 + min(6, len(description_hits))
        if score:
            scored.append((score, key))
    scored.sort(key=lambda pair: (-pair[0], pair[1] == "software", pair[1]))
    themes = [key for _, key in scored]
    # A generic title should allow specific responsibilities to lead the draft.
    if themes and themes[0] == "software" and len(themes) > 1 and description:
        themes.append(themes.pop(0))
    return {
        "themes": themes,
        "primary": themes[0] if themes else None,
        "description_available": bool(description),
        "title": clean_context(job.get("title")),
        "company": clean_context(job.get("company")),
        "domain": next((label for pattern, label in DOMAINS if re.search(pattern, normalize(domain_text))), None),
        "responsibilities": [key for key, pattern, _ in RESPONSIBILITIES if re.search(pattern, description)],
    }


def record_text(record):
    return normalize(
        " ".join(
            plain(value(record, key))
            for key in ("role", "story", "situation", "task", "action", "result", "tags", "technologies")
        )
    )


def list_phrase(items):
    items = list(dict.fromkeys(items))
    if len(items) < 2:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def sentence(text):
    return text.rstrip(" .") + "."


def story_facts(record):
    fields = record.get("fields", {})
    star = [fields[key] for key in ("situation", "task", "action", "result") if key in fields]
    return star or ([fields["story"]] if fields.get("story") else [])


def concise_story(record, context):
    """Extract a complete relevant clause and make CV bullets readable as prose."""
    original = " ".join(plain(f["value"]) for f in story_facts(record))
    if "\n" not in original and "•" not in original and len(original.split()) <= 75:
        return sentence(original)
    chunks = [part.strip(" \t•-*.") for part in re.split(r"\n|•|(?<=[.!?])\s+(?=[A-Z])", original)]
    chunks = [part for part in chunks if part and "|" not in part and 5 <= len(part.split()) <= 90]
    if not chunks:
        return None

    def score(chunk):
        text = normalize(chunk)
        return sum(
            max(1, 10 - index * 2)
            for index, theme in enumerate(context["themes"])
            if re.search(THEMES[theme]["pattern"], text)
        ) + sum(
            3 for key, pattern, _ in RESPONSIBILITIES if key in context["responsibilities"] and re.search(pattern, text)
        )

    selected = max(chunks, key=score)
    if re.match(
        r"(?:built|developed|designed|trained|fine.tuned|conducted|automated|led|implemented|created|supported)\b",
        selected,
        re.I,
    ):
        prefix = f"At {record['name']}, I " if record.get("kind") == "experience" else "I "
        selected = prefix + selected[0].lower() + selected[1:]
    return sentence(selected)


def supported_activities(records, context, *, ai_only=False):
    matches = []
    priorities = {key: index for index, key in enumerate(context["responsibilities"])}
    for order, (key, pattern, label) in enumerate(ACTIVITIES):
        if ai_only and key in {"backend", "frontend", "data"}:
            continue
        supporting = [r for r in records if story_facts(r) and re.search(pattern, record_text(r))]
        if supporting:
            supporting.sort(key=lambda r: r.get("kind") != "experience")
            record = supporting[0]
            score = (8 if record.get("kind") == "experience" else 0) + (5 if key in priorities else 0)
            matches.append((score, order, label, record))
    matches.sort(key=lambda match: (-match[0], match[1]))
    return [(label, record) for _, _, label, record in matches]


def relevant_records(knowledge, semantic, context):
    intent = semantic["intent"]
    target = {
        "LEADERSHIP": "leadership",
        "OWNERSHIP": "ownership",
        "TEAMWORK": "teamwork",
        "FAILURE": "failure",
        "CHALLENGE": "challenge",
        "PROBLEM_SOLVING": "problem solving",
    }.get(intent)
    query = normalize(semantic.get("text", ""))
    ranked = []
    for record in knowledge.records("experience") + knowledge.records("project"):
        if not story_facts(record):
            continue
        text = record_text(record)
        tags = normalize(plain(value(record, "tags")))
        if target and target not in tags:
            # A difficult/failed outcome cannot be invented from an ordinary project.
            if intent in {"FAILURE", "CHALLENGE", "PROBLEM_SOLVING"}:
                concepts = {
                    "FAILURE": ("failure", "lesson"),
                    "CHALLENGE": ("challenge",),
                    "PROBLEM_SOLVING": ("problem", "challenge"),
                }[intent]
                if not any(value(record, concept) for concept in concepts):
                    continue
            else:
                continue
        score = 0
        for index, theme in enumerate(context["themes"]):
            if re.search(THEMES[theme]["pattern"], text):
                score += max(1, 10 - 3 * index)
        for technology in value(record, "technologies", []):
            if normalize(technology) and normalize(technology) in query:
                score += 8
        if target and target in tags:
            score += 20
        # Prefer professional evidence when relevance is otherwise equal.
        score += 1 if record.get("kind") == "experience" else 0
        ranked.append((score, record))
    ranked.sort(key=lambda item: (-item[0], item[1]["name"]))
    if ranked and ranked[0][0] >= 5 and context["themes"]:
        # A second paragraph must earn its relevance; a professional but unrelated
        # record should not displace a directly relevant project or pad a draft.
        threshold = max(3, ranked[0][0] * 0.45)
        ranked = [item for item in ranked if item[0] >= threshold]
    return [record for _, record in ranked]


def evidence_areas(records, themes):
    result = []
    combined = " ".join(record_text(record) for record in records)
    for key in list(dict.fromkeys([*themes, *THEMES])):
        if key == "software" and result:
            continue
        if re.search(THEMES[key]["pattern"], combined):
            result.append(THEMES[key]["area"])
    return result[:3]


def compose_narrative(knowledge, semantic, job=None):
    """Return draft prose, exact supporting facts, rules and role-selection context.

    Accepts a Knowledge instance, so expired, unconfirmed and conflicting values
    have already been excluded by its authoritative record accessor.
    """
    job = job or {}
    intent = semantic["intent"]
    context = role_context(job)
    context["purpose"] = intent
    result = {"answer": None, "facts": [], "rules": [], "context": context}
    people = knowledge.records("person")
    interests = people[0].get("fields", {}).get("professional_interests") if len(people) == 1 else None
    records = relevant_records(knowledge, semantic, context)
    chosen = records[:2] if intent in PURPOSES else records[:1]
    context["evidence_entities"] = [record["id"] for record in chosen]
    focus = THEMES[context["primary"]]["focus"] if context["primary"] else None
    responsibility_prose = [phrase for key, _, phrase in RESPONSIBILITIES if key in context["responsibilities"]]
    specific_focus = list_phrase(responsibility_prose[:2]) if responsibility_prose else None
    if focus and context["domain"]:
        focus += " in " + context["domain"]
    if specific_focus and context["domain"]:
        specific_focus += " in " + context["domain"]

    def include(record, keys=None):
        facts = [record["fields"][key] for key in keys if key in record["fields"]] if keys else story_facts(record)
        result["facts"].extend(facts)

    def use_records(selected):
        for record in selected:
            include(record)
            include(record, ("tags", "technologies"))
            result["rules"].append("relevant_verified_story:" + record["id"])

    if intent in {"AI_USAGE", "LLM_USAGE"}:
        # Generalise an actual coding-assistance fact, without claiming invented
        # prompting habits, research use, productivity gains or review routines.
        all_records = knowledge.records("experience") + knowledge.records("project")
        assistants = [
            r
            for r in all_records
            if re.search(
                r"ai coding assist|ai[- ]assisted (?:coding|software)|coding (?:assistant|copilot)|copilot|llm.{0,35}(?:coding|development)",
                record_text(r),
            )
        ]
        activities = supported_activities(all_records, context, ai_only=True)[:3]
        parts = []
        if assistants:
            ongoing = [r for r in assistants if value(r, "ongoing") is True]
            assistant = (ongoing or assistants)[0]
            parts.append(
                "I use AI coding assistance as part of my software development workflow."
                if ongoing
                else "I have used AI coding assistance in software development."
            )
            use_records([assistant])
            include(assistant, ("ongoing",))
        if activities:
            parts.append(
                "My broader hands-on AI experience includes " + list_phrase([label for label, _ in activities]) + "."
            )
            use_records([record for _, record in activities])
        if parts:
            result["answer"] = " ".join(parts)
            result["rules"].append("generalise_verified_ai_activities_without_inventing_daily_habits")
        context["evidence_entities"] = list(
            dict.fromkeys(f["entity_id"] for f in result["facts"] if f.get("entity_id"))
        )
    elif intent in PURPOSES:
        if not chosen:
            return result
        use_records(chosen)
        areas = evidence_areas(chosen, context["themes"])
        if not areas:
            # Freeform prose may be valid without a known technical taxonomy.
            # Preserve it as evidence, rather than inventing a specialism.
            areas = ["the work described in my experience"]
        destination = (
            f"the {context['title']} role at {context['company']}"
            if context["title"] and context["company"]
            else f"the {context['title']} role"
            if context["title"]
            else "this role"
        )
        if interests:
            result["facts"].append(interests)
            opening = (
                f"I'm interested in {destination} because it would let me {specific_focus or focus}."
                if specific_focus or focus
                else f"My interest in {plain(interests['value'])} is central to my application for {destination}."
            )
        else:
            opening = (
                f"My background in {list_phrase(areas)} is relevant to the opportunity to {specific_focus or focus}."
                if specific_focus or focus
                else f"I am applying for {destination} with experience in {list_phrase(areas)}."
            )
        if intent == "CAREER_GOALS":
            # Express the connection to known interests, not a fabricated career plan.
            opening = (f"My interests are in {plain(interests['value'])}. " if interests else "") + opening
        activities = supported_activities(chosen, context)[:3]
        background = list_phrase([label for label, _ in activities]) if activities else list_phrase(areas)
        fit = f"My experience in {background} would provide a practical foundation for this work."
        if intent == "COVER_LETTER":
            evidence = [text for record in chosen if (text := concise_story(record, context))]
            result["answer"] = "\n\n".join([opening, *evidence])
        else:
            # Motivation/additional-context questions are not requests to paste a project story.
            result["answer"] = opening + " " + fit
        result["rules"].extend(
            [
                "answer_application_purpose_before_examples",
                "job_context_selects_verified_evidence",
                "employer_context_never_becomes_candidate_fact",
            ]
        )
    elif chosen:
        use_records(chosen)
        result["answer"] = " ".join(plain(f["value"]) for f in story_facts(chosen[0]))
        result["rules"].append("question_intent_selects_evidence_story")
    result["facts"] = list({fact["id"]: fact for fact in result["facts"]}.values())
    result["rules"] = list(dict.fromkeys(result["rules"]))
    return result


class SelectedEvidence:
    """Preserve a document's explicit evidence selection, including older CV facts."""

    def __init__(self, knowledge, flat_facts=(), allowed_ids=None):
        self.knowledge = knowledge
        self.allowed_ids = set(allowed_ids) if allowed_ids is not None else None
        self.flat_facts = [f for f in flat_facts if f.get("category") != "knowledge"]

    def records(self, kind):
        records = []
        if self.knowledge:
            for record in self.knowledge.records(kind):
                fields = {
                    key: fact
                    for key, fact in record["fields"].items()
                    if self.allowed_ids is None or fact["id"] in self.allowed_ids
                }
                if fields:
                    records.append({**record, "fields": fields})
        if kind in {"project", "experience"}:
            records.extend(
                {
                    "id": "flat:" + fact["id"],
                    "kind": kind,
                    "name": fact["key"],
                    "fields": {"story": fact},
                }
                for fact in self.flat_facts
                if fact.get("category") == kind
            )
        if kind == "person":
            fields = {
                fact["key"]: fact for fact in self.flat_facts if fact["key"] in {"full_name", "professional_interests"}
            }
            if fields and not records:
                records.append({"id": "selected-person", "kind": "person", "name": "Candidate", "fields": fields})
            elif fields and len(records) == 1:
                records[0] = {**records[0], "fields": {**fields, **records[0]["fields"]}}
        return records


def compose_cover_letter(knowledge, job, *, flat_facts=(), allowed_ids=None):
    view = SelectedEvidence(knowledge, flat_facts, allowed_ids)
    result = compose_narrative(view, {"intent": "COVER_LETTER", "text": "cover letter"}, job)
    if result["answer"] is None:
        return result
    result["answer"] = "Dear hiring team,\n\n" + result["answer"] + "\n\nThank you for considering my application."
    people = view.records("person")
    name = people[0]["fields"].get("full_name") if len(people) == 1 else None
    if name:
        result["answer"] += "\n\n" + plain(name["value"])
        result["facts"].append(name)
    result["facts"] = list({fact["id"]: fact for fact in result["facts"]}.values())
    return result
