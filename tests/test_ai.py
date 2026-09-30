from types import SimpleNamespace

import pytest

from internmatch import ai, config
from internmatch.matcher import build_candidate, rank, recommend
from internmatch.models import Profile
from internmatch.resume import parse_resume, score_resume


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


@pytest.fixture
def analysis(postings, prelaw_text):
    profile = Profile(target_tracks=["Pre-Law"])
    pr = parse_resume(prelaw_text)
    report = score_resume(pr, profile)
    matches, _ = rank(postings, build_candidate(pr, report, profile))
    return profile, report, recommend(matches, n=5)


def make_review(**overrides):
    base = dict(score=130, summary="Strong.", strengths=["Mock trial"], weaknesses=["Few metrics"],
                bullet_rewrites=[ai.BulletRewrite(original="Helped x", improved="Drafted x", why="verb")],
                keywords_to_add=["Bluebook"], job_assessments=[], next_steps=["Apply"])
    base.update(overrides)
    return ai.AIReview(**base)


def test_review_request_shape(analysis, prelaw_text):
    profile, report, recs = analysis
    assessments = [ai.JobAssessment(posting_id=recs[0].posting.id, verdict="Likely", reasoning="fits"),
                   ai.JobAssessment(posting_id="made-up", verdict="Reach", reasoning="?")]
    client, messages = fake_client(SimpleNamespace(stop_reason="end_turn",
                                                   parsed_output=make_review(job_assessments=assessments)))
    result = ai.review(prelaw_text, profile, report, recs, client=client)

    kw = messages.kwargs
    assert kw["model"] == ai.MODEL and kw["output_format"] is ai.AIReview
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw and "temperature" not in kw
    assert "law" in kw["system"].lower()
    prompt = kw["messages"][0]["content"]
    assert "Maya Thompson" in prompt and recs[0].posting.id in prompt and "<automated_resume_check>" in prompt
    assert result.score == 100  # clamped
    assert [j.posting_id for j in result.job_assessments] == [recs[0].posting.id]  # unknown ids dropped


def test_review_handles_refusal_and_truncation(analysis, prelaw_text):
    profile, report, recs = analysis
    client, _ = fake_client(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    with pytest.raises(ai.AIError, match="declined"):
        ai.review(prelaw_text, profile, report, recs, client=client)
    client, _ = fake_client(SimpleNamespace(stop_reason="max_tokens", parsed_output=None))
    with pytest.raises(ai.AIError, match="incomplete"):
        ai.review(prelaw_text, profile, report, recs, client=client)


def test_review_rejects_empty_resume():
    with pytest.raises(ai.AIError):
        ai.review("   ", Profile(), client=object())


def test_available_from_settings_or_env(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert ai.available() is False
    config.save({"anthropic_api_key": "sk-ant-from-settings"})
    assert ai.available() is True
    config.save({"anthropic_api_key": None})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert ai.available() is True
