import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from internmatch.sources import (
    ADZUNA_URL,
    USAJOBS_URL,
    ListingStore,
    default_terms,
    detail_source,
    detect_pay,
    html_to_text,
    parse_adzuna,
    parse_board_spec,
    parse_greenhouse_board,
    parse_lever_board,
    parse_muse,
    parse_usajobs,
    term_options,
    terms_from_text,
    upcoming_terms,
)

from .conftest import iso, mock_transport, muse_job, muse_transport, no_keys


def run(coro):
    return asyncio.run(coro)


def test_parse_muse(rows):
    postings = {p.id: p for p in parse_muse({"results": rows})}
    assert "muse:11" not in postings  # software engineering: off-focus
    legal = postings["muse:1"]
    assert legal.source == "The Muse" and legal.category == "Legal"
    assert legal.company == "Hartley Legal Aid" and legal.locations == ["Boston, MA"]
    assert legal.url.startswith("https://www.themuse.com/jobs/")
    assert legal.terms == ["Summer 2027"] and "Westlaw" in legal.description
    assert legal.pay == "paid" and legal.pay_detail == "$18 per hour"
    assert legal.date_posted.tzinfo is not None
    assert postings["muse:4"].category == "Government & Policy"
    assert postings["muse:9"].pay == "stipend"
    assert postings["muse:8"].pay == "unpaid"
    # A generic "Summer Intern" title falls back to The Muse's own category.
    assert postings["muse:15"].category == "Media & Writing"


def test_muse_pages_are_numbered_from_zero(tmp_path, rows):
    calls: list[str] = []
    s = ListingStore(cache_dir=tmp_path, settings=no_keys, transport=muse_transport(rows, calls, per_page=2))
    postings, detail = run(s.fetch_source("themuse"))
    assert len(postings) == 15  # nothing lost on page 0
    legal_pages = sorted(int(httpx.URL(c).params["page"]) for c in calls
                         if httpx.URL(c).params.get("category") == "Legal Services")
    assert legal_pages == [0, 1, 2]  # 5 legal rows at 2 per page
    assert detail["queries"]["Legal Services"]["kept"] == 5


def test_titles_without_a_field_signal_use_the_description():
    lawn = parse_muse({"results": [muse_job(90, "Residential Lawn Specialist Intern", "TruGreen", "Legal Services",
                                            contents="<p>Sell lawn care plans door to door. Customer service.</p>")]})
    assert lawn == []  # off-focus despite the "Legal Services" label
    filler = " ".join(["Stock shelves, greet customers and keep the store tidy."] * 8)
    vague = parse_muse({"results": [muse_job(91, "Summer Intern", "Acme", "Legal Services",
                                             contents=f"<p>{filler}</p>")]})
    assert vague == []  # label says legal, description doesn't back it up
    backed = parse_muse({"results": [muse_job(92, "Summer Intern", "Acme", "Business Operations",
                                              contents="<p>Legal research on Westlaw and drafting legal memos.</p>")]})
    assert backed[0].category == "Legal"
    lab = parse_muse({"results": [muse_job(93, "Intern, Year Round", "Sandia National Laboratories", "Management",
                                           contents="<p>Project management and Excel.</p>")]})
    assert lab == []


def test_technical_roles_with_field_buzzwords_are_dropped():
    tech = " ".join(["You will develop PVD chambers in our semiconductor fab. A degree in engineering is required,"
                     " with experience in process engineering and MATLAB."] * 4)
    films = parse_muse({"results": [muse_job(94, "College Intern - Process development for emerging films", "Applied",
                                             "Management", contents=f"<p>{tech}</p>")]})
    assert films == []
    comms = " ".join(["Write press releases and social media content for our engineering teams; media relations,"
                      " copywriting and AP style."] * 4)
    kept = parse_muse({"results": [muse_job(95, "Engineering Communications Intern", "Acme", "Management",
                                            contents=f"<p>{comms}</p>")]})
    assert kept and kept[0].category == "Marketing & Communications"


