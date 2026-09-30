from datetime import datetime, timedelta, timezone

from internmatch.companies import selectivity
from internmatch.engine import posting_from_text
from internmatch.matcher import (
    build_candidate,
    check_posting,
    expand_locations,
    location_match,
    rank,
    recommend,
    summarize_profile,
)
from internmatch.models import Posting, Profile
from internmatch.resume import parse_resume, score_resume


def candidate(text: str, profile: Profile):
    pr = parse_resume(text)
    return build_candidate(pr, score_resume(pr, profile), profile)


def by_key(matches):
    return {(m.posting.company, m.posting.title): m for m in matches}


def test_off_focus_roles_never_reach_matching(postings):
    assert "TechCo" not in {p.company for p in postings}


def test_eligibility_filters(postings, prelaw_text, summer_profile):
    matches, excluded = rank(postings, candidate(prelaw_text, summer_profile))
    got = {m.posting.company for m in matches}
    assert "Cravath, Swaine & Moore LLP" not in got  # 2L summer associate role, candidate is an undergrad
    assert "Northwind Bank" not in got  # freshman/sophomore program, candidate is a junior
    assert excluded["degree"] == 1 and excluded["level"] == 1 and excluded["duplicate"] == 1
    assert "Office of the Public Defender" in got  # citizen by default
    assert "Old Mill Books" in got  # no term in the title, but the description says Summer 2027


def test_law_students_see_law_student_roles(postings, prelaw_text):
    matches, _ = rank(postings, candidate(prelaw_text, Profile(degree_level="jd")))
    assert "Cravath, Swaine & Moore LLP" in {m.posting.company for m in matches}


def test_sponsorship_and_paid_filters(postings, prelaw_text):
    prof = Profile(work_authorization="needs_sponsorship", paid_only=True)
    matches, excluded = rank(postings, candidate(prelaw_text, prof))
    got = {m.posting.company for m in matches}
    assert "Office of the Public Defender" not in got and excluded["sponsorship"] == 1
    assert "The Lakeshore Review" not in got and excluded["unpaid"] == 1
    assert "City Museum of Art" in got  # a stipend counts as paid


def test_term_filter_and_unknown_terms(postings, prelaw_text):
    strict = Profile(target_terms=["Summer 2027"], include_unknown_terms=False)
    matches, excluded = rank(postings, candidate(prelaw_text, strict))
    assert all("Summer 2027" in m.posting.terms for m in matches)
    assert excluded["term"] > 0


def test_tracks_filter(postings, prelaw_text):
    matches, excluded = rank(postings, candidate(prelaw_text, Profile(target_tracks=["Pre-Law"])))
    assert {m.posting.category for m in matches} <= {"Legal", "Government & Policy", "Nonprofit & Advocacy"}
    assert excluded["category"] > 0


def test_prelaw_resume_prefers_legal_roles(postings, prelaw_text):
    matches, _ = rank(postings, candidate(prelaw_text, Profile()))
    m = by_key(matches)
    legal = m[("Hartley Legal Aid", "Legal Intern")]
    marketing = m[("Luma Cosmetics", "Marketing Intern")]
    assert legal.match_score > marketing.match_score + 30
    assert legal.tier == "Likely"
    assert {"Legal Research", "Westlaw"} <= set(legal.matched_skills)
    assert any("$18 per hour" in r for r in legal.reasons)


def test_business_resume_prefers_finance_roles(postings, business_text):
    matches, _ = rank(postings, candidate(business_text, Profile()))
    m = by_key(matches)
    finance = m[("Riverbend Credit Union", "Finance Intern")]
    curatorial = m[("City Museum of Art", "Curatorial Intern")]
    assert finance.match_score > curatorial.match_score + 30
    assert finance.tier == "Likely"


def test_humanities_resume_prefers_media_and_arts(postings, humanities_text):
    matches, _ = rank(postings, candidate(humanities_text, Profile()))
    m = by_key(matches)
    editorial = m[("The Lakeshore Review", "Editorial Intern")]
    banking = m[("Evercore", "Investment Banking Summer Analyst")]
    assert editorial.match_score > banking.match_score + 30
    assert any("Unpaid" in w or "academic credit" in w for w in editorial.warnings)


def test_elite_employers_are_never_likely(postings, business_text):
    matches, _ = rank(postings, candidate(business_text, Profile()))
    ib = by_key(matches)[("Evercore", "Investment Banking Summer Analyst")]
    assert ib.selectivity == "elite" and ib.tier != "Likely"
    assert any("selective" in w for w in ib.warnings)


