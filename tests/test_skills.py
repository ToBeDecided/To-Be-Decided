from internmatch.skills import (
    canonical,
    categories_for,
    classify,
    classify_description,
    extract_languages,
    extract_skills,
    title_requirements,
)


def test_legal_business_and_humanities_skills():
    text = """Conducted legal research on Westlaw; drafted legal memos in Bluebook format for a law firm.
    Built DCF and LBO models in Excel; M&A pitch books; passed the SIE exam.
    Copyedited 30 stories in AP style for the student newspaper; catalogued prints for the museum archives.
    Wrote grant proposals and coordinated volunteers; ran Instagram and TikTok accounts using Canva."""
    found = set(extract_skills(text))
    assert {"Legal Research", "Westlaw", "Legal Writing", "Bluebook", "Legal Office Experience"} <= found
    assert {"Valuation", "Excel", "Investment Banking", "Securities Licenses"} <= found
    assert {"Copyediting", "Style Guides", "Journalism", "Collections Management", "Archival Research"} <= found
    assert {"Grant Writing", "Volunteer Coordination", "Social Media", "Canva"} <= found


def test_common_false_positives_are_ignored():
    text = "I excel at teamwork. April events in Spring 2025. John R. Smith; R&D lab; Latin honors; Greek life."
    found = extract_skills(text)
    assert not {"Excel", "Public Relations", "R", "Latin", "Greek", "Event Planning"} & set(found)


def test_languages_need_language_context():
    assert extract_languages("Languages: Spanish (fluent), Mandarin (conversational)") == {"Spanish": 1, "Mandarin": 1}
    assert extract_languages("Wrote a thesis on the French Revolution") == {}
    assert extract_languages("Bilingual in English and Chinese") == {"Mandarin": 1}


def test_canonical():
    assert canonical("lexis") == "LexisNexis"
    assert canonical("mock trial") == "Mock Trial"
    assert canonical("spanish") == "Spanish"
    assert canonical("underwater basket weaving") is None


def test_classify_titles():
    assert classify("Legal Intern") == ("Legal", "strong")
    assert classify("Healthcare Policy Intern") == ("Government & Policy", "strong")
    assert classify("Summer Analyst - Investment Banking")[0] == "Finance & Accounting"
    assert classify("Curatorial Intern")[0] == "Arts & Culture"
    assert classify("Development Intern")[0] == "Nonprofit & Advocacy"
    assert classify("Software Engineering Intern") == ("Other", "off")
    assert classify("Clinical Research Intern") == ("Other", "off")
    assert classify("Data Analyst Intern") == ("Consulting & Business", "weak")
    assert classify("Summer Intern") == (None, "none")
    assert classify("Operations Analyst Intern") == ("Consulting & Business", "weak")  # "opera" is not arts
    assert classify("Opera Production Intern")[0] == "Arts & Culture"
    assert classify("Intern, R&D Bioresource Security") == ("Other", "off")
    assert classify("27 Intern | US | ES&H Safety")[1] == "off"
    assert classify("27 Intern | US | Elec Field Eng")[1] == "off"
    assert classify("Public Safety Policy Intern")[0] == "Government & Policy"
    # What the intern does outranks the department or product they do it for.
    assert classify("Intern - Accounts Receivable & Collections")[0] == "Finance & Accounting"
    assert classify("2027 Summer Intern: Human Resources - Spectrum News")[0] == "Consulting & Business"
    assert classify("Art & Nature Category Management Project Intern (TikTok Shop)")[0] == "Consulting & Business"
    assert classify("Museum Collections Intern")[0] == "Arts & Culture"
    assert classify("Newsroom Intern")[0] == "Media & Writing"
    assert classify("Intern, Product Analytics Leadership Development Program") == ("Consulting & Business", "weak")
    assert classify("GE Vernova - Aeroderivative Industrialization Internship")[1] == "off"
    assert classify("2027 Summer Intern, Global Customer Research Intern")[0] == "Marketing & Communications"
    assert classify("Research Intern")[0] == "Education & Research"


def test_tracks_expand_to_categories():
    assert categories_for(["Pre-Law"]) == {"Legal", "Government & Policy", "Nonprofit & Advocacy"}
    assert "Marketing & Communications" in categories_for(["Business"])
    assert categories_for([], ["Legal"]) == {"Legal"}
    assert len(categories_for([])) == 9


def test_title_requirements():
    assert [label for label, _ in title_requirements("Paralegal Intern")] == ["Legal"]
    assert "Investment finance" in [label for label, _ in title_requirements("Investment Banking Summer Analyst")]
    assert [label for label, _ in title_requirements("Summer Intern")] == ["General"]
    # An accounts-receivable role doesn't ask for curatorial research because "Collections" is in the title.
    assert [label for label, _ in title_requirements("Accounts Receivable & Collections Intern")] == ["Accounting"]
    assert [label for label, _ in title_requirements("Human Resources Intern - Spectrum News")] == ["HR & recruiting"]


def test_generic_skills_dont_decide_a_field():
    generic = {"Research": 2, "Writing": 2, "Public Speaking": 1, "Microsoft Office": 1}
    assert classify_description(generic, min_weight=4.5)[0] is not None  # white-collar, some field...
    assert classify_description(generic, min_weight=4.5, generic_weight=0.3)[0] is None  # ...but not which one
    teaching = {"Tutoring": 1, "Teaching": 1, "Writing": 1}
    assert classify_description(teaching, min_weight=4.5, generic_weight=0.3)[0] == "Education & Research"
