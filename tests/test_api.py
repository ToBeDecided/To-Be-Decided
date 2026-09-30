import json

import pytest
from fastapi.testclient import TestClient

from internmatch import ai, config
from internmatch.server import create_app

from .conftest import FIXTURES


@pytest.fixture
def client(store):
    return TestClient(create_app(store), base_url="http://127.0.0.1")


def test_index_and_health(client):
    res = client.get("/")
    assert res.status_code == 200 and "Pre-Law" in res.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/health").json()["app"] == "internmatch"


def test_rejects_foreign_hosts_and_cross_site_posts(store):
    evil = TestClient(create_app(store), base_url="http://evil.example")
    assert evil.get("/api/health").status_code == 403
    local = TestClient(create_app(store), base_url="http://127.0.0.1:8000")
    res = local.post("/api/settings", json={"values": {"usajobs_email": "x@y.z"}},
                     headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    ok = local.post("/api/settings", json={"values": {"usajobs_email": "x@y.z"}},
                    headers={"Origin": "http://127.0.0.1:8000"})
    assert ok.status_code == 200
    lan = TestClient(create_app(store, allow_any_host=True), base_url="http://192.168.1.20:8000")
    assert lan.get("/api/health").status_code == 200


def test_meta(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: False)
    body = client.get("/api/meta").json()
    assert body["listings"]["count"] == 15
    assert body["listings"]["sources"]["themuse"]["enabled"] is True
    assert body["listings"]["sources"]["usajobs"]["enabled"] is False
    assert set(body["tracks"]) == {"Pre-Law", "Business", "Humanities"}
    assert any(t["term"] == "Summer 2027" for t in body["terms"])
    assert body["settings"]["usajobs_api_key"]["configured"] is False
    assert body["ai"]["available"] is False


def test_settings_roundtrip(client):
    res = client.post("/api/settings", json={"values": {"usajobs_email": "me@school.edu",
                                                        "usajobs_api_key": "secret-key-1234"}})
    assert res.status_code == 200
    settings = res.json()["settings"]
    assert settings["usajobs_email"]["value"] == "me@school.edu"
    assert settings["usajobs_api_key"]["value"] == "…1234"  # never echo a secret back
    assert config.get("usajobs_api_key") == "secret-key-1234"
    assert client.post("/api/settings", json={"values": {"nope": "x"}}).status_code == 422
    client.post("/api/settings", json={"values": {"usajobs_api_key": ""}})
    assert config.get("usajobs_api_key") is None


def test_analyze_with_upload(client):
    profile = {"target_terms": ["Summer 2027"], "locations": ["Boston"], "target_tracks": ["Pre-Law"]}
    with open(FIXTURES / "prelaw_resume.pdf", "rb") as fh:
        res = client.post("/api/analyze", files={"resume": ("resume.pdf", fh, "application/pdf")},
                          data={"profile": json.dumps(profile), "enrich": "false", "top": "5"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["resume"]["overall"] >= 85
    assert body["profile"]["track_fit"][0]["category"] == "Pre-Law"
    assert body["recommended"][0]["posting"]["company"] == "Hartley Legal Aid"
    assert body["recommended"][0]["tier"] == "Likely"
    assert {m["posting"]["category"] for m in body["matches"]} <= {"Legal", "Government & Policy",
                                                                   "Nonprofit & Advocacy"}
    assert all(m["posting"]["description"] == "" for m in body["matches"])
    assert "Maya Thompson" in body["resume_text"]


def test_analyze_with_pasted_text(client, weak_text):
    res = client.post("/api/analyze", data={"resume_text": weak_text, "profile": "{}", "enrich": "false"})
    assert res.status_code == 200 and res.json()["resume"]["overall"] < 45


def test_analyze_validation(client):
    assert client.post("/api/analyze", data={"resume_text": "too short"}).status_code == 400
    res = client.post("/api/analyze", data={"resume_text": "x" * 100, "profile": '{"gpa": 9}'})
    assert res.status_code == 422
    res = client.post("/api/analyze", files={"resume": ("resume.pages", b"PK\x03\x04notreally", "application/zip")})
    assert res.status_code == 400 and "Export To" in res.json()["detail"]


def test_check_posting(client, prelaw_text):
    res = client.post("/api/check", data={"title": "Legal Intern", "company": "Legal Aid Society",
                                           "description": "Legal research on Westlaw and client intake. $17/hour.",
                                           "location": "New York, NY", "resume_text": prelaw_text})
    assert res.status_code == 200
    body = res.json()
    assert body["match"]["tier"] == "Likely" and body["blocker"] is None
    assert body["match"]["posting"]["pay"] == "paid"
    blocked = client.post("/api/check", data={"title": "Summer Associate", "description": "For 2L law students.",
                                               "resume_text": prelaw_text}).json()
    assert "law students" in blocked["blocker"]
    assert client.post("/api/check", data={"title": " ", "resume_text": prelaw_text}).status_code == 400


def test_ai_review_requires_key(client, monkeypatch, prelaw_text):
    monkeypatch.setattr(ai, "available", lambda: False)
    res = client.post("/api/ai-review", json={"resume_text": prelaw_text})
    assert res.status_code == 503 and "Settings" in res.json()["detail"]


def test_ai_review_success(client, monkeypatch, prelaw_text):
    monkeypatch.setattr(ai, "available", lambda: True)
    seen = {}

    def fake_review(resume_text, profile, report, matches):
        seen["n"] = len(matches)
        return ai.AIReview(score=80, summary="Solid.", strengths=["a"], weaknesses=["b"], bullet_rewrites=[],
                           keywords_to_add=[], job_assessments=[], next_steps=["c"])

    monkeypatch.setattr(ai, "review", fake_review)
    analysis = client.post("/api/analyze", data={"resume_text": prelaw_text, "enrich": "false"}).json()
    res = client.post("/api/ai-review", json={"resume_text": prelaw_text, "profile": {},
                                              "report": analysis["resume"], "matches": analysis["recommended"][:3]})
    assert res.status_code == 200 and res.json()["score"] == 80 and seen["n"] == 3


def test_ai_errors_map_to_http_status(client, monkeypatch, prelaw_text):
    monkeypatch.setattr(ai, "available", lambda: True)

    def boom(*_):
        raise ai.AIError("Rate limited", status=429)

    monkeypatch.setattr(ai, "review", boom)
    assert client.post("/api/ai-review", json={"resume_text": prelaw_text}).status_code == 429