def test_weak_resume_has_lower_odds(postings, prelaw_text, weak_text):
    prof = Profile(degree_level="bachelor")
    strong = by_key(rank(postings, candidate(prelaw_text, prof))[0])
    weak = by_key(rank(postings, candidate(weak_text, prof))[0])
    key = ("Hartley Legal Aid", "Legal Intern")
    assert weak[key].likelihood < strong[key].likelihood - 20


def test_stale_postings_and_deadlines(prelaw_text):
    now = datetime.now(timezone.utc)

    def post(pid, days_ago, deadline_in=None):
        return Posting(id=pid, source="t", company=f"Co {pid}", title="Legal Intern", category="Legal",
                       date_posted=now - timedelta(days=days_ago),
                       deadline=now + timedelta(days=deadline_in) if deadline_in is not None else None)

    matches, excluded = rank([post("old", 40), post("new", 1, deadline_in=5), post("closed", 1, deadline_in=-1)],
                             candidate(prelaw_text, Profile()))
    assert [m.posting.id for m in matches] == ["new", "old"]
    assert excluded["closed"] == 1
    assert any("close in" in w for w in matches[0].warnings)
    assert any("40 days" in w for w in matches[1].warnings)


def test_postings_for_past_terms_are_closed(prelaw_text):
    now = datetime.now(timezone.utc)
    last = f"Summer {now.year - 1}"
    old = Posting(id="old", source="t", company="Co", title=f"Legal Intern ({last})", category="Legal", terms=[last])
    matches, excluded = rank([old], candidate(prelaw_text, Profile()))
    assert not matches and excluded["closed"] == 1


def test_underclassmen_get_early_programs(postings, weak_text):
    prof = Profile(degree_level="bachelor", grad_year=2030)
    matches, excluded = rank(postings, candidate(weak_text, prof))
    program = by_key(matches)[("Northwind Bank", "Explore Program Intern (Freshman/Sophomore)")]
    assert any("early-year" in r for r in program.reasons)
    assert excluded["level"] == 0


def test_strict_location(postings, prelaw_text):
    prof = Profile(locations=["DC", "Boston"], location_strict=True, remote_ok=True)
    matches, excluded = rank(postings, candidate(prelaw_text, prof))
    assert matches and excluded["location"] > 0
    for m in matches:
        assert any(x in loc for loc in m.posting.locations for x in ("Washington", "Boston", "Remote"))


def test_recommend_caps_per_employer(postings, prelaw_text):
    matches, _ = rank(postings, candidate(prelaw_text, Profile()))
    picked = recommend(matches, n=6, per_company=1)
    companies = [m.posting.company for m in picked]
    assert len(set(companies)) == len(companies)
    tiers = [m.tier for m in picked]
    first_reach = tiers.index("Reach") if "Reach" in tiers else len(tiers)
    assert all(t == "Reach" for t in tiers[first_reach:])


def test_summary_names_the_right_track(postings, prelaw_text, business_text, humanities_text):
    for text, track in ((prelaw_text, "Pre-Law"), (business_text, "Business"), (humanities_text, "Humanities")):
        c = candidate(text, Profile())
        pr = parse_resume(text)
        summary = summarize_profile(c, score_resume(pr), rank(postings, c)[1])
        assert summary.track_fit[0].category == track
        assert track in summary.notes[0]


def test_check_posting_flags_blockers(prelaw_text):
    c = candidate(prelaw_text, Profile())
    posting = posting_from_text("Summer Associate", "Big Firm LLP", "Open to rising 2L law students.")
    match, blocker = check_posting(posting, c)
    assert blocker and "law students" in blocker
    ok, none = check_posting(posting_from_text("Legal Intern", "Legal Aid", "Legal research on Westlaw."), c)
    assert none is None and ok.tier == "Likely"


def test_location_matching():
    def hit(prefs, loc):
        return location_match(Posting(id="x", source="t", company="c", title="t", locations=[loc]),
                              expand_locations(prefs))

    assert hit(["DC"], "Washington, District of Columbia")
    assert hit(["NY"], "New York, New York") and hit(["NYC"], "New York, NY")
    assert hit(["Bay Area"], "Palo Alto, CA")
    assert hit(["USA"], "Austin, TX") and not hit(["USA"], "London, UK")
    assert not hit(["LA"], "Atlanta, GA")


def test_selectivity_tiers():
    assert selectivity("Goldman Sachs & Co. LLC") == "elite"
    assert selectivity("Sullivan & Cromwell LLP") == "elite"
    assert selectivity("Bain & Company") == "elite"
    assert selectivity("Bain Street Bakery") == "standard"
    assert selectivity("Smithsonian Institution") == "elite"
    assert selectivity("Deloitte") == "high"
    assert selectivity("Library of Congress") == "high"
    assert selectivity("Citizens Bank") == "standard"
    assert selectivity("Hartley Legal Aid") == "standard"
