"""Match a candidate against postings and estimate interview likelihood.

The likelihood is a heuristic, not a promise. It combines:

* fit: how well the resume's skills cover what the role needs (from the job
  description when we have it, otherwise from the title and category),
* competitiveness: resume strength versus how selective the employer is,
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
from .skills import LANGUAGE_NAMES, TRACKS, canonical, categories_for, extract_skills, title_requirements

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
_LA = ["los angeles", "santa monica", "culver city", "el segundo", "playa vista", "pasadena", "burbank"]
_DC = ["washington, dc", "washington dc", "washington, d.c", "district of columbia", "dc", "arlington", "mclean",
       "reston", "herndon", "bethesda", "alexandria"]
CITY_ALIASES: dict[str, list[str]] = {
    "new york": _NYC, "nyc": _NYC, "new york city": _NYC, "manhattan": _NYC,
    "san francisco": _SF, "sf": _SF, "bay area": _BAY, "sf bay area": _BAY, "silicon valley": _BAY,
    "los angeles": _LA, "la": _LA,
    "dc": _DC, "washington dc": _DC, "washington, dc": _DC, "d.c.": _DC, "dmv": _DC,
    "seattle": ["seattle", "bellevue", "redmond", "kirkland"],
    "boston": ["boston", "cambridge", "somerville", "waltham"],
    "canada": ["canada", "toronto", "vancouver", "montreal", "waterloo", "ottawa", ", on", ", bc", ", qc"],
    "uk": ["united kingdom", "london", "uk", "england"],
    "united kingdom": ["united kingdom", "london", "uk", "england"],
}
_US_TERMS = {"us", "usa", "u.s.", "united states", "america", "united states of america"}

# Programs reserved for first- and second-year students. Kept narrow on purpose: generic words like
# "discovery" or "early career" also show up in regular titles.
_EARLY_PROGRAM_RX = re.compile(
    r"freshm[ae]n|sophomore|first[\s-]year|second[\s-]year|underclass|\bexplore\b|early insight", re.I
)
# Roles for law students (J.D.), not pre-law undergraduates.
_LAW_STUDENT_TITLE_RX = re.compile(
    r"\b(1l|2l|3l|summer associate|law students?|j\.?\s?d\.?(?: candidates?| students?)?|juris doctor)\b", re.I
)
_LAW_STUDENT_DESC_RX = re.compile(
    r"(currently enrolled in|enrolled in|attending|completed (the|their|your) first year (of|at)) "
    r"(an? )?(aba[- ]accredited )?law school|rising (2l|3l|second[- ]year law|third[- ]year law)|"
    r"(first|second|third)[- ]year law students?|j\.?d\.? (candidates?|students?)|law students (only|are eligible)|"
    r"open to law students",
    re.I,
)
_MBA_DESC_RX = re.compile(r"\bmba (candidates?|students?)\b|currently (enrolled in|pursuing) an? mba", re.I)
_HIGH_SCHOOL_RX = re.compile(r"high school (students?|interns?|juniors|seniors|internship)", re.I)
_TITLE_STOP = frozenset(
    """intern interns internship internships summer fall winter spring co-op coop student students trainee
    the and for with new grad year program team associate part time full remote hybrid onsite ii iii based
    position role opportunity undergraduate graduate early career assistant fellow fellowship paid unpaid""".split()
)
_CITIZEN_RX = re.compile(
    r"u\.?s\.? citizen(ship)?\s+(is\s+)?required|must be (a )?u\.?s\.? citizen|(active|obtain|eligible for)"
    r"( a)? (security )?clearance|security clearance|\bts/sci\b|u\.?s\.? persons? (status )?(is )?required|"
    r"\bitar\b|citizenship requirement|only u\.?s\.? citizens",
    re.I,
)
_NO_SPONSOR_RX = re.compile(
    r"(unable|not able|will not|won't|cannot|can ?not|does not|do not) (to )?(provide |offer )?sponsor|"
    r"without (the )?(need for |requiring )?(current or future )?(visa |employment )?sponsorship|"
    r"not eligible for (visa )?sponsorship|no (visa )?sponsorship",
    re.I,
)

EXCLUSION_MESSAGES = {
    "degree": "This role asks for a different degree level (for example law students, MBA or PhD candidates).",
    "sponsorship": "This role doesn't sponsor visas or requires U.S. citizenship.",
    "level": "This program is reserved for first- and second-year students.",
    "closed": "The application deadline has passed.",
}


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

    @property
    def has_language(self) -> bool:
        return bool(self.skills & LANGUAGE_NAMES)


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
    """Turn free-text preferences ("NYC, DC, Chicago, TX, Remote") into match terms."""
    out: list[str] = []
    for raw in prefs:
        term = raw.strip().lower().rstrip(".")
        if not term or term == "remote":
            continue
        if term in _US_TERMS:
            out += ["united states", "usa", "us", "u.s."] + [f", {code}" for code in sorted(_STATE_CODES)]
            out += list(US_STATES)
            continue
        out.extend(CITY_ALIASES.get(term, [term]))
        if term in US_STATES:
            out.append(f", {US_STATES[term]}")
        elif term in _STATE_CODES:
            out.append(f", {term}")
            out.extend(name for name, code in US_STATES.items() if code == term)
    return list(dict.fromkeys(out))


def is_remote(p: Posting) -> bool:
    return any(re.search(r"remote|flexible", loc, re.I) for loc in p.locations)


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
# resume score can't see (school, referrals, networking), so cap the competitiveness factor.
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


def hard_block(p: Posting, c: Candidate, now: datetime) -> str | None:
    """Reasons the candidate can't apply at all, regardless of their preferences."""
    if not p.active:
        return "closed"
    if p.deadline and p.deadline < now:
        return "closed"
    auth = c.profile.work_authorization
    spons = effective_sponsorship(p)
    if auth == "needs_sponsorship" and spons in {"no_sponsorship", "citizenship_required"}:
        return "sponsorship"
    if auth == "authorized" and spons == "citizenship_required":
        return "sponsorship"
    title = p.title.lower()
    desc = p.description[:6000]
    if p.degrees and c.degree not in p.degrees:
        return "degree"
    if c.degree != "jd" and (_LAW_STUDENT_TITLE_RX.search(title) or _LAW_STUDENT_DESC_RX.search(desc)):
        return "degree"
    if c.degree != "mba" and (re.search(r"\bmba\b", title) or _MBA_DESC_RX.search(desc)):
        return "degree"
    if c.degree != "phd" and re.search(r"\bph\.?\s?d\b|\bdoctoral\b", title):
        return "degree"
    if c.degree in {"high_school", "associate", "bachelor"} and re.search(
        r"\b(master'?s|graduate student|grad student)\b|\bm\.a\.|\bm\.s\.", title
    ):
        return "degree"
    if re.search(r"\bundergrad", title) and c.degree in {"master", "phd", "mba", "jd"}:
        return "degree"
    if c.degree != "high_school" and (_HIGH_SCHOOL_RX.search(title) or _HIGH_SCHOOL_RX.search(desc[:1500])):
        return "degree"
    if _EARLY_PROGRAM_RX.search(p.title) and c.level not in {"Freshman", "Sophomore"}:
        return "level"
    return None


