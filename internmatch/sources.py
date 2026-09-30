"""Where internship postings come from.

* The Muse (no key needed): internships in legal, finance, business,
  marketing, media, writing, education and nonprofit categories.
* USAJOBS (free key): federal internships and Pathways student jobs; the best
  source for pre-law, policy, museum, archive and library roles.
* Adzuna (free key): a large aggregator; searched for internships in legal,
  finance, consulting, marketing, creative, teaching and nonprofit categories.
* Greenhouse / Lever / Ashby company boards: every internship at a specific
  employer, with full descriptions.

Everything is filtered to pre-law, business and humanities roles: postings
that are clearly tech, science or healthcare are dropped.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import httpx

from . import config
from .models import Posting
from .skills import (
    GENERIC_DISCOUNT,
    category_weights,
    classify,
    classify_description,
    off_focus_description,
    off_focus_employer,
    posting_skills,
)

log = logging.getLogger(__name__)

USER_AGENT = "internmatch/0.2 (+https://github.com/ToBeDecided/To-Be-Decided)"
_TERM_RX = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*(?:semester\s*)?'?(20\d{2}|\d{2})\b", re.I)
_YEAR_FIRST_TERM_RX = re.compile(r"\b(20\d{2})\s+(summer|fall|autumn|winter|spring)\b", re.I)
_INTERN_RX = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bapprentice|\bextern(ship)?\b|\bstudent trainee\b|"
                        r"\bsummer (analyst|associate|fellow)|\bfellowship\b|\bstudent (assistant|aide|worker)\b",
                        re.I)


def _ts(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            # Lever uses milliseconds.
            seconds = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        # USAJOBS sends seven fractional digits ("2026-09-15T00:00:00.0000000"); Python accepts up to six.
        text = re.sub(r"(\.\d{6})\d+", r"\1", text)
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-escapes its HTML
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/h\d|/ul|/ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def terms_from_text(title: str, description: str = "") -> list[str]:
    """Internship terms ("Summer 2027") named in the title, else in the description."""
    for text in (title, description[:4000]):
        out: list[str] = []
        pairs = _TERM_RX.findall(text or "") + [(season, year) for year, season in _YEAR_FIRST_TERM_RX.findall(text or "")]
        for season, year in pairs:
            season = "Fall" if season.lower() == "autumn" else season.capitalize()
            year = year if len(year) == 4 else f"20{year}"
            term = f"{season} {year}"
            if term not in out:
                out.append(term)
        if out:
            return out[:3]
    return []


# ---------------------------------------------------------------------------
# Pay
# ---------------------------------------------------------------------------

_UNPAID_RX = re.compile(
    r"\bunpaid\b|without compensation|no compensation|not a paid|volunteer (position|internship|opportunity)|"
    r"for academic credit only|(academic|college|course) credit (only|in lieu)|this is a volunteer",
    re.I,
)
_STIPEND_RX = re.compile(r"\bstipends?\b", re.I)
_PAY_AMOUNT_RX = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*[kK]?(?:\s*(?:-|–|—|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*[kK]?)?\s*"
    r"(?:/|per|an|a)\s*(?:hour|hr|week|wk|month|mo|year|yr|annum)\b",
    re.I,
)
_PAID_HINT_RX = re.compile(r"\bpaid internship\b|\bhourly (rate|wage|pay)\b|\bpay range\b|\bcompensation\b.{0,20}\$|"
                           r"\bcompetitive (pay|compensation|salary)\b", re.I)


def detect_pay(text: str) -> tuple[str, str]:
    """("paid" | "stipend" | "unpaid" | "unknown", short detail) from free text."""
    if not text:
        return "unknown", ""
    amount = _PAY_AMOUNT_RX.search(text)
    if amount:
        return "paid", re.sub(r"\s+", " ", amount.group(0)).strip()
    unpaid = _UNPAID_RX.search(text)
    if unpaid:
        return "unpaid", "For academic credit" if "credit" in unpaid.group(0).lower() else "Unpaid"
    if _STIPEND_RX.search(text):
        return "stipend", "Stipend"
    if _PAID_HINT_RX.search(text):
        return "paid", "Paid"
    return "unknown", ""


# ---------------------------------------------------------------------------
# Building postings
# ---------------------------------------------------------------------------


def make_posting(
    *,
    id: str,
    source: str,
    company: str,
    title: str,
    url: str,
    description: str = "",
    locations: list[str] | None = None,
    date_posted: Any = None,
    source_category: str = "",
    hint_category: str | None = None,
    default_category: str | None = None,
    pay: tuple[str, str] | None = None,
    deadline: Any = None,
) -> Posting | None:
    """Classify and normalize one posting. Returns None for off-focus or unclassifiable roles."""
    title = re.sub(r"\s+", " ", html_to_text(title)).strip()
    if not title:
        return None
    category, how = classify(title)
    if how == "off":
        return None
    skills = posting_skills(description)
    substantial = len(description.split()) >= 60
    field_weight = max(category_weights(skills).values()) if skills else 0.0
    if substantial and field_weight < 3 and off_focus_description(description) >= 2:
        return None  # a technical/scientific role, whatever the title's buzzwords ("films", "communications")
    if how != "strong":
        if off_focus_employer(company):
            return None
        label_cat, label_how = classify(source_category)
        if label_how == "off" and how == "none":
            return None
        # Generic asks (Office, research, writing, public speaking) show a posting is white-collar, not which field.
        specific = category_weights(skills, generic_weight=GENERIC_DISCOUNT)
        if how == "weak":
            # "Operations Analyst" is a business role unless the job board's label is clearly better backed by the
            # description than the title's own field is.
            if label_how == "strong" and (not substantial or specific[label_cat] >= max(3.0, specific[category] + 1.5)):
                category = label_cat
        else:
            # The title says nothing about the field ("Summer Intern"), so let the description's specific skills
            # decide, then the job board's label. Labels are noisy: when a full description shows no sign of any of
            # our fields, don't trust the label.
            desc_cat, _ = classify_description(skills, min_weight=4.5, generic_weight=GENERIC_DISCOUNT)
            in_field, _ = classify_description(skills, min_weight=4.5)
            guess = label_cat if label_how == "strong" else hint_category or default_category
            if not desc_cat and not in_field and substantial and guess != default_category:
                return None
            category = desc_cat or guess or in_field
    if category is None:
        return None
    pay_kind, pay_detail = pay if pay and pay[0] != "unknown" else detect_pay(f"{title}\n{description}")
    return Posting(
        id=id,
        source=source,
        company=(company or "").strip() or "Unknown organization",
        title=title,
        category=category,
        locations=[loc.strip() for loc in (locations or []) if loc and loc.strip()],
        url=url or "",
        terms=terms_from_text(title, description),
        date_posted=_ts(date_posted),
        description=description.strip(),
        pay=pay_kind,  # type: ignore[arg-type]
        pay_detail=pay_detail,
        deadline=_ts(deadline),
        source_category=source_category,
        skills=skills,
    )


# ---------------------------------------------------------------------------
# The Muse
# ---------------------------------------------------------------------------

MUSE_URL = "https://www.themuse.com/api/public/jobs"
# Muse category name -> our category (used when a job title alone is ambiguous). These are the names the live
# API recognizes; unknown names silently return nothing.
MUSE_CATEGORIES: dict[str, str] = {
    "Legal Services": "Legal",
    "Accounting and Finance": "Finance & Accounting",
    "Business Operations": "Consulting & Business",
    "Management": "Consulting & Business",
    "Project Management": "Consulting & Business",
    "Human Resources and Recruitment": "Consulting & Business",
    "Sales": "Consulting & Business",
    "Account Management": "Consulting & Business",
    "Data and Analytics": "Consulting & Business",
    "Real Estate": "Finance & Accounting",
    "Advertising and Marketing": "Marketing & Communications",
    "Media, PR, and Communications": "Marketing & Communications",
    "Social Media and Community": "Marketing & Communications",
    "Writing and Editing": "Media & Writing",
    "Arts": "Arts & Culture",
    "Education": "Education & Research",
}
MUSE_PAGES_PER_CATEGORY = 10  # 20 results a page
MUSE_GENERAL_PAGES = 5


def parse_muse(data: dict[str, Any], query_category: str | None = None) -> list[Posting]:
    out = []
    for job in data.get("results") or []:
        cats = [c.get("name", "") for c in job.get("categories") or [] if isinstance(c, dict)]
        hint = next((MUSE_CATEGORIES[c] for c in cats if c in MUSE_CATEGORIES), None)
        if hint is None and query_category:
            hint = MUSE_CATEGORIES.get(query_category)
        refs = job.get("refs") or {}
        p = make_posting(
            id=f"muse:{job.get('id')}",
            source="The Muse",
            company=(job.get("company") or {}).get("name", ""),
            title=job.get("name") or "",
            url=refs.get("landing_page") or "",
            description=html_to_text(job.get("contents") or ""),
            locations=[loc.get("name", "") for loc in job.get("locations") or [] if isinstance(loc, dict)],
            date_posted=job.get("publication_date"),
            source_category=", ".join(cats),
            hint_category=hint,
        )
        if p:
            out.append(p)
    return out


async def fetch_muse(client: httpx.AsyncClient, settings: Callable[[str], str | None]) -> tuple[list[Posting], dict]:
    key = settings("themuse_api_key")
    base: list[tuple[str, Any]] = [("level", "Internship")] + ([("api_key", key)] if key else [])
    sem = asyncio.Semaphore(8)
    detail: dict[str, Any] = {}

    async def query(category: str | None, max_pages: int) -> list[Posting]:
        found: list[Posting] = []
        page, page_count, total = 0, 1, None  # The Muse numbers pages from 0
        while page < min(page_count, max_pages):
            params = base + [("page", page)] + ([("category", category)] if category else [])
            async with sem:
                resp = await client.get(MUSE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            page_count, total = int(data.get("page_count") or 1), data.get("total")
            found.extend(parse_muse(data, category))
            page += 1
        detail[category or "(all internships)"] = {"kept": len(found), "pages": page_count, "total": total}
        return found

    labels = list(MUSE_CATEGORIES) + [None]
    results = await asyncio.gather(*(query(c, MUSE_PAGES_PER_CATEGORY) for c in MUSE_CATEGORIES),
                                   query(None, MUSE_GENERAL_PAGES), return_exceptions=True)
    postings: dict[str, Posting] = {}
    errors = []
    for cat, res in zip(labels, results):
        if isinstance(res, BaseException):
            errors.append(f"{cat or 'all'}: {res}")
            detail[cat or "(all internships)"] = {"error": str(res)[:200]}
            continue
        for p in res:
            postings.setdefault(p.id, p)
    if len(errors) == len(results):
        raise RuntimeError("; ".join(errors[:3]))
    return list(postings.values()), {"queries": detail, "partial_errors": len(errors)}


# ---------------------------------------------------------------------------
# USAJOBS
# ---------------------------------------------------------------------------

USAJOBS_URL = "https://data.usajobs.gov/api/search"
_PAY_INTERVALS = {"PH": "per hour", "PA": "per year", "PD": "per day", "PW": "per week", "BW": "every two weeks",
                  "PM": "per month"}


def _usajobs_pay(desc: dict[str, Any]) -> tuple[str, str]:
    for rem in desc.get("PositionRemuneration") or []:
        code = (rem.get("RateIntervalCode") or "").upper()
        if code == "WC":
            return "unpaid", "Without compensation"
        try:
            lo = float(rem.get("MinimumRange") or 0)
            hi = float(rem.get("MaximumRange") or 0)
        except (TypeError, ValueError):
            continue
        if lo or hi:
            unit = _PAY_INTERVALS.get(code, (rem.get("Description") or "").lower())
            fmt = "${:,.2f}" if code == "PH" else "${:,.0f}"
            rng = fmt.format(lo) if not hi or hi == lo else f"{fmt.format(lo)}–{fmt.format(hi)}"
            return "paid", f"{rng} {unit}".strip()
    return "unknown", ""


def parse_usajobs(data: dict[str, Any], require_intern_title: bool = False) -> list[Posting]:
    out = []
    items = ((data.get("SearchResult") or {}).get("SearchResultItems")) or []
    for item in items:
        d = item.get("MatchedObjectDescriptor") or {}
        title = d.get("PositionTitle") or ""
        if require_intern_title and not (_INTERN_RX.search(title) or re.search(r"\bstudent\b", title, re.I)):
            continue
        details = ((d.get("UserArea") or {}).get("Details")) or {}
        duties = details.get("MajorDuties") or []
        if isinstance(duties, str):
            duties = [duties]
        description = "\n\n".join(x for x in [
            details.get("JobSummary") or "",
            "\n".join(f"- {html_to_text(x)}" for x in duties if x),
            d.get("QualificationSummary") or "",
            details.get("Education") or "",
            details.get("Requirements") or "",
        ] if x)
        locations = [loc.get("LocationName", "") for loc in d.get("PositionLocation") or [] if isinstance(loc, dict)]
        if not locations and d.get("PositionLocationDisplay"):
            locations = [d["PositionLocationDisplay"]]
        if details.get("RemoteIndicator") is True:
            locations.append("Remote")
        job_cats = ", ".join(c.get("Name", "") for c in d.get("JobCategory") or [] if isinstance(c, dict))
        apply = d.get("ApplyURI") or []
        p = make_posting(
            id=f"usajobs:{d.get('PositionID') or item.get('MatchedObjectId')}",
            source="USAJOBS",
            company=d.get("OrganizationName") or d.get("DepartmentName") or "",
            title=title,
            url=d.get("PositionURI") or (apply[0] if apply else ""),
            description=html_to_text(description),
            locations=locations,
            date_posted=d.get("PublicationStartDate"),
            source_category=job_cats,
            default_category="Government & Policy",  # it's a government job, after all
            pay=_usajobs_pay(d),
            deadline=d.get("ApplicationCloseDate"),
        )
        if p:
            out.append(p)
    return out


async def fetch_usajobs(client: httpx.AsyncClient, settings: Callable[[str], str | None]) -> tuple[list[Posting], dict]:
    headers = {"Authorization-Key": settings("usajobs_api_key") or "", "User-Agent": settings("usajobs_email") or "",
               "Host": "data.usajobs.gov"}
    queries = [
        ({"HiringPath": "student", "ResultsPerPage": 500, "Page": 1}, False),
        ({"Keyword": "intern", "ResultsPerPage": 500, "Page": 1}, True),
    ]
    postings: dict[str, Posting] = {}
    detail = {}
    for params, require_title in queries:
        resp = await client.get(USAJOBS_URL, params=params, headers=headers)
        if resp.status_code in (401, 403):
            raise RuntimeError("USAJOBS rejected the API key or email. Check them in Settings.")
        resp.raise_for_status()
        found = parse_usajobs(resp.json(), require_intern_title=require_title)
        detail[str(params)] = len(found)
        for p in found:
            postings.setdefault(p.id, p)
    return list(postings.values()), {"queries": detail}


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"
ADZUNA_CATEGORIES: dict[str, str | None] = {
    "legal-jobs": "Legal",
    "accounting-finance-jobs": "Finance & Accounting",
    "consultancy-jobs": "Consulting & Business",
    "pr-advertising-marketing-jobs": "Marketing & Communications",
    "creative-design-jobs": "Media & Writing",
    "charity-voluntary-jobs": "Nonprofit & Advocacy",
    "teaching-jobs": "Education & Research",
    "hr-jobs": "Consulting & Business",
    "graduate-jobs": None,
}
ADZUNA_PAGES = 2


def parse_adzuna(data: dict[str, Any], tag: str | None = None) -> list[Posting]:
    out = []
    for job in data.get("results") or []:
        title = html_to_text(job.get("title") or "")
        if not _INTERN_RX.search(title):
            continue
        cat = job.get("category") or {}
        pay = ("unknown", "")
        predicted = str(job.get("salary_is_predicted", "1")) in {"1", "true", "True"}
        if job.get("salary_min") and not predicted:
            lo, hi = float(job["salary_min"]), float(job.get("salary_max") or job["salary_min"])
            unit = "per hour" if hi < 200 else "per year"
            rng = f"${lo:,.0f}" if hi == lo else f"${lo:,.0f}–${hi:,.0f}"
            pay = ("paid", f"{rng} {unit}")
        p = make_posting(
            id=f"adzuna:{job.get('id')}",
            source="Adzuna",
            company=(job.get("company") or {}).get("display_name", ""),
            title=title,
            url=job.get("redirect_url") or "",
            description=html_to_text(job.get("description") or ""),
            locations=[(job.get("location") or {}).get("display_name", "")],
            date_posted=job.get("created"),
            source_category=cat.get("label", ""),
            hint_category=ADZUNA_CATEGORIES.get(cat.get("tag") or tag or ""),
            pay=pay,
        )
        if p:
            out.append(p)
    return out


async def fetch_adzuna(client: httpx.AsyncClient, settings: Callable[[str], str | None]) -> tuple[list[Posting], dict]:
    creds = {"app_id": settings("adzuna_app_id") or "", "app_key": settings("adzuna_app_key") or ""}
    detail: dict[str, Any] = {}

    async def query(tag: str) -> list[Posting]:
        found: list[Posting] = []
        for page in range(1, ADZUNA_PAGES + 1):
            params = {**creds, "results_per_page": 50, "what": "intern", "category": tag, "max_days_old": 90,
                      "sort_by": "date", "content-type": "application/json"}
            resp = await client.get(ADZUNA_URL.format(page=page), params=params)
            if resp.status_code in (401, 403):
                raise RuntimeError("Adzuna rejected the app ID or key. Check them in Settings.")
            resp.raise_for_status()
            data = resp.json()
            found.extend(parse_adzuna(data, tag))
            if len(data.get("results") or []) < 50:
                break
        detail[tag] = len(found)
        return found

    results = await asyncio.gather(*(query(t) for t in ADZUNA_CATEGORIES), return_exceptions=True)
    errors = [r for r in results if isinstance(r, BaseException)]
    if len(errors) == len(results):
        raise RuntimeError(str(errors[0]))
    postings: dict[str, Posting] = {}
    for res in results:
        if not isinstance(res, BaseException):
            for p in res:
                postings.setdefault(p.id, p)
    return list(postings.values()), {"queries": detail, "partial_errors": len(errors)}


# ---------------------------------------------------------------------------
# Company job boards (Greenhouse / Lever / Ashby)
# ---------------------------------------------------------------------------

_BOARD_URL_PATTERNS = (
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)", re.I)),
    ("lever", re.compile(r"lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com/([\w.%-]+)", re.I)),
)


def parse_board_spec(spec: str) -> tuple[str, str] | None:
    """Accept "greenhouse:nytimes", "lever:acme", "ashby:acme" or a board URL."""
    spec = spec.strip()
    if not spec:
        return None
    if "://" not in spec and ":" in spec:
        kind, _, slug = spec.partition(":")
        kind = kind.strip().lower()
        if kind in {"greenhouse", "lever", "ashby"} and slug.strip():
            return kind, slug.strip()
        return None
    for kind, rx in _BOARD_URL_PATTERNS:
        m = rx.search(spec)
        if m and m.group(1).lower() not in {"jobs", "job-boards", "boards"}:
            return kind, m.group(1)
    return None


def _board_posting(employment: str = "", **kw: Any) -> Posting | None:
    if not _INTERN_RX.search(kw["title"]) and not _INTERN_RX.search(employment):
        return None
    return make_posting(default_category="Consulting & Business", **kw)


def parse_greenhouse_board(slug: str, data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        loc = (job.get("location") or {}).get("name") or ""
        p = _board_posting(
            id=f"greenhouse:{slug}:{job.get('id')}", source="Greenhouse",
            company=job.get("company_name") or slug.replace("-", " ").title(), title=job.get("title") or "",
            url=job.get("absolute_url") or "", description=html_to_text(job.get("content") or ""),
            locations=[x.strip() for x in re.split(r";|\|", loc) if x.strip()],
            date_posted=job.get("first_published") or job.get("updated_at"),
        )
        if p:
            out.append(p)
    return out


def _lever_description(job: dict[str, Any]) -> str:
    parts = [job.get("descriptionPlain") or html_to_text(job.get("description") or "")]
    for block in job.get("lists") or []:
        parts.append(block.get("text") or "")
        parts.append(html_to_text(block.get("content") or ""))
    parts.append(job.get("additionalPlain") or "")
    return "\n".join(p for p in parts if p).strip()


def parse_lever_board(slug: str, data: list[dict[str, Any]]) -> list[Posting]:
    out = []
    for job in data:
        cats = job.get("categories") or {}
        p = _board_posting(
            id=f"lever:{slug}:{job.get('id')}", source="Lever", company=slug.replace("-", " ").title(),
            title=job.get("text") or "", url=job.get("hostedUrl") or "", description=_lever_description(job),
            locations=cats.get("allLocations") or ([cats["location"]] if cats.get("location") else []),
            date_posted=job.get("createdAt"), employment=cats.get("commitment") or "",
            source_category=cats.get("team") or "",
        )
        if p:
            out.append(p)
    return out


def parse_ashby_board(slug: str, data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        if job.get("isListed") is False:
            continue
        locs = [job.get("location") or ""] + [s.get("location") or "" for s in job.get("secondaryLocations") or []]
        if job.get("isRemote"):
            locs.append("Remote")
        p = _board_posting(
            id=f"ashby:{slug}:{job.get('id')}", source="Ashby", company=slug.replace("-", " ").title(),
            title=job.get("title") or "", url=job.get("jobUrl") or "",
            description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""),
            locations=locs, date_posted=job.get("publishedAt"), employment=job.get("employmentType") or "",
            source_category=job.get("department") or "",
        )
        if p:
            out.append(p)
    return out


def board_api_url(kind: str, slug: str) -> str:
    if kind == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    if kind == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if kind == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    raise ValueError(f"unknown board type: {kind}")


def parse_board(kind: str, slug: str, data: Any) -> list[Posting]:
    return {"greenhouse": parse_greenhouse_board, "lever": parse_lever_board, "ashby": parse_ashby_board}[kind](
        slug, data
    )


_GH_JOB = re.compile(r"greenhouse\.io/([\w.-]+)/jobs/(\d+)", re.I)
_LEVER_JOB = re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I)
_ASHBY_JOB = re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", re.I)


def detail_source(url: str) -> tuple[str, str, str] | None:
    """(kind, board slug, job id) if we know how to fetch this posting's full description."""
    for kind, rx in (("greenhouse", _GH_JOB), ("lever", _LEVER_JOB), ("ashby", _ASHBY_JOB)):
        m = rx.search(url or "")
        if m and m.group(1).lower() not in {"embed"}:
            return kind, m.group(1), m.group(2)
    return None


