from .engine import CandidateEngine

COVERAGE = {
    "Identity": ["Full name", "First name", "Surname", "Nationality"],
    "Contact": ["Email", "Phone", "LinkedIn", "Portfolio"],
    "Education": ["Current university", "Highest completed degree", "Degree classification", "Degree average"],
    "Skills": ["Have you used Python?", "Have you used Java?", "Have you used React?", "Have you used SQL?"],
    "Experience": ["Current employer", "Most recent employer", "Largest team led", "Have you taught programming?"],
    "Projects": ["Describe a project", "Describe ownership of a product", "How do you use AI?"],
    "Availability": ["Earliest start date", "Expected graduation year", "Current notice period"],
    "Work authorization": [
        "Current visa type",
        "Current right to work in the UK",
        "Will you require sponsorship in the future?",
    ],
    "Salary": ["Current salary", "Expected salary"],
    "Demographic policy": ["Describe your gender"],
}


def knowledge_view(db):
    engine = CandidateEngine(db)
    k = engine.knowledge
    categories = []
    for category, questions in COVERAGE.items():
        answers = [
            engine.answer_question(q, {"required": False}, {"location": "London", "country": "United Kingdom"})
            for q in questions
        ]
        missing = [q for q, a in zip(questions, answers) if a["answer"] is None and not a["leave_blank"]]
        categories.append(
            {
                "category": category,
                "total": len(questions),
                "supported": len(questions) - len(missing),
                "percentage": round(100 * (len(questions) - len(missing)) / len(questions)),
                "missing": missing,
            }
        )
    return {
        "as_of": k.today.isoformat(),
        "version": k.version,
        "entities": list(k.entities.values()),
        "facts": k.rows,
        "relationships": k.links,
        "policies": k.policies,
        "conflicts": k.conflicts(),
        "coverage": categories,
        "coverage_note": "Measured against listed diagnostic concepts, not all possible application questions.",
        "derived": [
            engine.answer_question(q, {}, {})
            for q in [
                "Current university",
                "Highest completed degree",
                "Earliest start date",
                "Expected graduation year",
            ]
        ],
    }