def eligibility(p: Posting, c: Candidate, now: datetime) -> str | None:
    """Return a short exclusion reason, or None if the posting should be shown."""
    blocked = hard_block(p, c, now)
    if blocked:
        return blocked
    prof = c.profile
    if prof.exclude_companies:
        norm = normalize_company(p.company)
        if any(normalize_company(x) == norm for x in prof.exclude_companies if x.strip()):
            return "excluded company"
    if prof.target_terms:
        if p.terms and not set(p.terms) & set(prof.target_terms):
            return "term"
        if not p.terms and not prof.include_unknown_terms:
            return "term"
    if p.category not in categories_for(prof.target_tracks, prof.target_categories):
        return "category"
    if prof.paid_only and p.pay == "unpaid":
        return "unpaid"
    if prof.location_strict and c.location_terms:
        if not location_match(p, c.location_terms) and not (prof.remote_ok and is_remote(p)):
            return "location"
    if prof.max_age_days:
        age = p.age_days(now)
        if age is not None and age > prof.max_age_days:
            return "age"
    return None


def _title_tokens(title: str) -> list[str]:
    toks = re.findall(r"[a-z][a-z&]{2,}", title.lower())
    seen: list[str] = []
    for t in toks:
        if t not in _TITLE_STOP and t not in seen:
            seen.append(t)
    return seen[:5]


