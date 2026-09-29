from jobagent.rules import matches_rule


def test_rules_narrow_jobs_without_overriding_filters():
    job = {
        "match_score": 92,
        "location": "London, UK",
        "title": "Machine Learning Engineer",
        "ats": "greenhouse",
        "status": "SCORED",
    }
    assert matches_rule(
        job, {"min_match": 85, "location": "London", "role_contains": "Machine Learning", "ats": "greenhouse"}
    )
    assert not matches_rule(job, {"min_match": 95})
    assert not matches_rule(job, {"location": "Berlin"})
    assert not matches_rule(job | {"status": "IGNORED"}, {})
    assert not matches_rule(job | {"match_details": {"excluded": True}}, {})
