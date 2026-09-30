from internmatch.skills import canonical, extract_skills, normalize_category, title_requirements


def test_symbols_and_short_names():
    found = extract_skills("Languages: C/C++, C#, Go, R, Node.js, .NET, Objective-C")
    for name in ("C", "C++", "C#", "Go", "R", "Node.js", ".NET", "Objective-C"):
        assert name in found, name
    # "js" inside "node.js" must not count as JavaScript
    assert "JavaScript" not in found


def test_java_is_not_javascript():
    found = extract_skills("Built a JavaScript app")
    assert "JavaScript" in found and "Java" not in found


def test_common_false_positives_are_ignored():
    text = "Spring 2025 semester. I excel at teamwork and react to incidents quickly. John C. Smith. R&D lab."
    found = extract_skills(text)
    assert not {"Spring", "Excel", "React", "C", "R"} & set(found)


def test_aliases_map_to_canonical_names():
    found = extract_skills("Used sklearn, k8s, postgres and GitHub Actions")
    assert {"scikit-learn", "Kubernetes", "PostgreSQL", "CI/CD"} <= set(found)
    assert canonical("golang") == "Go"
    assert canonical("pytorch") == "PyTorch"
    assert canonical("not a real skill") is None


def test_normalize_category():
    assert normalize_category("Software Engineering") == "Software"
    assert normalize_category("Data Science, AI & Machine Learning") == "AI/ML/Data"
    assert normalize_category("Quantitative Finance") == "Quant"
    assert normalize_category("Hardware Engineering") == "Hardware"
    assert normalize_category("Product Management") == "Product"
    assert normalize_category(None) == "Other"


def test_title_requirements_prefers_specific_rules():
    labels = [label for label, _ in title_requirements("Backend Software Engineer Intern")]
    assert labels == ["Backend"]
    labels = [label for label, _ in title_requirements("Software Engineer Intern")]
    assert labels == ["General software"]
    assert title_requirements("Marketing Intern") == []
