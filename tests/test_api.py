import json

import pytest
from fastapi.testclient import TestClient

from internmatch import ai
from internmatch.server import create_app

from .conftest import FIXTURES


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def test_index_is_served(client):
    res = client.get("/")
    assert res.status_code == 200 and "Internship Matcher" in res.text
    assert client.get("/app.js").status_code == 200


def test_meta(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: False)
    body = client.get("/api/meta").json()
    assert body["listings"]["count"] == 15
    assert any(t["term"] == "Summer 2027" for t in body["terms"])
    assert "Software" in body["categories"]
    assert body["ai"]["available"] is False


def test_analyze_with_upload(client):
    profile = {"target_terms": ["Summer 2027"], "locations": ["NYC"]}
    with open(FIXTURES / "sample_resume.pdf", "rb") as fh:
        res = client.post("/api/analyze", files={"resume": ("resume.pdf", fh, "application/pdf")},
                          data={"profile": json.dumps(profile), "enrich": "false", "top": "5"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["resume"]["overall"] >= 85
    assert len(body["recommended"]) == 5
    assert body["stats"]["eligible"] == len(body["matches"])
    assert body["recommended"][0]["tier"] == "Likely"
    assert all(m["posting"]["description"] == "" for m in body["matches"])
    assert "Jordan Lee" in body["resume_text"]


def test_analyze_with_pasted_text(client, weak_text):
    res = client.post("/api/analyze", data={"resume_text": weak_text, "profile": "{}", "enrich": "false"})
    assert res.status_code == 200
    assert res.json()["resume"]["overall"] < 55


def test_analyze_validation(client):
    assert client.post("/api/analyze", data={"resume_text": "too short"}).status_code == 400
    res = client.post("/api/analyze", data={"resume_text": "x" * 100, "profile": '{"gpa": 9}'})
    assert res.status_code == 422
    res = client.post("/api/analyze", files={"resume": ("old.doc", b"\xd0\xcf\x11\xe0" * 20, "application/msword")})
    assert res.status_code == 400 and ".doc" in res.json()["detail"]


def test_ai_review_requires_key(client, monkeypatch, sample_text):
    monkeypatch.setattr(ai, "available", lambda: False)
    res = client.post("/api/ai-review", json={"resume_text": sample_text})
    assert res.status_code == 503 and "ANTHROPIC_API_KEY" in res.json()["detail"]


def test_ai_review_success(client, monkeypatch, sample_text):
    monkeypatch.setattr(ai, "available", lambda: True)
    seen = {}

    def fake_review(resume_text, profile, report, matches):
        seen["n"] = len(matches)
        return ai.AIReview(score=80, summary="Solid.", strengths=["a"], weaknesses=["b"], bullet_rewrites=[],
                           keywords_to_add=[], job_assessments=[], next_steps=["c"])

    monkeypatch.setattr(ai, "review", fake_review)
    analysis = client.post("/api/analyze", data={"resume_text": sample_text, "enrich": "false"}).json()
    res = client.post("/api/ai-review", json={"resume_text": sample_text, "profile": {},
                                              "report": analysis["resume"], "matches": analysis["recommended"][:3]})
    assert res.status_code == 200 and res.json()["score"] == 80
    assert seen["n"] == 3


def test_ai_errors_map_to_http_status(client, monkeypatch, sample_text):
    monkeypatch.setattr(ai, "available", lambda: True)

    def boom(*_):
        raise ai.AIError("Rate limited", status=429)

    monkeypatch.setattr(ai, "review", boom)
    res = client.post("/api/ai-review", json={"resume_text": sample_text})
    assert res.status_code == 429