def test_weak_titles_keep_their_own_category_over_noisy_labels():
    ops = " ".join(["Support store operations and supply chain planning. Build Excel reports on inventory."] * 6)
    p = parse_muse({"results": [muse_job(96, "Field Supply Chain - Operations Manager Intern", "Walmart",
                                         "Legal Services", contents=f"<p>{ops}</p>")]})
    assert p[0].category == "Consulting & Business"  # not "Legal", despite the label
    assert "Excel" in p[0].skills  # skills are extracted once, at download time
    security = " ".join(["Track security incidents in our case management system and support compliance."] * 6)
    p = parse_muse({"results": [muse_job(99, "Operations Analyst - Corporate Physical Security", "Spectrum",
                                         "Legal Services", contents=f"<p>{security}</p>")]})
    assert p[0].category == "Consulting & Business"  # a passing mention of compliance isn't a legal role


def test_generic_descriptions_keep_the_job_boards_label():
    # "Research, writing, public speaking" used to read as an education role, so an English major matched a bank's
    # leadership program. Only specific skills can override the label.
    corp = " ".join(["Conduct research, strong writing and public speaking skills, Microsoft Office, Excel."] * 6)
    p = parse_muse({"results": [muse_job(97, "Graduate Leadership Program - Consumer Deposit Products", "TD Bank",
                                         "Accounting and Finance", contents=f"<p>{corp}</p>")]})
    assert p[0].category == "Finance & Accounting"
    tutoring = " ".join(["Tutor K-12 students and support classroom teaching; lesson planning."] * 6)
    p = parse_muse({"results": [muse_job(98, "Summer Intern", "City Schools", "Management",
                                         contents=f"<p>{tutoring}</p>")]})
    assert p[0].category == "Education & Research"


def test_detect_pay():
    assert detect_pay("Pay: $18-$22 per hour") == ("paid", "$18-$22 per hour")
    assert detect_pay("Interns earn $25 an hour") == ("paid", "$25 an hour")
    assert detect_pay("Unpaid; for academic credit only")[0] == "unpaid"
    assert detect_pay("A $3,000 stipend") == ("stipend", "Stipend")
    assert detect_pay("Competitive compensation") == ("paid", "Paid")
    assert detect_pay("Great team") == ("unknown", "")


def test_terms():
    assert terms_from_text("Legal Intern - Summer 2027") == ["Summer 2027"]
    assert terms_from_text("Intern", "Starts Fall '27, continues Spring 2028") == ["Fall 2027", "Spring 2028"]
    assert terms_from_text("2027 Summer Intern: Operations Analyst") == ["Summer 2027"]
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert upcoming_terms(now) == ["Spring 2027", "Summer 2027", "Fall 2027", "Spring 2028"]


def test_term_options(postings):
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    opts = {o["term"]: o["count"] for o in term_options(postings, now)}
    assert opts["Summer 2027"] >= 3 and "Spring 2027" in opts
    assert default_terms(term_options(postings, now), now) == ["Summer 2027"]


USAJOBS_PAYLOAD = {"SearchResult": {"SearchResultCount": 3, "SearchResultItems": [
    {"MatchedObjectId": "1", "MatchedObjectDescriptor": {
        "PositionID": "DOJ-27-001", "PositionTitle": "Student Trainee (Legal Assistant)",
        "PositionURI": "https://www.usajobs.gov/job/1", "ApplyURI": ["https://www.usajobs.gov/job/1/apply"],
        "PositionLocation": [{"LocationName": "Washington, District of Columbia"}],
        "OrganizationName": "Office of the U.S. Attorney", "DepartmentName": "Department of Justice",
        "JobCategory": [{"Name": "Legal Assistance", "Code": "0986"}],
        "PositionRemuneration": [{"MinimumRange": "18.50", "MaximumRange": "24.00", "RateIntervalCode": "PH",
                                  "Description": "Per Hour"}],
        "PublicationStartDate": "2026-09-20T00:00:00.0000000", "ApplicationCloseDate": "2026-10-20T23:59:59.9970000",
        "QualificationSummary": "Must be enrolled at least half-time.",
        "UserArea": {"Details": {"JobSummary": "Support attorneys with legal research and case files.",
                                 "MajorDuties": ["Maintain case files", "Draft correspondence"]}}}},
    {"MatchedObjectId": "2", "MatchedObjectDescriptor": {
        "PositionID": "SI-27-002", "PositionTitle": "Student Trainee (Museum Specialist)",
        "PositionURI": "https://www.usajobs.gov/job/2", "PositionLocationDisplay": "Washington, District of Columbia",
        "OrganizationName": "Smithsonian Institution", "JobCategory": [{"Name": "Museum Specialist And Technician"}],
        "PositionRemuneration": [{"MinimumRange": "0", "MaximumRange": "0", "RateIntervalCode": "WC"}],
        "PublicationStartDate": "2026-09-25T00:00:00.0000000"}},
    {"MatchedObjectId": "3", "MatchedObjectDescriptor": {
        "PositionID": "NIH-27-003", "PositionTitle": "Student Trainee (Biological Science)",
        "PositionURI": "https://www.usajobs.gov/job/3", "OrganizationName": "National Institutes of Health",
        "JobCategory": [{"Name": "General Natural Resources Management And Biological Sciences"}]}},
]}}


