"""Optional AI review powered by Claude.

The rule-based engine works fully offline. When an Anthropic API key is
available, this module adds a recruiter-style critique of the resume, bullet
rewrites, and a second opinion on the top matches in a single API call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import config
from .models import Match, Profile, ResumeReport

MODEL = os.environ.get("INTERNMATCH_MODEL", "claude-opus-5-5")
MAX_POSTINGS = 20

SYSTEM_PROMPT = """You are a senior university recruiter and pre-professional advisor who has screened \
thousands of internship applications in law and government, finance and consulting, marketing and \
communications, media and publishing, museums and the arts, education, and nonprofits. You are reviewing \
one student's resume and a shortlist of internships an automated matcher picked for them.

Ground every statement in the resume text you are given: never invent experience, employers, numbers or \
skills. When a rewrite needs a metric the resume doesn't state, use a bracketed placeholder such as \
[X%] or [N users] so the student fills in the true value. Be candid and specific, and phrase feedback \
so the student can act on it today."""


class BulletRewrite(BaseModel):
    original: str
    improved: str
    why: str


class JobAssessment(BaseModel):
    posting_id: str
    verdict: Literal["Likely", "Target", "Reach"]
    reasoning: str


class AIReview(BaseModel):
    score: int
    summary: str
    strengths: list[str]
    weaknesses: list[str]
    bullet_rewrites: list[BulletRewrite]
    keywords_to_add: list[str]
    job_assessments: list[JobAssessment]
    next_steps: list[str]


class AIError(RuntimeError):
    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def available() -> bool:
    """True if Anthropic credentials are likely configured (Settings, env var, or `ant auth login`)."""
    if config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    profile_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "anthropic"
    return profile_dir.is_dir() and any(profile_dir.iterdir())


def _posting_brief(m: Match) -> dict:
    p = m.posting
    age = p.age_days()
    return {
        "posting_id": p.id,
        "company": p.company,
        "title": p.title,
        "category": p.category,
        "locations": p.locations[:3],
        "terms": p.terms,
        "posted_days_ago": None if age is None else round(age),
        "pay": p.pay_detail or p.pay,
        "company_selectivity": m.selectivity,
        "matcher_tier": m.tier,
        "matched_skills": m.matched_skills,
        "missing_skills": m.missing_skills,
        "description_excerpt": p.description[:1500] if p.description else None,
    }


def build_prompt(resume_text: str, profile: Profile, report: ResumeReport | None, matches: list[Match]) -> str:
    profile_json = profile.model_dump(exclude={"exclude_companies", "max_age_days", "location_strict"})
    if report is not None:
        profile_json["detected_level"] = report.detected.get("level")
    parts = [
        "<candidate_profile>\n" + json.dumps(profile_json, indent=1) + "\n</candidate_profile>",
        "<resume>\n" + resume_text.strip() + "\n</resume>",
    ]
    if report is not None:
        rubric = {
            "overall": report.overall,
            "subscores": {s.label: s.score for s in report.subscores},
            "automated_suggestions": report.improvements,
        }
        parts.append("<automated_resume_check>\n" + json.dumps(rubric, indent=1) + "\n</automated_resume_check>")
    briefs = [_posting_brief(m) for m in matches[:MAX_POSTINGS]]
    parts.append("<shortlisted_internships>\n" + json.dumps(briefs, indent=1) + "\n</shortlisted_internships>")
    parts.append(
        """Review this candidate for internship recruiting.

1. score: 0-100, how competitive this resume is for the internships they're targeting, calibrated so \
that 50 is a typical applicant and 85+ is a resume that gets interviews at most companies it applies to.
2. summary: two or three sentences a recruiter would say after a 30-second skim.
3. strengths and weaknesses: 3-5 each, specific to this resume (the automated check is a starting point; \
add what it can't see, such as the story the resume tells, depth of leadership, writing quality and \
relevance to their targets; for pre-law students, note what law firms and law school admissions value).
4. bullet_rewrites: up to 5 of the weakest bullets, quoting the original exactly, with an improved version \
that keeps the facts and a short reason.
5. keywords_to_add: skills or terms the shortlisted postings want that the resume lacks. Only include ones \
the student could plausibly have or learn quickly; they must add them only if true.
6. job_assessments: one entry per shortlisted internship (use its posting_id) with your own Likely / \
Target / Reach verdict for getting an interview and one sentence of reasoning.
7. next_steps: 3-5 concrete actions for the next two weeks, most impactful first."""
    )
    return "\n\n".join(parts)


def review(
    resume_text: str,
    profile: Profile,
    report: ResumeReport | None = None,
    matches: list[Match] | None = None,
    client=None,
) -> AIReview:
    import anthropic

    if not resume_text.strip():
        raise AIError("No resume text to review.", status=400)
    matches = matches or []
    if client is None:
        key = config.get("anthropic_api_key")
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    try:
        response = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            # If a safety classifier declines, retry server-side on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_prompt(resume_text, profile, report, matches)}],
            output_format=AIReview,
        )
    except anthropic.AuthenticationError as exc:
        raise AIError("Anthropic rejected the API key. Check it in Settings.", status=401) from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIError("This API key doesn't have access to the model. Set INTERNMATCH_MODEL.", status=403) from exc
    except anthropic.NotFoundError as exc:
        raise AIError(f"Model {MODEL!r} was not found. Set INTERNMATCH_MODEL to a model you can use.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Rate limited by the Anthropic API. Try again in a minute.", status=429) from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Couldn't reach the Anthropic API. Check your internet connection.", status=503) from exc

    if response.stop_reason == "refusal":
        raise AIError("The model declined to review this resume.")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise AIError("The AI review came back incomplete. Please try again.")

    result: AIReview = response.parsed_output
    result.score = max(0, min(100, result.score))
    known = {m.posting.id for m in matches[:MAX_POSTINGS]}
    result.job_assessments = [j for j in result.job_assessments if j.posting_id in known]
    return result
