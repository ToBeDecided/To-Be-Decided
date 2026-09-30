"""Match a candidate against postings and estimate interview likelihood.

The likelihood is a heuristic, not a promise. It combines:

* fit: how well the resume's skills cover what the role needs (from the job
  description when we have it, otherwise from the title and category),
* competitiveness: resume strength versus how selective the company is,
* timing: fresh postings get far more interviews than month-old ones,
* seniority: most internships target juniors/seniors; underclassmen get a boost
  on programs aimed at them and a small haircut elsewhere.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .companies import normalize_company, selectivity
from .models import CategoryFit, Match, Posting, Profile, ProfileSummary, ResumeReport
from .resume import ParsedResume, category_affinity, school_level
from .skills import canonical, extract_skills, title_requirements

US_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca", "colorado": "co",
    "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
    "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
    "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny", "north carolina": "nc",
    "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
    "rhode island": "ri", "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
    "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
    "wisconsin": "wi", "wyoming": "wy", "district of columbia": "dc", "washington dc": "dc",
}
_STATE_CODES = set(US_STATES.values())
_NYC = ["new york", "nyc", "manhattan", "brooklyn"]
_SF = ["san francisco", "sf"]
_BAY = _SF + ["san jose", "palo alto", "mountain view", "sunnyvale", "menlo park", "redwood city", "santa clara",
              "cupertino", "oakland", "san mateo", "fremont", "berkeley", "foster city", "burlingame", "milpitas"]
_LA = ["los angeles", "santa monica", "culver city", "el segundo", "playa vista", "pasadena"]
_DC = ["washington, dc", "washington dc", "dc", "arlington", "mclean", "reston", "herndon", "bethesda"]
CITY_ALIASES: dict[str, list[str]] = {
    "new york": _NYC, "nyc": _NYC, "new york city": _NYC, "manhattan": _NYC,
    "san francisco": _SF, "sf": _SF, "bay area": _BAY, "sf bay area": _BAY, "silicon valley": _BAY,
    "los angeles": _LA, "la": _LA,
    "dc": _DC, "washington dc": _DC, "washington, dc": _DC, "dmv": _DC,
    "seattle": ["seattle", "bellevue", "redmond", "kirkland"],
    "boston": ["boston", "cambridge", "somerville", "waltham"],
    "canada": ["canada", "toronto", "vancouver", "montreal", "waterloo", "ottawa", ", on", ", bc", ", qc"],
    "uk": ["united kingdom", "london", "uk", "england", "cambridge, uk"],
    "united kingdom": ["united kingdom", "london", "uk", "england"],
}
_US_TERMS = {"us", "usa", "u.s.", "united states", "america", "united states of america"}
# Programs reserved for first- and second-year students. Kept narrow on purpose: generic words like
# "discovery" or "early career" also show up in regular titles ("Drug Discovery Intern").
_EARLY_PROGRAM_RX = re.compile(
    r"freshm[ae]n|sophomore|first[\s-]year|second[\s-]year|underclass|\bexplore\b|\bstep\b|early insight",
    re.I,
)
_TITLE_STOP = frozenset(
    """intern interns internship internships summer fall winter spring co-op coop student students engineer
    engineering software developer the and for with new grad year program team associate part time full
    remote hybrid onsite ii iii based position role opportunity undergraduate graduate early career""".split()
)
_CITIZEN_RX = re.compile(
    r"u\.?s\.? citizen(ship)?\s+(is\s+)?required|must be (a )?u\.?s\.? citizen|(active|obtain|eligible for)"
    r"( a)? (security )?clearance|security clearance|\bts/sci\b|u\.?s\.? persons? (status )?(is )?required|"
    r"itar",
    re.I,
)
_NO_SPONSOR_RX = re.compile(
    r"(unable|not able|will not|won't|cannot|can ?not|does not|do not) (to )?(provide |offer )?sponsor|"
    r"without (the )?(need for |requiring )?(current or future )?(visa |employment )?sponsorship|"
    r"not eligible for (visa )?sponsorship|no (visa )?sponsorship",
    re.I,
)


@dataclass
class Candidate:
    skills: set[str]
    resume_lower: str
    degree: str
    level: str
    strength: float  # 0..1
    category_aff: dict[str, float]
    profile: Profile
    location_terms: list[str] = field(default_factory=list)


def build_candidate(pr: ParsedResume, report: ResumeReport, profile: Profile) -> Candidate:
    skills = set(pr.skills)
    for s in profile.extra_skills:
        skills.add(canonical(s) or s.strip())
    degree = profile.degree_level or pr.degree or "bachelor"
    level = school_level(profile.grad_year or pr.grad_year, degree)
    exp = next(s.score for s in report.subscores if s.key == "experience")
    strength = (0.7 * report.overall + 0.3 * exp) / 100
    return Candidate(
        skills=skills,
        resume_lower=pr.text.lower(),
        degree=degree,
        level=level,
        strength=max(0.0, min(1.0, strength)),
        category_aff=category_affinity(skills),
        profile=profile,
        location_terms=expand_locations(profile.locations),
    )


def expand_locations(prefs: list[str]) -> list[str]:
    """Turn free-text preferences ("NYC, Bay Area, TX, Remote") into match terms."""
    out: list[str] = []
    for raw in prefs:
        term = raw.strip().lower().rstrip(".")
        if not term or term == "remote":
            continue
        if term in _US_TERMS:
            out += ["united states", "usa", "us", "u.s."] + [f", {code}" for code in sorted(_STATE_CODES)]
            continue
        out.extend(CITY_ALIASES.get(term, [term]))
        if term in US_STATES:
            out.append(f", {US_STATES[term]}")
        elif term in _STATE_CODES:
            out.append(f", {term}")
            out.extend(name for name, code in US_STATES.items() if code == term)
    return list(dict.fromkeys(out))


def is_remote(p: Posting) -> bool:
    return any("remote" in loc.lower() for loc in p.locations)


def location_match(p: Posting, terms: list[str]) -> bool:
    for loc in p.locations:
        low = loc.lower()
        for t in terms:
            # Whole-word matching so "la" doesn't hit "Atlanta" and ", ca" doesn't hit ", canada".
            lead = r"(?<![a-z])" if t[:1].isalpha() else ""
            if re.search(rf"{lead}{re.escape(t)}(?![a-z])", low):
                return True
    return False


def _recency(age: float | None) -> float:
    if age is None:
        return 0.5
    for days, val in ((3, 1.0), (7, 0.9), (14, 0.75), (30, 0.55), (60, 0.35)):
        if age <= days:
            return val
    return 0.2


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


_TIER_REQUIREMENT = {"standard": 0.50, "high": 0.68, "elite": 0.82}
# Even a great resume rarely makes a cold application to a top firm "likely": they screen on things a
# resume score can't see (pedigree, referrals, competition results), so cap the competitiveness factor.
_TIER_CEILING = {"standard": 1.0, "high": 0.88, "elite": 0.7}
LIKELY_AT, TARGET_AT = 70, 50


def effective_sponsorship(p: Posting) -> str:
    if p.sponsorship != "unknown" or not p.description:
        return p.sponsorship
    if _CITIZEN_RX.search(p.description):
        return "citizenship_required"
    if _NO_SPONSOR_RX.search(p.description):
        return "no_sponsorship"
    return "unknown"


def eligibility(p: Posting, c: Candidate, now: datetime) -> str | None:
    """Return a short exclusion reason, or None if the candidate can apply."""
    prof = c.profile
    if not p.active:
        return "inactive"
    if prof.exclude_companies:
        norm = normalize_company(p.company)
        if any(normalize_company(x) == norm for x in prof.exclude_companies if x.strip()):
            return "excluded company"
    if prof.target_terms and p.terms and not set(p.terms) & set(prof.target_terms):
        return "term"
    if prof.target_categories and p.category not in prof.target_categories:
        return "category"
    spons = effective_sponsorship(p)
    if prof.work_authorization == "needs_sponsorship" and spons in {"no_sponsorship", "citizenship_required"}:
        return "sponsorship"
    if prof.work_authorization == "authorized" and spons == "citizenship_required":
        return "sponsorship"
    title = p.title.lower()
    if p.degrees and c.degree not in p.degrees:
        # A bachelor's student can't take a PhD-only role, but "high school" postings are rare enough to ignore.
        return "degree"
    if re.search(r"\bph\.?\s?d\b|\bdoctoral\b", title) and c.degree != "phd":
        return "degree"
    if re.search(r"\bmba\b", title) and c.degree != "mba":
        return "degree"
    if re.search(r"\b(master'?s|graduate student|grad student|ms student)\b|\bm\.s\.", title) and c.degree in {
        "high_school", "associate", "bachelor"
    }:
        return "degree"
    if re.search(r"\bundergrad", title) and c.degree in {"master", "phd", "mba"}:
        return "degree"
    if _EARLY_PROGRAM_RX.search(p.title) and c.level not in {"Freshman", "Sophomore"}:
        return "level"
    if prof.location_strict and c.location_terms:
        if not location_match(p, c.location_terms) and not (prof.remote_ok and is_remote(p)):
            return "location"
    if prof.max_age_days:
        age = p.age_days(now)
        if age is not None and age > prof.max_age_days:
            return "age"
    return None


def _title_tokens(title: str) -> list[str]:
    toks = re.findall(r"[a-z][a-z+#]{2,}", title.lower())
    seen: list[str] = []
    for t in toks:
        if t not in _TITLE_STOP and t not in seen and not re.fullmatch(r"20\d\d", t):
            seen.append(t)
    return seen[:5]


def score_posting(p: Posting, c: Candidate, now: datetime) -> Match:
    rules = title_requirements(p.title)
    groups: list[tuple[str, ...]] = []
    for _, gs in rules:
        for g in gs:
            if g not in groups:
                groups.append(g)
    focus = " / ".join(label for label, _ in rules[:2])

    matched: list[str] = []
    missing: list[str] = []
    satisfied = 0
    for g in groups:
        have = [s for s in g if s in c.skills]
        if have:
            satisfied += 1
            matched.extend(s for s in have[:2] if s not in matched)
        else:
            missing.append(g[0])
    group_score = satisfied / len(groups) if groups else None

    desc_skills = extract_skills(p.description) if p.description else {}
    desc_score = None
    if desc_skills:
        hit = [s for s in desc_skills if s in c.skills]
        desc_score = min(1.0, len(hit) / max(3.0, 0.5 * len(desc_skills)))
        for s in sorted(hit, key=lambda s: -desc_skills[s]):
            if s not in matched:
                matched.append(s)
        for s in sorted(desc_skills, key=lambda s: -desc_skills[s]):
            if s not in c.skills and s not in missing:
                missing.append(s)

    cat_aff = c.category_aff.get(p.category, 0.35)
    tokens = _title_tokens(p.title)
    title_rel = (
        sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}", c.resume_lower)) / len(tokens) if tokens else None
    )

    comps: list[tuple[float, float]] = []
    if desc_score is not None:
        comps.append((desc_score, 0.35))
    if group_score is not None:
        comps.append((group_score, 0.25 if desc_score is not None else 0.40))
    comps.append((cat_aff, 0.25))
    if title_rel is not None:
        comps.append((title_rel, 0.15))
    fit = sum(v * w for v, w in comps) / sum(w for _, w in comps)

    reasons: list[str] = []
    warnings: list[str] = []
    loc_mult = 1.0
    if c.location_terms:
        if location_match(p, c.location_terms):
            reasons.append("In one of your preferred locations.")
        elif c.profile.remote_ok and is_remote(p):
            reasons.append("Remote-friendly.")
        else:
            loc_mult = 0.85
            warnings.append("Outside your preferred locations.")
    match_score = round(100 * fit * loc_mult)

    sel = selectivity(p.company)
    comp = _TIER_CEILING[sel] * (0.35 + 0.65 * _sigmoid((c.strength - _TIER_REQUIREMENT[sel]) * 9))
    age = p.age_days(now)
    rec = _recency(age)
    early = bool(_EARLY_PROGRAM_RX.search(p.title))
    year_mult = 1.0
    if c.level == "Freshman":
        year_mult = 1.12 if early else 0.85
    elif c.level == "Sophomore":
        year_mult = 1.08 if early else 0.93
    likelihood = 100 * (fit * loc_mult) ** 0.8 * comp * (0.8 + 0.2 * rec) * year_mult
    likelihood = max(1, min(99, round(likelihood)))
    tier = "Likely" if likelihood >= LIKELY_AT else "Target" if likelihood >= TARGET_AT else "Reach"

    if matched:
        reasons.insert(0, f"Your skills match: {', '.join(matched[:6])}.")
    if group_score is not None and focus:
        reasons.append(f"Covers {satisfied} of {len(groups)} core areas for {focus.lower()} roles.")
    if age is not None and age <= 7:
        reasons.append(f"Posted {'today' if age < 1 else f'{int(age)} day(s) ago'}. Early applicants get the most "
                       "interviews.")
    elif age is not None and age > 30:
        warnings.append(f"Posted {int(age)} days ago. It may be close to filled.")
    if early and c.level in {"Freshman", "Sophomore"}:
        reasons.append("Program aimed at early-year students like you.")
    if sel == "elite":
        warnings.append("Extremely selective company. Apply, but treat it as a reach.")
    elif sel == "high":
        warnings.append("Highly competitive company.")
    if c.profile.work_authorization == "needs_sponsorship" and effective_sponsorship(p) == "offers":
        reasons.append("Offers visa sponsorship.")
    if not p.terms:
        warnings.append("Term not stated. Check the dates.")
    if not p.description:
        warnings.append("Scored from the title and category (no description available).")

    return Match(
        posting=p,
        match_score=max(0, min(100, match_score)),
        likelihood=likelihood,
        tier=tier,
        selectivity=sel,
        matched_skills=matched[:10],
        missing_skills=missing[:6],
        reasons=reasons,
        warnings=warnings,
        focus=focus,
    )


def rank(postings: list[Posting], c: Candidate, now: datetime | None = None) -> tuple[list[Match], Counter[str]]:
    now = now or datetime.now(timezone.utc)
    excluded: Counter[str] = Counter()
    matches: list[Match] = []
    seen: set[tuple[str, str]] = set()
    for p in postings:
        key = (normalize_company(p.company), p.title.strip().lower())
        if key in seen:  # the same role cross-posted by several sources
            excluded["duplicate"] += 1
            continue
        reason = eligibility(p, c, now)
        if reason:
            excluded[reason] += 1
            continue
        seen.add(key)
        matches.append(score_posting(p, c, now))
    matches.sort(key=lambda m: (-m.likelihood, -m.match_score, m.posting.age_days(now) or 999))
    return matches, excluded


def recommend(matches: list[Match], n: int = 30, per_company: int = 2) -> list[Match]:
    """Pick a diversified shortlist: best odds first, at most ``per_company`` roles per employer."""
    counts: defaultdict[str, int] = defaultdict(int)
    picked: list[Match] = []
    for tier_ok in ({"Likely", "Target"}, {"Reach"}):
        for m in matches:
            if len(picked) >= n:
                return picked
            if m.tier not in tier_ok:
                continue
            key = normalize_company(m.posting.company)
            if counts[key] >= per_company:
                continue
            counts[key] += 1
            picked.append(m)
    return picked


def summarize_profile(c: Candidate, report: ResumeReport, excluded: Counter[str]) -> ProfileSummary:
    fits = sorted(c.category_aff.items(), key=lambda kv: -kv[1])
    notes = []
    top = [f"{cat} ({round(100 * v)})" for cat, v in fits[:2] if v > 0.2]
    if top:
        notes.append(f"Your resume reads strongest for: {', '.join(top)}.")
    if c.level in {"Freshman", "Sophomore"}:
        notes.append(
            f"As a {c.level.lower()}, most postings target juniors and seniors. Programs labeled for first- and "
            "second-year students get a boost in your ranking. Apply broadly and early."
        )
    if excluded.get("sponsorship"):
        notes.append(f"Hid {excluded['sponsorship']} postings that don't sponsor visas or require U.S. citizenship.")
    if excluded.get("degree"):
        notes.append(f"Hid {excluded['degree']} postings that require a different degree level.")
    if excluded.get("level"):
        notes.append(f"Hid {excluded['level']} postings reserved for first- and second-year students.")
    strength = round(100 * c.strength)
    if strength >= 80:
        notes.append("Your profile is competitive even at selective companies. Don't skip the reaches.")
    elif strength < 55:
        notes.append("Focus on the \"Likely\" list and on the resume fixes above. Each fix raises your odds everywhere.")
    return ProfileSummary(
        candidate_strength=strength,
        level=c.level,
        category_fit=[CategoryFit(category=cat, fit=round(100 * v)) for cat, v in fits],
        notes=notes,
    )
