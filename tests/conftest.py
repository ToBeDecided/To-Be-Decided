from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from internmatch.models import Profile
from internmatch.sources import MUSE_URL, ListingStore, parse_muse

FIXTURES = Path(__file__).parent / "fixtures"


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def muse_job(i: int, title: str, company: str, category: str, *, days_ago: float = 2,
             locations: tuple[str, ...] = ("New York, NY",), contents: str = "") -> dict:
    """One result in The Muse's public jobs API format."""
    return {
        "id": i,
        "name": title,
        "type": "external",
        "short_name": title.lower().replace(" ", "-"),
        "model_type": "jobs",
        "contents": contents or f"<p>{title} at {company}.</p>",
        "publication_date": iso(days_ago),
        "locations": [{"name": loc} for loc in locations],
        "categories": [{"name": category}],
        "levels": [{"name": "Internship", "short_name": "internship"}],
        "tags": [],
        "refs": {"landing_page": f"https://www.themuse.com/jobs/{company.lower().replace(' ', '')}/{i}"},
        "company": {"id": 1000 + i, "short_name": company.lower(), "name": company},
    }


def muse_rows() -> list[dict]:
    return [
        muse_job(1, "Legal Intern", "Hartley Legal Aid", "Legal Services", days_ago=1, locations=("Boston, MA",),
                 contents="<p>Summer 2027. Assist attorneys with <b>legal research</b> on Westlaw and draft memos. "
                          "Strong legal writing and Spanish a plus. Pay: $18 per hour.</p>"),
        muse_job(2, "Paralegal Intern", "Brightwater Law Group", "Legal Services", days_ago=3,
                 locations=("Boston, MA",), contents="<p>Document review, case files and client intake.</p>"),
        muse_job(3, "Summer Associate", "Cravath, Swaine & Moore LLP", "Legal Services",
                 contents="<p>Open to rising 2L law students at ABA-accredited schools.</p>"),
        muse_job(4, "Policy Intern", "Civic Futures Institute", "Social Services", locations=("Washington, DC",),
                 contents="<p>Research legislation and write policy memos. Summer 2027.</p>"),
        muse_job(5, "Investment Banking Summer Analyst", "Evercore", "Accounting and Finance",
                 contents="<p>Build financial models (DCF, comparable companies) in Excel and PowerPoint.</p>"),
        muse_job(6, "Finance Intern", "Riverbend Credit Union", "Accounting and Finance", locations=("Chicago, IL",),
                 contents="<p>Budgeting, forecasting and reconciliations in Excel. $20/hour.</p>"),
        muse_job(7, "Marketing Intern", "Luma Cosmetics", "Advertising and Marketing", locations=("Los Angeles, CA",),
                 contents="<p>Plan Instagram and TikTok content, design in Canva, write copy.</p>"),
        muse_job(8, "Editorial Intern", "The Lakeshore Review", "Writing and Editing", locations=("Chicago, IL",),
                 contents="<p>Copyediting and fact-checking in AP style. This is an unpaid internship for "
                          "academic credit only.</p>"),
        muse_job(9, "Curatorial Intern", "City Museum of Art", "Arts", locations=("Chicago, IL",),
                 contents="<p>Provenance and archival research; write wall labels. A $3,000 stipend is provided.</p>"),
        muse_job(10, "Development Intern", "Hope Street Foundation", "Nonprofit", locations=("Remote",),
                 contents="<p>Grant writing, donor research and fundraising events.</p>"),
        muse_job(11, "Software Engineer Intern", "TechCo", "Software Engineering",
                 contents="<p>Python and React.</p>"),
        muse_job(12, "Explore Program Intern (Freshman/Sophomore)", "Northwind Bank", "Accounting and Finance"),
        muse_job(13, "Legal Intern (Undergraduate)", "Office of the Public Defender", "Legal Services",
                 locations=("Washington, DC",), contents="<p>Applicants must be a U.S. citizen.</p>"),
        muse_job(14, "Consulting Intern", "Pinecrest Advisory", "Business Operations", days_ago=45,
                 locations=("Boston, MA",), contents="<p>Market research and PowerPoint decks.</p>"),
        muse_job(15, "Summer Intern", "Old Mill Books", "Writing and Editing", locations=("New York, NY",),
                 contents="<p>Join our publishing team for Summer 2027: read manuscripts and proofread.</p>"),
        muse_job(16, "Legal Intern", "Hartley Legal Aid", "Legal Services", days_ago=1, locations=("Boston, MA",)),
    ]


def muse_transport(rows: list[dict], calls: list[str] | None = None, fail: bool = False,
                   per_page: int = 20) -> httpx.MockTransport:
    """Mimic The Muse's /jobs endpoint: filter by category; pages are numbered from 0, like the real API."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if fail:
            return httpx.Response(503, json={"error": "down"})
        if not str(request.url).startswith(MUSE_URL):
            return httpx.Response(404, json={"error": "not found"})
        assert request.url.params.get("level") == "Internship"
        cat = request.url.params.get("category")
        page = int(request.url.params.get("page", "0"))
        results = [r for r in rows if cat is None or any(c["name"] == cat for c in r["categories"])]
        pages = max(1, -(-len(results) // per_page))
        return httpx.Response(200, json={"page": page, "page_count": pages, "items_per_page": per_page,
                                         "total": len(results),
                                         "results": results[page * per_page:(page + 1) * per_page]})

    return httpx.MockTransport(handler)


def mock_transport(routes: dict[str, object], calls: list[str] | None = None) -> httpx.MockTransport:
    """Serve JSON for exact URLs (or URL prefixes ending in '*'); 404 everything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for key, payload in routes.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                if isinstance(payload, httpx.Response):
                    return payload
                return httpx.Response(200, content=json.dumps(payload), headers={"Content-Type": "application/json"})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def no_keys(_: str) -> None:
    return None


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Never touch the real ~/.config or ~/Library while testing."""
    monkeypatch.setenv("INTERNMATCH_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("INTERNMATCH_CACHE_DIR", str(tmp_path / "cache"))
    for var in ("ANTHROPIC_API_KEY", "USAJOBS_API_KEY", "USAJOBS_EMAIL", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
                "THEMUSE_API_KEY", "INTERNMATCH_ALLOW_ANY_HOST"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def rows() -> list[dict]:
    return muse_rows()


@pytest.fixture
def postings(rows):
    return parse_muse({"results": rows})


@pytest.fixture
def store(tmp_path, rows) -> ListingStore:
    return ListingStore(cache_dir=tmp_path / "store", transport=muse_transport(rows), settings=no_keys)


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def prelaw_text() -> str:
    return read("prelaw_resume.txt")


@pytest.fixture
def business_text() -> str:
    return read("business_resume.txt")


@pytest.fixture
def humanities_text() -> str:
    return read("humanities_resume.txt")


@pytest.fixture
def weak_text() -> str:
    return read("weak_resume.txt")


@pytest.fixture
def summer_profile() -> Profile:
    return Profile(target_terms=["Summer 2027"])
