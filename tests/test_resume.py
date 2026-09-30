import io
from datetime import date

import pytest

from internmatch.models import Profile
from internmatch.resume import ResumeParseError, extract_text, parse_resume, school_level, score_resume

from .conftest import FIXTURES


def test_parse_sample_resume(sample_text):
    pr = parse_resume(sample_text)
    assert {"education", "experience", "projects", "leadership", "skills"} <= set(pr.sections)
    assert pr.contact == {"email": True, "phone": True, "linkedin": True, "github": True, "portfolio": False}
    assert pr.gpa == 3.72
    assert pr.degree == "bachelor"
    assert pr.major == "Computer Science"
    assert pr.grad_year == 2028
    assert pr.roles == 2 and pr.internships == 1
    assert pr.projects == 3
    assert pr.research and pr.leadership
    assert len(pr.bullets) == 11
    weak = [b for b in pr.bullets if b.weak_opener]
    assert len(weak) == 1 and weak[0].text.startswith("Worked on")
    assert {"Python", "C++", "PyTorch", "React", "Docker"} <= set(pr.skills)


def test_strong_resume_outscores_weak(sample_text, weak_text):
    strong = score_resume(parse_resume(sample_text))
    weak = score_resume(parse_resume(weak_text))
    assert strong.overall >= 85
    assert weak.overall < 55
    assert strong.grade.startswith("A")
    assert weak.grade in {"C", "D"}
    for report in (strong, weak):
        assert all(0 <= s.score <= 100 for s in report.subscores)
        assert abs(sum(s.weight for s in report.subscores) - 1) < 1e-9


def test_weak_resume_gets_actionable_fixes(weak_text):
    report = score_resume(parse_resume(weak_text))
    text = " ".join(report.improvements)
    assert "number" in text  # quantify impact
    assert "action verb" in text
    assert "GitHub" in text
    assert report.weak_bullets and all(b.issues for b in report.weak_bullets)
    assert report.detected["degree"] == "associate"


def test_profile_overrides_detected_values(sample_text):
    report = score_resume(parse_resume(sample_text), Profile(gpa=2.8, grad_year=2030))
    academics = next(s for s in report.subscores if s.key == "academics")
    assert "2.80" in academics.detail
    assert report.detected["gpa"] == 3.72  # what the resume says is still reported


def test_school_level():
    today = date(2026, 9, 30)
    assert school_level(2027, "bachelor", today) == "Senior"
    assert school_level(2028, "bachelor", today) == "Junior"
    assert school_level(2029, "bachelor", today) == "Sophomore"
    assert school_level(2030, "bachelor", today) == "Freshman"
    assert school_level(2026, "bachelor", today) == "Recent graduate"
    assert school_level(2027, "master", today) == "Graduate student"
    assert school_level(None, None, today) == "Junior"


def test_extract_pdf():
    text, pages = extract_text("resume.pdf", (FIXTURES / "sample_resume.pdf").read_bytes())
    assert pages == 1
    assert "Jordan Lee" in text and "PyTorch" in text
    report = score_resume(parse_resume(text, pages=pages))
    assert report.stats["pages"] == 1
    assert report.overall >= 85


def test_extract_docx_marks_list_paragraphs_as_bullets():
    import docx

    doc = docx.Document()
    doc.add_paragraph("EXPERIENCE")
    doc.add_paragraph("Software Intern, Acme  Jun 2026 – Aug 2026")
    doc.add_paragraph("Built a Go service handling 5,000 requests per second", style="List Bullet")
    doc.add_paragraph("Reduced deploy time by 40% with GitHub Actions", style="List Bullet")
    doc.add_paragraph("SKILLS")
    doc.add_paragraph("Go, Python, Docker")
    buf = io.BytesIO()
    doc.save(buf)
    text, pages = extract_text("resume.docx", buf.getvalue())
    assert pages is None
    assert "• Built a Go service" in text
    pr = parse_resume(text)
    assert len(pr.bullets) == 2 and all(b.quantified and b.action for b in pr.bullets)


def test_extract_rejects_bad_files():
    with pytest.raises(ResumeParseError):
        extract_text("resume.doc", b"\xd0\xcf\x11\xe0 legacy word file")
    with pytest.raises(ResumeParseError):
        extract_text("resume.pdf", b"%PDF-1.4 garbage")


def test_plain_text_decoding():
    text, _ = extract_text("resume.txt", "Café Résumé".encode("latin-1"))
    assert "Caf" in text
