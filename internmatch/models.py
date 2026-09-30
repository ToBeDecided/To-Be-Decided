"""Shared data models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

DegreeLevel = Literal["high_school", "associate", "bachelor", "master", "phd", "mba", "jd"]
WorkAuth = Literal["citizen", "authorized", "needs_sponsorship"]
Sponsorship = Literal["unknown", "offers", "no_sponsorship", "citizenship_required"]
Pay = Literal["paid", "stipend", "unpaid", "unknown"]


class Profile(BaseModel):
    """What the candidate tells us about themselves (in addition to the resume)."""

    name: str = ""
    degree_level: DegreeLevel | None = None  # None = use what the resume says
    major: str = ""
    grad_year: int | None = None
    gpa: float | None = Field(default=None, ge=0, le=4.5)
    work_authorization: WorkAuth = "citizen"
    target_terms: list[str] = Field(default_factory=list)
    include_unknown_terms: bool = True  # keep postings that don't say which term they're for
    target_tracks: list[str] = Field(default_factory=list)  # "Pre-Law", "Business", "Humanities"
    target_categories: list[str] = Field(default_factory=list)
    paid_only: bool = False
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True
    location_strict: bool = False
    extra_skills: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    max_age_days: int | None = Field(default=None, ge=1)


class Posting(BaseModel):
    id: str
    source: str
    company: str
    title: str
    category: str = "Other"
    locations: list[str] = Field(default_factory=list)
    url: str = ""
    terms: list[str] = Field(default_factory=list)
    date_posted: datetime | None = None
    sponsorship: Sponsorship = "unknown"
    degrees: list[str] = Field(default_factory=list)
    description: str = ""
    active: bool = True
    pay: Pay = "unknown"
    pay_detail: str = ""
    deadline: datetime | None = None
    source_category: str = ""

    def age_days(self, now: datetime | None = None) -> float | None:
        if self.date_posted is None:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.date_posted).total_seconds() / 86400)


class SubScore(BaseModel):
    key: str
    label: str
    score: int
    weight: float
    detail: str = ""


class BulletFeedback(BaseModel):
    text: str
    issues: list[str]


class ResumeReport(BaseModel):
    overall: int
    grade: str
    subscores: list[SubScore]
    strengths: list[str]
    improvements: list[str]
    skills: dict[str, list[str]]  # group -> skills
    skill_count: int
    sections: list[str]
    contact: dict[str, bool]
    stats: dict[str, float | int | None]
    detected: dict[str, str | int | float | None]  # gpa, grad_year, degree, ...
    weak_bullets: list[BulletFeedback]


class CategoryFit(BaseModel):
    category: str
    fit: int  # 0-100


class ProfileSummary(BaseModel):
    candidate_strength: int
    level: str  # e.g. "Sophomore"
    track_fit: list[CategoryFit] = Field(default_factory=list)  # Pre-Law / Business / Humanities
    category_fit: list[CategoryFit]
    notes: list[str]


class Match(BaseModel):
    posting: Posting
    match_score: int  # how well the role fits the resume, 0-100
    likelihood: int  # estimated relative interview chance, 0-100
    tier: Literal["Likely", "Target", "Reach"]
    selectivity: Literal["standard", "high", "elite"]
    matched_skills: list[str]
    missing_skills: list[str]
    reasons: list[str]
    warnings: list[str]
    focus: str = ""  # detected role focus, e.g. "Backend"


class AnalysisResult(BaseModel):
    resume: ResumeReport
    profile: ProfileSummary
    recommended: list[Match]
    matches: list[Match]
    stats: dict[str, int | str | None]
    resume_text: str