def _has(c: Candidate, skill: str) -> bool:
    return c.has_language if skill == "@language" else skill in c.skills


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
        have = [s for s in g if _has(c, s)]
        if have:
            satisfied += 1
            for s in have[:2]:
                name = ", ".join(sorted(c.skills & LANGUAGE_NAMES)) if s == "@language" else s
                if name not in matched:
                    matched.append(name)
        else:
            missing.append("A foreign language" if g[0] == "@language" else g[0])
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
        reasons.insert(0, f"Your background matches: {', '.join(matched[:6])}.")
    if group_score is not None and focus:
        reasons.append(f"Covers {satisfied} of {len(groups)} core areas for {focus.lower()} roles.")
    if age is not None and age <= 7:
        reasons.append(f"Posted {'today' if age < 1 else f'{int(age)} day(s) ago'}. Early applicants get the most "
                       "interviews.")
    elif age is not None and age > 30:
        warnings.append(f"Posted {int(age)} days ago. It may be close to filled.")
    if p.deadline:
        days_left = (p.deadline - now).total_seconds() / 86400
        if days_left <= 14:
            warnings.append(f"Applications close in {max(0, int(days_left))} day(s).")
    if early and c.level in {"Freshman", "Sophomore"}:
        reasons.append("Program aimed at early-year students like you.")
    if p.pay in {"paid", "stipend"}:
        reasons.append(f"Paid: {p.pay_detail}." if p.pay_detail and p.pay_detail not in {"Paid", "Stipend"}
                       else "Paid position." if p.pay == "paid" else "Comes with a stipend.")
    elif p.pay == "unpaid":
        warnings.append(f"{p.pay_detail or 'Unpaid'}. Check whether your school offers funding for unpaid internships.")
    if sel == "elite":
        warnings.append("Extremely selective employer. Apply, but treat it as a reach.")
    elif sel == "high":
        warnings.append("Highly competitive employer.")
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


def check_posting(p: Posting, c: Candidate, now: datetime | None = None) -> tuple[Match, str | None]:
    """Score one posting the user pasted in, ignoring their search filters. Also says if they can't apply."""
    now = now or datetime.now(timezone.utc)
    blocked = hard_block(p, c, now)
    return score_posting(p, c, now), EXCLUSION_MESSAGES.get(blocked or "", None)


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
    tracks = sorted(((t, max(c.category_aff[x] for x in cats)) for t, cats in TRACKS.items()), key=lambda kv: -kv[1])
    notes = []
    best_track, best_track_fit = tracks[0]
    if best_track_fit > 0.2:
        cats = ", ".join(f"{cat} ({round(100 * v)})" for cat, v in fits if cat in TRACKS[best_track] and v > 0.2)
        notes.append(f"Your resume reads strongest for {best_track}: {cats}.")
    else:
        notes.append("Your resume doesn't yet point clearly at pre-law, business or humanities roles. The fixes "
                     "above (skills, activities, coursework) will help the matcher and recruiters place you.")
    if c.level in {"Freshman", "Sophomore"}:
        notes.append(
            f"As a {c.level.lower()}, many postings target juniors and seniors. Programs labeled for first- and "
            "second-year students get a boost in your ranking. Apply broadly and early."
        )
    if excluded.get("degree"):
        notes.append(f"Hid {excluded['degree']} postings for a different degree level (e.g. law-student, MBA or "
                     "PhD roles).")
    if excluded.get("sponsorship"):
        notes.append(f"Hid {excluded['sponsorship']} postings that don't sponsor visas or require U.S. citizenship.")
    if excluded.get("level"):
        notes.append(f"Hid {excluded['level']} postings reserved for first- and second-year students.")
    if excluded.get("unpaid"):
        notes.append(f"Hid {excluded['unpaid']} unpaid postings.")
    if excluded.get("closed"):
        notes.append(f"Hid {excluded['closed']} postings whose deadline has passed.")
    strength = round(100 * c.strength)
    if strength >= 80:
        notes.append("Your profile is competitive even at selective employers. Don't skip the reaches.")
    elif strength < 55:
        notes.append("Focus on the \"Likely\" list and on the resume fixes above. Each fix raises your odds everywhere.")
    return ProfileSummary(
        candidate_strength=strength,
        level=c.level,
        track_fit=[CategoryFit(category=t, fit=round(100 * v)) for t, v in tracks],
        category_fit=[CategoryFit(category=cat, fit=round(100 * v)) for cat, v in fits],
        notes=notes,
    )