# ---------------------------------------------------------------------------
# Registry and store
# ---------------------------------------------------------------------------

Fetcher = Callable[[httpx.AsyncClient, Callable[[str], "str | None"]], Awaitable[tuple[list[Posting], dict]]]


class SourceInfo:
    def __init__(self, name: str, label: str, fetch: Fetcher, needs: tuple[str, ...], signup: str, about: str):
        self.name, self.label, self.fetch, self.needs, self.signup, self.about = name, label, fetch, needs, signup, about


SOURCES: dict[str, SourceInfo] = {
    "themuse": SourceInfo("themuse", "The Muse", fetch_muse, (), "https://www.themuse.com/developers/api/v2",
                          "Internships in legal, finance, business, marketing, media, writing and education. "
                          "No key needed."),
    "usajobs": SourceInfo("usajobs", "USAJOBS", fetch_usajobs, ("usajobs_api_key", "usajobs_email"),
                          "https://developer.usajobs.gov/apirequest/",
                          "Federal internships and Pathways student jobs (Justice, State, Library of Congress, "
                          "Smithsonian, National Archives and more). Free key."),
    "adzuna": SourceInfo("adzuna", "Adzuna", fetch_adzuna, ("adzuna_app_id", "adzuna_app_key"),
                         "https://developer.adzuna.com/signup",
                         "Large job aggregator: legal, finance, consulting, marketing, creative, teaching and "
                         "nonprofit internships from across the web. Free key."),
}


