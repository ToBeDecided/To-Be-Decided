from datetime import datetime, timezone

from internmatch.companies import selectivity
from internmatch.matcher import build_candidate, expand_locations, location_match, rank, recommend
from internmatch.models import Posting, Profile
from internmatch.resume import parse_resume, score_resume


def candidate(text: str, profile: Profile):
    pr = parse_resume(text)
    return build_candidate(pr, score_resume(pr, profile), profile)


def by_company(matches):
    return {(m.posting.company, m.posting.title): m for m in matches}


def test_eligibility_filters(postings, sample_text, summer_profile):
    matches, excluded = rank(postings, candidate(sample_text, summer_profile))
    got = {m.posting.company for m in matches}
    assert "Stale Co" not in got and excluded["term"] == 1
    assert "Closed Co" not in got  # inactive rows are dropped at parse time
    assert "Hooli" not in got and excluded["degree"] == 1  # PhD-only
    assert "Vandelay" not in got and excluded["level"] == 1  # freshman/sophomore program, candidate is a junior
    assert excluded["duplicate"] == 1
    assert "Contoso Defense" in got  # citizen by default


def test_sponsorship_filter(postings, sample_text):
    prof = Profile(target_terms=["Summer 2027"], work_authorization="needs_sponsorship")
    matches, excluded = rank(postings, candidate(sample_text, prof))
    assert "Contoso Defense" not in {m.posting.company for m in matches}
    assert excluded["sponsorship"] == 1


def test_sponsorship_inferred_from_description(sample_text):
    p = Posting(id="x", source="t", company="Contoso", title="Software Engineer Intern", terms=["Summer 2027"],
                description="Applicants must be a U.S. citizen and able to obtain a security clearance.")
    prof = Profile(work_authorization="needs_sponsorship")
    matches, excluded = rank([p], candidate(sample_text, prof))
    assert not matches and excluded["sponsorship"] == 1


def test_software_resume_prefers_software_roles(postings, sample_text, summer_profile):
    matches, _ = rank(postings, candidate(sample_text, summer_profile))
    m = by_company(matches)
    swe = m[("Acme Logistics", "Software Engineer Intern")]
    fpga = m[("Umbrella Silicon", "FPGA Design Intern")]
    assert swe.match_score > fpga.match_score + 30
    assert swe.likelihood > fpga.likelihood
    assert swe.tier == "Likely"
    assert "Python" in swe.matched_skills


def test_hardware_resume_prefers_hardware_roles(postings, hardware_text, summer_profile):
    matches, _ = rank(postings, candidate(hardware_text, summer_profile))
    m = by_company(matches)
    fpga = m[("Umbrella Silicon", "FPGA Design Intern")]
    pm = m[("Soylent", "Product Manager Intern")]
    assert fpga.match_score > pm.match_score
    assert fpga.tier == "Likely"
    assert {"Verilog", "FPGA"} & set(fpga.matched_skills)


def test_elite_companies_are_never_likely(postings, sample_text, summer_profile):
    matches, _ = rank(postings, candidate(sample_text, summer_profile))
    js = by_company(matches)[("Jane Street", "Software Engineer Intern")]
    acme = by_company(matches)[("Acme Logistics", "Software Engineer Intern")]
    assert js.selectivity == "elite"
    assert js.tier != "Likely"
    assert js.likelihood < acme.likelihood
    assert any("selective" in w for w in js.warnings)


def test_weak_resume_has_lower_odds(postings, sample_text, weak_text):
    prof = Profile(target_terms=["Summer 2027"], degree_level="bachelor")
    strong = by_company(rank(postings, candidate(sample_text, prof))[0])
    weak = by_company(rank(postings, candidate(weak_text, prof))[0])
    key = ("Acme Logistics", "Software Engineer Intern")
    assert weak[key].likelihood < strong[key].likelihood - 20


def test_stale_postings_rank_lower(sample_text):
    def post(days):
        return Posting(id=f"p{days}", source="t", company=f"Co{days}", title="Embedded Firmware Intern",
                       category="Hardware", terms=["Summer 2027"],
                       date_posted=datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - days * 86400,
                                                          tz=timezone.utc))
    matches, _ = rank([post(40), post(1)], candidate(sample_text, Profile()))
    assert [m.posting.id for m in matches] == ["p1", "p40"]
    assert any("40 days" in w for w in matches[1].warnings)


def test_early_programs_boost_underclassmen(postings, weak_text):
    prof = Profile(target_terms=["Summer 2027"], degree_level="bachelor", grad_year=2030)
    matches, excluded = rank(postings, candidate(weak_text, prof))
    vandelay = by_company(matches)[("Vandelay", "Explore Program Intern (Freshman/Sophomore)")]
    assert any("early-year" in r for r in vandelay.reasons)
    assert excluded["level"] == 0


def test_strict_location_and_category_filters(postings, sample_text):
    prof = Profile(target_terms=["Summer 2027"], locations=["NYC"], location_strict=True, remote_ok=True,
                   target_categories=["Software"])
    matches, excluded = rank(postings, candidate(sample_text, prof))
    for m in matches:
        assert m.posting.category == "Software"
        assert any("New York" in loc or "Remote" in loc for loc in m.posting.locations)
    assert excluded["location"] >= 1 and excluded["category"] >= 1
    assert ("Acme Logistics", "Frontend Engineer Intern") in by_company(matches)  # remote counts


def test_recommend_caps_per_company(postings, sample_text, summer_profile):
    matches, _ = rank(postings, candidate(sample_text, summer_profile))
    picked = recommend(matches, n=5, per_company=1)
    companies = [m.posting.company for m in picked]
    assert len(picked) == 5
    assert len(set(companies)) == len(companies)
    # Likely/Target roles fill the shortlist before any Reach.
    tiers = [m.tier for m in picked]
    first_reach = tiers.index("Reach") if "Reach" in tiers else len(tiers)
    assert "Reach" not in tiers[:first_reach] and all(t == "Reach" for t in tiers[first_reach:])


def test_location_matching():
    def hit(prefs, loc):
        return location_match(Posting(id="x", source="t", company="c", title="t", locations=[loc]),
                              expand_locations(prefs))

    assert hit(["NYC"], "New York, NY") and hit(["New York"], "NYC")
    assert hit(["Bay Area"], "Mountain View, CA")
    assert hit(["Texas"], "Dallas, TX") and hit(["TX"], "Austin, TX")
    assert hit(["USA"], "Seattle, WA")
    assert not hit(["LA"], "Atlanta, GA")
    assert not hit(["CA"], "Toronto, Canada")
    assert not hit(["USA"], "London, UK")


def test_selectivity_tiers():
    assert selectivity("Jane Street") == "elite"
    assert selectivity("Meta Platforms, Inc.") == "elite"
    assert selectivity("Metaview") == "standard"
    assert selectivity("Microsoft Corporation") == "high"
    assert selectivity("SIG Sauer") == "standard"
    assert selectivity("Acme Logistics") == "standard"
