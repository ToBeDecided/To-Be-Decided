from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from internmatch.models import Profile
from internmatch.sources import ListingStore, parse_simplify

FIXTURES = Path(__file__).parent / "fixtures"
DAY = 86400


def row(i: int, company: str, title: str, category: str = "Software", *, days_ago: float = 2, locations=None,
        terms=None, sponsorship: str = "Other", degrees=None, url: str | None = None, active: bool = True) -> dict:
    ts = int(time.time() - days_ago * DAY)
    return {
        "source": "Simplify",
        "category": category,
        "company_name": company,
        "id": f"id-{i}",
        "title": title,
        "active": active,
        "terms": terms if terms is not None else ["Summer 2027"],
        "date_updated": ts,
        "date_posted": ts,
        "url": url or f"https://example.com/jobs/{i}",
        "locations": locations if locations is not None else ["New York, NY"],
        "company_url": "",
        "is_visible": True,
        "sponsorship": sponsorship,
        "degrees": degrees if degrees is not None else ["Bachelor's"],
    }


def listing_rows() -> list[dict]:
    return [
        row(1, "Acme Logistics", "Software Engineer Intern", locations=["New York, NY"]),
        row(2, "Initech", "Backend Engineer Intern", locations=["Austin, TX"], days_ago=1),
        row(3, "Globex", "Machine Learning Intern", "AI/ML/Data", locations=["San Francisco, CA"], days_ago=5),
        row(4, "Umbrella Silicon", "FPGA Design Intern", "Hardware", locations=["Boston, MA"]),
        row(5, "Jane Street", "Software Engineer Intern", locations=["New York, NY"], days_ago=1),
        row(6, "Contoso Defense", "Software Engineer Intern", sponsorship="U.S. Citizenship is Required"),
        row(7, "Hooli", "Research Intern - PhD", "AI/ML/Data", degrees=["PhD"]),
        row(8, "Vandelay", "Explore Program Intern (Freshman/Sophomore)", locations=["Seattle, WA"]),
        row(9, "Stale Co", "Software Engineer Intern", terms=["Summer 2026"]),
        row(10, "Closed Co", "Software Engineer Intern", active=False),
        row(11, "Acme Logistics", "Data Engineer Intern", "AI/ML/Data", locations=["New York, NY"], days_ago=3),
        row(12, "Acme Logistics", "Frontend Engineer Intern", locations=["Remote in USA"], days_ago=3),
        row(13, "Pied Piper", "Embedded Firmware Intern", "Hardware", locations=["Palo Alto, CA"], days_ago=40),
        row(14, "Soylent", "Product Manager Intern", "Product", locations=["Chicago, IL"]),
        row(15, "Massive Dynamic", "Quantitative Research Intern", "Quant", locations=["Chicago, IL"]),
        row(16, "Initech", "Backend Engineer Intern", locations=["Austin, TX"], days_ago=1),  # duplicate of 2
    ]


@pytest.fixture
def rows() -> list[dict]:
    return listing_rows()


@pytest.fixture
def postings(rows):
    return parse_simplify(rows)


@pytest.fixture
def sample_text() -> str:
    return (FIXTURES / "sample_resume.txt").read_text()


@pytest.fixture
def weak_text() -> str:
    return (FIXTURES / "weak_resume.txt").read_text()


@pytest.fixture
def hardware_text() -> str:
    return (FIXTURES / "hardware_resume.txt").read_text()


@pytest.fixture
def summer_profile() -> Profile:
    return Profile(target_terms=["Summer 2027"])


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


@pytest.fixture
def store(tmp_path, rows, monkeypatch) -> ListingStore:
    monkeypatch.setenv("INTERNMATCH_LISTINGS_URL", "https://listings.test/listings.json")
    return ListingStore(cache_dir=tmp_path, transport=mock_transport({"https://listings.test/listings.json": rows}))
