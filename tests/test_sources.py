import asyncio
import json
from datetime import datetime, timezone

import httpx

from internmatch.sources import (
    ListingStore,
    default_terms,
    detail_source,
    html_to_text,
    parse_ashby_board,
    parse_board_spec,
    parse_greenhouse_board,
    parse_lever_board,
    parse_simplify,
    simplify_urls,
    term_options,
    terms_from_title,
)

from .conftest import mock_transport, row


def run(coro):
    return asyncio.run(coro)


def test_parse_simplify_normalizes_fields(rows):
    rows.append(row(99, "Hidden Co", "Intern", sponsorship="Offers Sponsorship"))
    rows[-1]["is_visible"] = False
    postings = parse_simplify(rows)
    assert len(postings) == len(rows) - 2  # one inactive, one hidden
    by_id = {p.id: p for p in postings}
    contoso = by_id["simplify:id-6"]
    assert contoso.sponsorship == "citizenship_required"
    assert by_id["simplify:id-7"].degrees == ["phd"]
    assert by_id["simplify:id-3"].category == "AI/ML/Data"
    assert by_id["simplify:id-1"].date_posted.tzinfo is not None


def test_terms_and_urls():
    assert terms_from_title("SWE Intern - Summer 2027") == ["Summer 2027"]
    assert terms_from_title("Co-op (Fall '26)") == ["Fall 2026"]
    urls = simplify_urls(datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert "Summer2027" in urls[0] and "Summer2026" in urls[1]
    assert "Summer2026" in simplify_urls(datetime(2026, 3, 1, tzinfo=timezone.utc))[0]


def test_term_options(postings):
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    opts = term_options(postings, now)
    assert [o["term"] for o in opts] == ["Summer 2027"]  # Summer 2026 has already happened
    assert default_terms(opts, now) == ["Summer 2027"]
    assert default_terms([], now) == []


def test_board_spec_parsing():
    assert parse_board_spec("greenhouse:stripe") == ("greenhouse", "stripe")
    assert parse_board_spec("Lever: palantir") == ("lever", "palantir")
    assert parse_board_spec("https://boards.greenhouse.io/figma") == ("greenhouse", "figma")
    assert parse_board_spec("https://jobs.lever.co/netflix") == ("lever", "netflix")
    assert parse_board_spec("https://jobs.ashbyhq.com/ramp") == ("ashby", "ramp")
    assert parse_board_spec("workday:acme") is None
    assert parse_board_spec("") is None


def test_detail_source():
    assert detail_source("https://job-boards.greenhouse.io/togetherai/jobs/5232036007") == (
        "greenhouse", "togetherai", "5232036007")
    assert detail_source("https://jobs.lever.co/acme/0b1c2d3e-1111-2222-3333-444455556666/apply")[0] == "lever"
    assert detail_source("https://jobs.ashbyhq.com/flint/39f9e665-7037-4dff-b77a-ff7039df2bfc/application")[:2] == (
        "ashby", "flint")
    assert detail_source("https://acme.wd1.myworkdayjobs.com/job/123") is None


def test_html_to_text():
    raw = "&lt;p&gt;We use &lt;strong&gt;Python&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Go&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "We use Python" in text and "- Go" in text and "<" not in text


GREENHOUSE = {"jobs": [
    {"id": 1, "title": "Software Engineering Intern (Summer 2027)", "absolute_url": "https://gh/1",
     "location": {"name": "New York, NY"}, "first_published": "2026-09-28T12:00:00Z",
     "content": "&lt;p&gt;Experience with Python and React&lt;/p&gt;", "company_name": "Stripe"},
    {"id": 2, "title": "Senior Software Engineer", "absolute_url": "https://gh/2", "location": {"name": "Remote"},
     "content": ""},
]}
LEVER = [
    {"id": "a", "text": "Machine Learning Intern", "hostedUrl": "https://lever/a", "createdAt": 1790000000000,
     "categories": {"location": "San Francisco, CA", "commitment": "Internship"},
     "descriptionPlain": "Work with PyTorch.", "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}]},
    {"id": "b", "text": "Account Executive", "hostedUrl": "https://lever/b", "categories": {"commitment": "Full-time"}},
]
ASHBY = {"jobs": [
    {"id": "x", "title": "Product Design Intern", "location": "Remote", "isRemote": True, "jobUrl": "https://ashby/x",
     "publishedAt": "2026-09-20T00:00:00Z", "employmentType": "Intern", "descriptionPlain": "Figma daily."},
    {"id": "y", "title": "Engineer", "employmentType": "FullTime", "jobUrl": "https://ashby/y"},
]}


def test_board_parsers():
    gh = parse_greenhouse_board("stripe", GREENHOUSE)
    assert len(gh) == 1 and gh[0].terms == ["Summer 2027"] and "Python" in gh[0].description
    assert gh[0].company == "Stripe" and gh[0].category == "Software"
    lv = parse_lever_board("acme", LEVER)
    assert len(lv) == 1 and lv[0].category == "AI/ML/Data" and "SQL" in lv[0].description
    assert lv[0].date_posted.year == 2026
    ab = parse_ashby_board("ramp", ASHBY)
    assert len(ab) == 1 and ab[0].category == "Product" and "Remote" in ab[0].locations


def test_store_downloads_and_caches(store, tmp_path):
    postings = run(store.get_postings())
    assert len(postings) == 15
    assert (tmp_path / "simplify_postings.json").exists()
    assert store.status()["count"] == 15

    # A new store reads the on-disk cache without touching the network.
    offline = ListingStore(cache_dir=tmp_path, transport=mock_transport({}))
    assert len(run(offline.get_postings())) == 15


def test_store_falls_back_to_stale_cache(store, tmp_path):
    run(store.get_postings())
    payload = json.loads((tmp_path / "simplify_postings.json").read_text())
    payload["fetched_at"] = 0  # make it stale
    (tmp_path / "simplify_postings.json").write_text(json.dumps(payload))
    broken = ListingStore(cache_dir=tmp_path, transport=mock_transport(
        {"https://listings.test/listings.json": httpx.Response(500)}))
    assert len(run(broken.get_postings())) == 15
    assert broken.status()["error"]


def test_store_raises_without_any_data(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNMATCH_LISTINGS_URL", "https://listings.test/listings.json")
    s = ListingStore(cache_dir=tmp_path, transport=mock_transport({}))
    try:
        run(s.get_postings())
    except RuntimeError as exc:
        assert "Could not download" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_fetch_boards(tmp_path):
    s = ListingStore(cache_dir=tmp_path, transport=mock_transport({
        "https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true": GREENHOUSE,
        "https://api.lever.co/v0/postings/acme?mode=json": LEVER,
    }))
    postings, errors = run(s.fetch_boards(["greenhouse:stripe", "lever:acme", "ashby:nope", "bogus"]))
    assert {p.source for p in postings} == {"Greenhouse", "Lever"}
    assert any("ashby:nope" in e for e in errors) and any("bogus" in e for e in errors)


def test_enrich_fetches_and_caches_descriptions(tmp_path, postings):
    gh_url = "https://job-boards.greenhouse.io/acme/jobs/123"
    ashby_url = "https://jobs.ashbyhq.com/flint/39f9e665-7037-4dff-b77a-ff7039df2bfc/application"
    postings[0].url, postings[1].url = gh_url, ashby_url
    calls: list[str] = []
    s = ListingStore(cache_dir=tmp_path, transport=mock_transport({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123": {"content": "&lt;p&gt;Go and Kafka&lt;/p&gt;"},
        "https://api.ashbyhq.com/posting-api/job-board/flint*": {"jobs": [
            {"id": "39f9e665-7037-4dff-b77a-ff7039df2bfc", "descriptionPlain": "TypeScript and React"}]},
    }, calls))
    assert run(s.enrich(postings[:3])) == 2
    assert "Kafka" in postings[0].description and "React" in postings[1].description
    assert len(calls) == 2  # the third posting has no supported ATS URL

    # Second time round the descriptions come from the on-disk cache.
    fresh = [p.model_copy(update={"description": ""}) for p in postings[:2]]
    s2 = ListingStore(cache_dir=tmp_path, transport=mock_transport({}, calls))
    assert run(s2.enrich(fresh)) == 2
    assert len(calls) == 2