class ListingStore:
    """Fetches, filters and caches postings from every configured source. Safe to share across requests."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_hours: float = 6,
        transport: httpx.AsyncBaseTransport | None = None,
        settings: Callable[[str], str | None] = config.get,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else config.cache_dir()
        self.ttl = ttl_hours * 3600
        self._transport = transport
        self._settings = settings
        self._postings: list[Posting] | None = None
        self._fetched_at: float | None = None
        self._status: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._descriptions: dict[str, str] | None = None
        self._board_cache: dict[str, tuple[float, list[Posting]]] = {}

    # -- plumbing ---------------------------------------------------------
    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=self._transport,
        )

    def _cache_file(self, name: str) -> Path:
        return self.cache_dir / f"source_{name}.json"

    def _write_json(self, path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(path)
        except OSError as exc:  # a read-only home dir shouldn't break the app
            log.warning("could not write cache %s: %s", path, exc)

    def _read_cache(self, name: str) -> tuple[float, list[Posting]] | None:
        try:
            payload = json.loads(self._cache_file(name).read_text())
            return payload["fetched_at"], [Posting.model_validate(p) for p in payload["postings"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def enabled(self, name: str) -> bool:
        return all(self._settings(k) for k in SOURCES[name].needs)

    def invalidate(self) -> None:
        """Forget the in-memory list (e.g. after API keys change) so the next request re-reads sources."""
        self._postings = None

    # -- sources ------------------------------------------------------------
    async def fetch_source(self, name: str) -> tuple[list[Posting], dict]:
        """Fetch one source live, bypassing the cache."""
        async with self._client(timeout=45) as client:
            return await SOURCES[name].fetch(client, self._settings)

    async def _load_source(self, name: str, force: bool) -> list[Posting]:
        now = time.time()
        cached = self._read_cache(name)
        if cached and not force and now - cached[0] < self.ttl:
            self._status[name] = {"count": len(cached[1]), "fetched_at": cached[0], "error": None}
            return cached[1]
        try:
            postings, detail = await self.fetch_source(name)
        except Exception as exc:
            msg = str(exc) or type(exc).__name__
            log.warning("%s refresh failed: %s", name, msg)
            if cached:
                self._status[name] = {"count": len(cached[1]), "fetched_at": cached[0],
                                      "error": f"Using saved results; refresh failed: {msg}"}
                return cached[1]
            self._status[name] = {"count": 0, "fetched_at": None, "error": msg}
            return []
        self._write_json(self._cache_file(name), {"fetched_at": now,
                                                  "postings": [p.model_dump(mode="json") for p in postings]})
        self._status[name] = {"count": len(postings), "fetched_at": now, "error": None, "detail": detail}
        return postings

    async def get_postings(self, force: bool = False) -> list[Posting]:
        async with self._lock:
            now = time.time()
            if not force and self._postings is not None and now - (self._fetched_at or 0) < self.ttl:
                return self._postings
            names = [n for n in SOURCES if self.enabled(n)]
            for n in SOURCES:
                if n not in names:
                    self._status.pop(n, None)
            results = await asyncio.gather(*(self._load_source(n, force) for n in names))
            postings = [p for res in results for p in res]
            if not postings:
                errors = "; ".join(f"{SOURCES[n].label}: {self._status[n]['error']}" for n in names
                                   if self._status.get(n, {}).get("error"))
                raise RuntimeError(f"Could not load any internship listings ({errors or 'no results'}).")
            self._postings, self._fetched_at = postings, now
            return postings

    def status(self) -> dict[str, Any]:
        sources = {}
        for name, info in SOURCES.items():
            st = self._status.get(name, {})
            sources[name] = {
                "label": info.label,
                "about": info.about,
                "signup": info.signup,
                "needs": list(info.needs),
                "enabled": self.enabled(name),
                "count": st.get("count", 0),
                "fetched_at": datetime.fromtimestamp(st["fetched_at"], tz=timezone.utc).isoformat()
                if st.get("fetched_at") else None,
                "error": st.get("error"),
            }
        fetched = [s["fetched_at"] for s in sources.values() if s["fetched_at"]]
        errors = [f"{s['label']}: {s['error']}" for s in sources.values() if s["enabled"] and s["error"]]
        return {
            "count": len(self._postings or []),
            "fetched_at": min(fetched) if fetched else None,
            "sources": sources,
            "error": "; ".join(errors) or None,
        }

    # -- company boards ---------------------------------------------------
    async def fetch_boards(self, specs: Iterable[str]) -> tuple[list[Posting], list[str]]:
        parsed, errors = [], []
        for spec in specs:
            p = parse_board_spec(spec)
            if p is None:
                if spec.strip():
                    errors.append(f"Couldn't understand board '{spec}'. Use e.g. greenhouse:nytimes or lever:acme.")
                continue
            parsed.append(p)
        if not parsed:
            return [], errors

        async def one(client: httpx.AsyncClient, kind: str, slug: str) -> list[Posting]:
            key = f"{kind}:{slug.lower()}"
            hit = self._board_cache.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
            resp = await client.get(board_api_url(kind, slug))
            if resp.status_code == 404:
                raise LookupError(f"{key} not found")
            resp.raise_for_status()
            postings = parse_board(kind, slug, resp.json())
            self._board_cache[key] = (time.time(), postings)
            return postings

        out: list[Posting] = []
        async with self._client() as client:
            results = await asyncio.gather(*(one(client, k, s) for k, s in parsed), return_exceptions=True)
        for (kind, slug), res in zip(parsed, results):
            if isinstance(res, BaseException):
                errors.append(f"{kind}:{slug}: {res}")
            else:
                out.extend(res)
        return out, errors

    # -- description enrichment -------------------------------------------
    @property
    def _descriptions_file(self) -> Path:
        return self.cache_dir / "descriptions.json"

    def _load_descriptions(self) -> dict[str, str]:
        if self._descriptions is None:
            try:
                self._descriptions = json.loads(self._descriptions_file.read_text())
            except (OSError, ValueError):
                self._descriptions = {}
        return self._descriptions

    async def enrich(self, postings: list[Posting], limit: int = 40, concurrency: int = 8) -> int:
        """Fill in ``description`` for up to ``limit`` postings. Returns how many were filled."""
        cache = self._load_descriptions()
        todo: list[tuple[Posting, tuple[str, str, str]]] = []
        filled = 0
        for p in postings:
            if p.description:
                continue
            if p.url in cache:
                p.description = cache[p.url]
                p.skills = posting_skills(p.description)
                filled += bool(p.description)
                continue
            src = detail_source(p.url)
            if src and len(todo) < limit:
                todo.append((p, src))
        if not todo:
            return filled

        sem = asyncio.Semaphore(concurrency)
        ashby_boards: dict[str, asyncio.Future[dict[str, Any]]] = {}

        async def ashby_board(client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
            resp = await client.get(board_api_url("ashby", slug))
            resp.raise_for_status()
            return resp.json()

        async def one(client: httpx.AsyncClient, posting: Posting, src: tuple[str, str, str]) -> str:
            kind, slug, job_id = src
            async with sem:
                if kind == "greenhouse":
                    eu = ".eu.greenhouse.io" in posting.url
                    host = "boards-api.eu.greenhouse.io" if eu else "boards-api.greenhouse.io"
                    resp = await client.get(f"https://{host}/v1/boards/{slug}/jobs/{job_id}")
                    resp.raise_for_status()
                    return html_to_text(resp.json().get("content") or "")
                if kind == "lever":
                    resp = await client.get(f"https://api.lever.co/v0/postings/{slug}/{job_id}")
                    resp.raise_for_status()
                    return _lever_description(resp.json())
                if slug not in ashby_boards:
                    ashby_boards[slug] = asyncio.ensure_future(ashby_board(client, slug))
                board = await ashby_boards[slug]
                for job in board.get("jobs", []):
                    if job.get("id") == job_id:
                        return job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
                return ""

        async with self._client(timeout=20) as client:
            results = await asyncio.gather(*(one(client, p, s) for p, s in todo), return_exceptions=True)
        for (posting, _), res in zip(todo, results):
            if isinstance(res, BaseException):
                log.info("description fetch failed for %s: %s", posting.url, res)
                continue
            cache[posting.url] = res
            if res:
                posting.description = res
                posting.skills = posting_skills(res)
                filled += 1
        self._write_json(self._descriptions_file, cache)
        return filled


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------

_SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def _term_key(term: str) -> tuple[int, int]:
    season, _, year = term.partition(" ")
    return (int(year) if year.isdigit() else 0, _SEASON_ORDER.get(season, 9))


def term_started(term: str, now: datetime) -> bool:
    year, season = _term_key(term)
    if not year or year < now.year:
        return True
    # "Winter <this year>" is used for both January and December starts, so keep it all year.
    start_month = {1: 2, 2: 6, 3: 9}.get(season)
    return year == now.year and start_month is not None and now.month >= start_month


def upcoming_terms(now: datetime, n: int = 4) -> list[str]:
    """The next few internship terms that haven't started yet, in order."""
    terms = [f"{season} {y}" for y in (now.year, now.year + 1, now.year + 2) for season in ("Spring", "Summer", "Fall")]
    return [t for t in terms if not term_started(t, now)][:n]


def term_options(postings: Iterable[Posting], now: datetime | None = None) -> list[dict[str, Any]]:
    """Upcoming terms (always shown) plus any other not-yet-started terms postings mention, with counts."""
    now = now or datetime.now(timezone.utc)
    counts: dict[str, int] = {t: 0 for t in upcoming_terms(now)}
    for p in postings:
        for t in p.terms:
            if not term_started(t, now):
                counts[t] = counts.get(t, 0) + 1
    return sorted(({"term": t, "count": n} for t, n in counts.items()), key=lambda d: _term_key(d["term"]))


def default_terms(options: list[dict[str, Any]], now: datetime | None = None) -> list[str]:
    """Next summer: that's when most students intern."""
    now = now or datetime.now(timezone.utc)
    summer = f"Summer {now.year + 1 if now.month >= 7 else now.year}"
    return [summer] if any(o["term"] == summer for o in options) else []