def test_parse_usajobs():
    postings = {p.id: p for p in parse_usajobs(USAJOBS_PAYLOAD)}
    assert set(postings) == {"usajobs:DOJ-27-001", "usajobs:SI-27-002"}  # biology trainee is off-focus
    doj = postings["usajobs:DOJ-27-001"]
    assert doj.category == "Legal" and doj.company == "Office of the U.S. Attorney"
    assert doj.pay == "paid" and doj.pay_detail == "$18.50–$24.00 per hour"
    assert doj.deadline.month == 10 and "legal research" in doj.description
    assert doj.locations == ["Washington, District of Columbia"]
    museum = postings["usajobs:SI-27-002"]
    assert museum.category == "Arts & Culture" and museum.pay == "unpaid"


def test_fetch_usajobs_sends_key_headers(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=USAJOBS_PAYLOAD)

    settings = {"usajobs_api_key": "KEY", "usajobs_email": "me@example.edu"}.get
    s = ListingStore(cache_dir=tmp_path, transport=httpx.MockTransport(handler), settings=settings)
    postings, _ = run(s.fetch_source("usajobs"))
    assert len(postings) == 2
    assert seen[0].url.copy_with(query=None) == httpx.URL(USAJOBS_URL)
    assert seen[0].headers["Authorization-Key"] == "KEY" and seen[0].headers["User-Agent"] == "me@example.edu"
    assert seen[0].url.params["HiringPath"] == "student"


ADZUNA_PAYLOAD = {"count": 3, "results": [
    {"id": "a1", "title": "Legal <strong>Intern</strong>", "description": "Assist with legal research...",
     "redirect_url": "https://www.adzuna.com/land/ad/a1", "created": iso(2),
     "company": {"display_name": "Morgan & Pike LLP"}, "location": {"display_name": "Chicago, Cook County"},
     "category": {"tag": "legal-jobs", "label": "Legal Jobs"}, "salary_min": 20, "salary_max": 22,
     "salary_is_predicted": "0"},
    {"id": "a2", "title": "Paralegal", "description": "Full-time paralegal.", "redirect_url": "x",
     "company": {"display_name": "X"}, "category": {"tag": "legal-jobs"}},
    {"id": "a3", "title": "Summer Intern", "description": "Help our team.", "redirect_url": "y", "created": iso(1),
     "company": {"display_name": "Bright Nonprofit"}, "category": {"tag": "charity-voluntary-jobs",
                                                                    "label": "Charity & Voluntary Jobs"}},
]}


def test_parse_adzuna():
    postings = {p.id: p for p in parse_adzuna(ADZUNA_PAYLOAD)}
    assert set(postings) == {"adzuna:a1", "adzuna:a3"}  # a full-time paralegal job isn't an internship
    assert postings["adzuna:a1"].title == "Legal Intern" and postings["adzuna:a1"].pay_detail == "$20–$22 per hour"
    assert postings["adzuna:a3"].category == "Nonprofit & Advocacy"


def test_fetch_adzuna(tmp_path):
    calls: list[str] = []
    settings = {"adzuna_app_id": "id", "adzuna_app_key": "key"}.get
    s = ListingStore(cache_dir=tmp_path, settings=settings,
                     transport=mock_transport({ADZUNA_URL.format(page=1) + "*": ADZUNA_PAYLOAD}, calls))
    postings, detail = run(s.fetch_source("adzuna"))
    assert {p.id for p in postings} == {"adzuna:a1", "adzuna:a3"}
    assert all("app_id=id" in c and "what=intern" in c for c in calls)
    assert "legal-jobs" in detail["queries"]


def test_sources_enabled_by_keys(tmp_path):
    s = ListingStore(cache_dir=tmp_path, settings=no_keys)
    assert s.enabled("themuse") and not s.enabled("usajobs") and not s.enabled("adzuna")
    s2 = ListingStore(cache_dir=tmp_path, settings={"usajobs_api_key": "k", "usajobs_email": "e"}.get)
    assert s2.enabled("usajobs")


def test_store_downloads_and_caches(store, tmp_path, rows):
    postings = run(store.get_postings())
    assert len(postings) == 15  # 16 rows minus the software job; the duplicate is removed later, when ranking
    status = store.status()
    assert status["count"] == 15 and status["sources"]["themuse"]["count"] == 15
    assert not status["sources"]["usajobs"]["enabled"]
    # A new store reads the on-disk cache without touching the network.
    offline = ListingStore(cache_dir=tmp_path / "store", transport=muse_transport(rows, fail=True), settings=no_keys)
    assert len(run(offline.get_postings())) == 15


def test_store_falls_back_to_stale_cache(store, tmp_path, rows):
    run(store.get_postings())
    path = tmp_path / "store" / "source_themuse.json"
    payload = json.loads(path.read_text())
    payload["fetched_at"] = 0  # make it stale
    path.write_text(json.dumps(payload))
    broken = ListingStore(cache_dir=tmp_path / "store", transport=muse_transport(rows, fail=True), settings=no_keys)
    assert len(run(broken.get_postings())) == 15
    assert "Using saved results" in broken.status()["sources"]["themuse"]["error"]


def test_store_raises_without_any_data(tmp_path, rows):
    s = ListingStore(cache_dir=tmp_path, transport=muse_transport(rows, fail=True), settings=no_keys)
    with pytest.raises(RuntimeError, match="Could not load any internship listings"):
        run(s.get_postings())


def test_board_spec_and_detail_source():
    assert parse_board_spec("greenhouse:nytimes") == ("greenhouse", "nytimes")
    assert parse_board_spec("https://jobs.lever.co/acme") == ("lever", "acme")
    assert parse_board_spec("workday:acme") is None
    assert detail_source("https://job-boards.greenhouse.io/nytimes/jobs/123") == ("greenhouse", "nytimes", "123")
    assert detail_source("https://www.themuse.com/jobs/x/1") is None


def test_board_parsers_keep_only_relevant_internships():
    gh = parse_greenhouse_board("nytimes", {"jobs": [
        {"id": 1, "title": "Editorial Intern", "absolute_url": "https://gh/1", "location": {"name": "New York, NY"},
         "content": "&lt;p&gt;Copyediting and AP style&lt;/p&gt;", "company_name": "The New York Times"},
        {"id": 2, "title": "Software Engineering Intern", "absolute_url": "https://gh/2", "content": ""},
        {"id": 3, "title": "Senior Editor", "absolute_url": "https://gh/3", "content": ""},
    ]})
    assert [p.title for p in gh] == ["Editorial Intern"]
    assert gh[0].category == "Media & Writing" and "AP style" in gh[0].description
    lv = parse_lever_board("acme", [
        {"id": "a", "text": "Summer Intern", "hostedUrl": "https://lever/a", "createdAt": 1790000000000,
         "categories": {"location": "Chicago, IL", "commitment": "Internship", "team": "Marketing"},
         "descriptionPlain": "Social media and events."},
    ])
    assert lv[0].category == "Marketing & Communications"


def test_html_to_text():
    raw = "&lt;p&gt;We use &lt;strong&gt;Westlaw&lt;/strong&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Research&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "We use Westlaw" in text and "- Research" in text and "<" not in text


def test_enrich_fetches_and_caches_descriptions(tmp_path, postings):
    gh_url = "https://job-boards.greenhouse.io/acme/jobs/123"
    target = postings[0].model_copy(update={"url": gh_url, "description": ""})
    calls: list[str] = []
    s = ListingStore(cache_dir=tmp_path, settings=no_keys, transport=mock_transport({
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123": {"content": "&lt;p&gt;Westlaw research&lt;/p&gt;"},
    }, calls))
    assert run(s.enrich([target])) == 1 and "Westlaw" in target.description
    again = target.model_copy(update={"description": ""})
    s2 = ListingStore(cache_dir=tmp_path, settings=no_keys, transport=mock_transport({}, calls))
    assert run(s2.enrich([again])) == 1 and len(calls) == 1  # served from the on-disk cache
