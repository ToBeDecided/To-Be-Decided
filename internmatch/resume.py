"""Resume text extraction and rule-based scoring.

The score is a transparent rubric (not a black box). Each sub-score comes
with the evidence behind it, and each weakness comes with a concrete fix.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

from .models import BulletFeedback, Profile, ResumeReport, SubScore
from .skills import CATEGORY_SIGNATURES, extract_skills, skill_group

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class ResumeParseError(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> tuple[str, int | None]:
    """Return (text, page_count) for a PDF, DOCX, or plain-text resume."""
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_text(data)
    if name.endswith(".docx") or data[:2] == b"PK":
        return _docx_text(data), None
    if name.endswith(".doc"):
        raise ResumeParseError("Old .doc files aren't supported. Save it as PDF or .docx and try again.")
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return data.decode("latin-1"), None


def _pdf_text(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise ResumeParseError(f"Couldn't read that PDF ({exc}).") from exc
    text = "\n".join(pages)
    if len(text.strip()) < 50:
        raise ResumeParseError(
            "That PDF has almost no extractable text (it may be a scanned image). "
            "Export your resume as a text-based PDF or upload the .docx."
        )
    return text, len(reader.pages)


def _docx_text(data: bytes) -> str:
    import docx

    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises a variety of zip/xml errors
        raise ResumeParseError(f"Couldn't read that .docx ({exc}).") from exc
    lines: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        is_list = "list" in (para.style.name or "").lower() or para._p.pPr is not None and para._p.pPr.numPr is not None
        lines.append(f"• {text}" if is_list and not _BULLET_RX.match(text) else text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    lines.append(cell.text.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_BULLET_RX = re.compile(r"^\s*([••●▪◦‣∙·➢⁃►*–—-]|o\s)\s*")

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "education": ("education", "academic background", "academics", "education & coursework"),
    "experience": (
        "experience", "work experience", "professional experience", "employment", "internships",
        "work history", "relevant experience", "industry experience", "technical experience",
    ),
    "projects": ("projects", "personal projects", "academic projects", "technical projects", "selected projects",
                 "software projects", "engineering projects", "project experience"),
    "skills": ("skills", "technical skills", "skills & interests", "skills and interests", "technologies",
               "core competencies", "languages & technologies", "tools", "technical proficiencies",
               "skills & tools", "languages and tools"),
    "leadership": ("leadership", "activities", "extracurriculars", "extracurricular activities", "involvement",
                   "organizations", "volunteer", "volunteering", "leadership & activities",
                   "leadership experience", "campus involvement", "community involvement"),
    "awards": ("awards", "honors", "achievements", "honors & awards", "awards & honors", "certifications",
               "certificates", "awards and honors"),
    "coursework": ("coursework", "relevant coursework"),
    "summary": ("summary", "objective", "profile", "about me", "professional summary"),
    "research": ("research", "research experience", "publications", "research & publications"),
}
_HEADER_LOOKUP = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}

ACTION_VERBS = frozenset(
    """
    accelerated accomplished achieved acquired adapted addressed administered advanced advised analyzed
    applied architected arranged assembled assessed audited authored automated balanced benchmarked boosted
    built calculated calibrated captured championed characterized classified cleaned coached coded collaborated
    collected compiled completed composed computed conceived conceptualized conducted configured consolidated
    constructed consulted contributed converted coordinated created cultivated curated customized cut debugged
    decreased defined delivered demonstrated deployed designed detected determined developed devised diagnosed
    digitized directed discovered drafted drove eliminated enabled engineered enhanced established evaluated
    executed expanded expedited experimented explored extended extracted fabricated facilitated fixed forecasted
    formulated founded generated guided halved handled headed identified implemented improved increased
    initiated innovated inspected installed instituted integrated interfaced interpreted introduced invented
    investigated launched led leveraged maintained managed mapped maximized measured mentored merged migrated
    minimized modeled modernized modified monitored motivated negotiated obtained operated optimized orchestrated
    organized overhauled oversaw parallelized partnered performed pioneered piloted planned prepared presented
    prioritized processed produced profiled programmed prototyped proposed provisioned published quantified
    raised ran rebuilt recommended redesigned reduced refactored refined reengineered released remodeled
    reorganized replaced reported researched resolved restructured revamped reviewed revised rewrote saved
    scaled scheduled scoped scripted secured selected served shipped simplified simulated slashed solved
    spearheaded specified standardized started streamlined strengthened structured supervised surveyed
    synthesized systematized taught tested trained transformed translated troubleshot tuned tutored
    unified upgraded validated verified visualized won wrote
    """.split()
)
# Also accept present tense for current roles ("Build", "Design") via a simple stem check.
_WEAK_OPENERS = (
    "responsible for", "helped", "help ", "worked on", "work on", "assisted", "assist ", "participated",
    "involved in", "tasked with", "duties included", "in charge of", "was ", "did ", "made ", "handled",
    "exposure to", "learned", "familiar with", "utilized",
)

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RANGE_RX = re.compile(
    rf"(?:{_MONTH}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{2,4}}|(?:summer|fall|spring|winter)\s+\d{{4}}|\b(?:19|20)\d{{2}})"
    rf"\s*(?:-|–|—|to|until)\s*"
    rf"(?:{_MONTH}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{2,4}}|\b(?:19|20)\d{{2}}|present|current|now|ongoing)",
    re.I,
)
_SEASON_RX = re.compile(r"\b(?:summer|fall|spring|winter)\s+(?:19|20)\d{2}\b", re.I)
_EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RX = re.compile(r"(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_LINKEDIN_RX = re.compile(r"linkedin\.com/in/[\w%-]+", re.I)
_GITHUB_RX = re.compile(r"github\.com/[\w-]+", re.I)
_URL_RX = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+\.(?:dev|io|me|com|net|org|app|site|xyz|ai|tech)(?:/[\w./-]*)?\b",
                     re.I)
_GPA_RX = re.compile(
    r"(?:gpa|g\.p\.a\.?|grade point average|cumulative)[^0-9\n]{0,20}([0-4]\.\d{1,3})(?:\s*/\s*(\d{1,2}(?:\.\d+)?))?",
    re.I,
)
_GPA_FALLBACK_RX = re.compile(r"\b([0-4]\.\d{1,2})\s*/\s*4\.0+\b")
_YEAR_RX = re.compile(r"\b(20\d{2})\b")
_PRONOUN_RX = re.compile(r"\b(I|me|my|My|we|our|Our)\b")

_DEGREE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phd", re.compile(r"\bPh\.?\s?D\b|\bDoctor of\b|\bDoctorate\b", re.I)),
    ("mba", re.compile(r"\bMBA\b")),
    ("master", re.compile(r"\bMasters?\b|\bMaster's\b|\bM\.S\.|\bM\.Sc\b|\bMSc\b|\bM\.Eng\b|\bMEng\b|\bMS (?:in|of)\b|"
                          r"\bM\.A\.|\bMSCS\b|\bMSE\b")),
    ("bachelor", re.compile(r"\bBachelors?\b|\bBachelor's\b|\bB\.S\.|\bB\.A\.|\bBSc\b|\bB\.Sc\b|\bB\.Eng\b|\bBEng\b|"
                            r"\bB\.?S\.? (?:in|of)\b|\bB\.?A\.? (?:in|of)\b|\bBSE\b|\bBSEE\b|\bBSCS\b|\bB\.?Tech\b|"
                            r"\bBS\b|\bBA\b|\bundergraduate\b", re.I)),
    ("associate", re.compile(r"\bAssociate'?s? (?:of|in|degree)\b|\bA\.S\.|\bA\.A\.", re.I)),
    ("high_school", re.compile(r"\bHigh School\b", re.I)),
)
_DEGREE_RANK = {"high_school": 0, "associate": 1, "bachelor": 2, "master": 3, "mba": 3, "phd": 4}

MAJORS = (
    "Electrical and Computer Engineering", "Computer Science and Engineering", "Computer Science",
    "Computer Engineering", "Electrical Engineering", "Software Engineering", "Data Science",
    "Applied Mathematics", "Mathematics", "Statistics", "Physics", "Mechanical Engineering",
    "Aerospace Engineering", "Industrial Engineering", "Operations Research", "Information Science",
    "Information Systems", "Information Technology", "Cognitive Science", "Economics", "Finance",
    "Business Administration", "Chemical Engineering", "Biomedical Engineering", "Computational Biology",
    "Bioinformatics", "Robotics", "Human-Computer Interaction", "Cybersecurity", "Design",
)
TECH_MAJOR_WORDS = ("computer", "software", "electrical", "data", "math", "statistic", "physics", "engineering",
                    "information", "robotics", "cyber", "computational", "bioinformatics", "operations research")


@dataclass
class Bullet:
    text: str
    section: str
    words: int = 0
    action: bool = False
    weak_opener: str | None = None
    quantified: bool = False
    pronoun: bool = False

    def issues(self) -> list[str]:
        out = []
        if self.weak_opener:
            out.append(f"Starts with a weak phrase (\"{self.weak_opener.strip()}\"). Lead with what you did.")
        elif not self.action:
            out.append("Doesn't open with a strong action verb.")
        if not self.quantified:
            out.append("No number showing scale or impact.")
        if self.words > 35:
            out.append(f"Too long ({self.words} words). Aim for 1–2 lines.")
        elif self.words < 6:
            out.append("Too short to show impact.")
        if self.pronoun:
            out.append("Drop first-person pronouns (I/my/we).")
        return out


@dataclass
class ParsedResume:
    text: str
    lines: list[str]
    sections: dict[str, list[str]] = field(default_factory=dict)
    bullets: list[Bullet] = field(default_factory=list)
    skills: dict[str, int] = field(default_factory=dict)
    contact: dict[str, bool] = field(default_factory=dict)
    gpa: float | None = None
    degree: str | None = None
    major: str | None = None
    grad_year: int | None = None
    roles: int = 0
    internships: int = 0
    projects: int = 0
    research: bool = False
    leadership: bool = False
    awards: bool = False
    open_source: bool = False
    has_coursework: bool = False
    word_count: int = 0
    pages: int | None = None


def _header_key(line: str) -> str | None:
    clean = re.sub(r"[^a-zA-Z&/ ]", "", line).strip().lower()
    clean = re.sub(r"\s+", " ", clean)
    if not clean or len(clean) > 40:
        return None
    if clean in _HEADER_LOOKUP:
        return _HEADER_LOOKUP[clean]
    # Tolerate "Technical Skills & Interests"-style variants on short lines.
    words = clean.split()
    if len(words) <= 4 and (line.isupper() or line.rstrip().endswith(":")):
        for alias, key in sorted(_HEADER_LOOKUP.items(), key=lambda kv: -len(kv[0])):
            if alias in clean:
                return key
    return None


def _is_quantified(text: str) -> bool:
    stripped = _DATE_RANGE_RX.sub(" ", text)
    stripped = re.sub(r"\b(?:19|20)\d{2}\b", " ", stripped)
    stripped = re.sub(r"\b(?:C\+\+|Python|Java|ES)\s?\d+\b", " ", stripped)  # "Python 3", "ES6"
    return bool(re.search(r"\d", stripped)) or bool(
        re.search(r"\b(doubled|tripled|halved|dozens|hundreds|thousands|millions)\b", stripped, re.I)
    )


def _analyze_bullet(text: str, section: str) -> Bullet:
    words = text.split()
    b = Bullet(text=text, section=section, words=len(words))
    first = re.sub(r"[^a-z]", "", words[0].lower()) if words else ""
    lower = text.lower()
    b.weak_opener = next((w for w in _WEAK_OPENERS if lower.startswith(w)), None)
    b.action = b.weak_opener is None and (
        first in ACTION_VERBS
        or first + "d" in ACTION_VERBS  # "Automate" -> "automated"
        or first + "ed" in ACTION_VERBS  # "Build" -> no, but "Deploy" -> "deployed"
        or (first.endswith("s") and first[:-1] + "ed" in ACTION_VERBS)  # "Builds" style
        or first in {"build", "design", "develop", "lead", "create", "implement", "write", "run", "own", "ship"}
    )
    b.quantified = _is_quantified(text)
    b.pronoun = bool(_PRONOUN_RX.search(text))
    return b


def parse_resume(text: str, pages: int | None = None) -> ParsedResume:
    text = text.replace("\r", "\n").replace(" ", " ")
    raw_lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in raw_lines if ln]
    pr = ParsedResume(text=text, lines=lines, pages=pages)
    pr.word_count = len(re.findall(r"\b\w+\b", text))

    # --- sections -------------------------------------------------------
    current = "header"
    sections: dict[str, list[str]] = {"header": []}
    for ln in lines:
        key = _header_key(ln)
        if key:
            current = key
            sections.setdefault(current, [])
            # Inline header like "Skills: Python, Java" keeps its content.
            if ":" in ln and ln.split(":", 1)[1].strip():
                sections[current].append(ln.split(":", 1)[1].strip())
            continue
        sections.setdefault(current, []).append(ln)
    pr.sections = sections

    # --- bullets (merge wrapped continuation lines) ---------------------
    bullet_sections = {"experience", "projects", "leadership", "research", "summary", "header", "awards"}
    merged: list[tuple[str, str]] = []
    for sec, sec_lines in sections.items():
        for ln in sec_lines:
            m = _BULLET_RX.match(ln)
            if m and len(ln) > len(m.group(0)) + 2:
                merged.append((sec, ln[m.end():].strip()))
            elif merged and merged[-1][0] == sec and ln[:1].islower() and not _DATE_RANGE_RX.search(ln):
                merged[-1] = (sec, merged[-1][1] + " " + ln)
    if len(merged) < 3:
        # Few or no bullet glyphs survived extraction; treat sentence-like lines in body sections as bullets.
        fallback = [
            (sec, _BULLET_RX.sub("", ln, count=1))
            for sec in ("experience", "projects", "leadership", "research")
            for ln in sections.get(sec, [])
            if len(ln.split()) >= 6 and not _DATE_RANGE_RX.search(ln)
        ]
        if len(fallback) > len(merged):
            merged = fallback
    pr.bullets = [_analyze_bullet(t, s) for s, t in merged if s in bullet_sections]

    # --- contact --------------------------------------------------------
    head = "\n".join(lines[:12])
    no_emails = _EMAIL_RX.sub(" ", text)  # "jo@example.com" is not a portfolio site
    urls = [u for u in _URL_RX.findall(no_emails) if not re.search(r"linkedin|github", u, re.I)]
    pr.contact = {
        "email": bool(_EMAIL_RX.search(text)),
        "phone": bool(_PHONE_RX.search(head) or _PHONE_RX.search(text)),
        "linkedin": bool(_LINKEDIN_RX.search(text) or re.search(r"\blinkedin\b", head, re.I)),
        "github": bool(_GITHUB_RX.search(text) or re.search(r"\bgithub\b", head, re.I)),
        "portfolio": bool(urls),
    }

    # --- education ------------------------------------------------------
    edu_text = "\n".join(sections.get("education", [])) or text
    m = _GPA_RX.search(edu_text) or _GPA_RX.search(text)
    if m:
        value = float(m.group(1))
        scale = float(m.group(2)) if m.group(2) else 4.0
        if scale and scale != 4.0 and scale > value:
            value = value / scale * 4.0
        if 0 < value <= 4.3:
            pr.gpa = round(min(value, 4.0), 2)
    else:
        m2 = _GPA_FALLBACK_RX.search(edu_text)
        if m2:
            pr.gpa = float(m2.group(1))

    found = [lvl for lvl, rx in _DEGREE_PATTERNS if rx.search(edu_text)]
    if found:
        pr.degree = max(found, key=lambda d: _DEGREE_RANK[d])
    for major in MAJORS:
        if re.search(re.escape(major), edu_text, re.I):
            pr.major = major
            break

    this_year = date.today().year
    years = [int(y) for y in _YEAR_RX.findall(edu_text) if this_year - 6 <= int(y) <= this_year + 7]
    grad_hint = re.search(r"(?:expected|graduat\w*|class of|anticipated)[^0-9\n]{0,20}(20\d{2})", edu_text, re.I)
    if grad_hint:
        pr.grad_year = int(grad_hint.group(1))
    elif years:
        pr.grad_year = max(years)
    pr.has_coursework = "coursework" in sections or bool(re.search(r"coursework", text, re.I))

    # --- experience -----------------------------------------------------
    exp_lines = sections.get("experience", [])
    role_lines = [ln for ln in exp_lines if _DATE_RANGE_RX.search(ln) or _SEASON_RX.search(ln)]
    pr.roles = len(role_lines)
    exp_blob = "\n".join(exp_lines)
    pr.internships = len(re.findall(r"\bintern(?:ship)?\b|\bco-?op\b", exp_blob, re.I))
    pr.internships = min(pr.internships, pr.roles) if pr.roles else min(pr.internships, 2)
    if pr.roles == 0 and exp_lines:
        # Headers without parsable dates: count non-bullet title-ish lines.
        pr.roles = min(4, sum(1 for ln in exp_lines if not _BULLET_RX.match(ln) and len(ln.split()) <= 12
                              and ln[:1].isupper()) // 2)

    proj_lines = sections.get("projects", [])
    proj_headers = [
        ln for ln in proj_lines
        if not _BULLET_RX.match(ln) and ln[:1].isupper() and len(ln.split()) <= 14
    ]
    proj_bullets = sum(1 for b in pr.bullets if b.section == "projects")
    pr.projects = min(6, max(len(proj_headers), (proj_bullets + 2) // 3 if proj_bullets else 0))

    lower = text.lower()
    pr.research = "research" in sections or bool(re.search(r"research (assistant|intern|fellow)|undergraduate research", lower))
    pr.leadership = "leadership" in sections or bool(
        re.search(r"\b(president|vice president|founder|co-founder|captain|officer|chair|organizer|team lead|"
                  r"teaching assistant|mentor|tutor)\b", lower)
    )
    pr.awards = "awards" in sections or bool(
        re.search(r"hackathon|winner|\b1st place|first place|finalist|award|scholarship|dean'?s list|honors", lower)
    )
    pr.open_source = bool(re.search(r"open[- ]source|pull requests?|contributor to", lower))

    pr.skills = extract_skills(text)
    return pr


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

LEVELS = ("Freshman", "Sophomore", "Junior", "Senior", "Graduate student", "Recent graduate")


def school_level(grad_year: int | None, degree: str | None, today: date | None = None) -> str:
    today = today or date.today()
    if degree in {"master", "phd", "mba"}:
        return "Graduate student"
    if not grad_year:
        return "Junior"  # the most common internship applicant; neutral default
    school_year_end = today.year + 1 if today.month >= 8 else today.year
    years_left = grad_year - school_year_end
    if years_left < 0:
        return "Recent graduate"
    return {0: "Senior", 1: "Junior", 2: "Sophomore"}.get(years_left, "Freshman")


_EXPECTED_EXPERIENCE = {"Freshman": 30, "Sophomore": 50, "Junior": 75, "Senior": 90, "Graduate student": 90,
                        "Recent graduate": 95}


def category_affinity(skills: set[str]) -> dict[str, float]:
    out = {}
    for cat, sig in CATEGORY_SIGNATURES.items():
        total = sum(sig.values())
        got = sum(w for s, w in sig.items() if s in skills)
        out[cat] = min(1.0, got / (0.55 * total))
    return out


def _gpa_points(gpa: float | None) -> int:
    if gpa is None:
        return 60
    for cutoff, pts in ((3.8, 100), (3.6, 90), (3.4, 80), (3.2, 70), (3.0, 60), (2.7, 45)):
        if gpa >= cutoff:
            return pts
    return 35


def _grade(score: int) -> str:
    if score >= 88:
        return "A"
    if score >= 80:
        return "A-"
    if score >= 74:
        return "B+"
    if score >= 68:
        return "B"
    if score >= 62:
        return "B-"
    if score >= 55:
        return "C+"
    if score >= 48:
        return "C"
    return "D"


def score_resume(pr: ParsedResume, profile: Profile | None = None) -> ResumeReport:
    profile = profile or Profile()
    strengths: list[tuple[int, str]] = []
    fixes: list[tuple[int, str]] = []  # (priority, message) — lower priority number = more important

    gpa = profile.gpa if profile.gpa is not None else pr.gpa
    degree = profile.degree_level or pr.degree or "bachelor"
    grad_year = profile.grad_year or pr.grad_year
    level = school_level(grad_year, degree)
    skills = set(pr.skills) | {s for s in profile.extra_skills if s}
    technical = {s for s in skills if skill_group(s) not in {"product"}}

    # --- impact & writing ------------------------------------------------
    bullets = pr.bullets
    nb = len(bullets)
    if nb:
        action = sum(b.action for b in bullets)
        quant = sum(b.quantified for b in bullets)
        weak = sum(bool(b.weak_opener) for b in bullets)
        long_ = sum(b.words > 35 for b in bullets)
        short = sum(b.words < 6 for b in bullets)
        pron = sum(b.pronoun for b in bullets)
        length_ok = nb - long_ - short
        impact = 100 * (
            0.35 * action / nb + 0.35 * min(1.0, (quant / nb) / 0.6) + 0.2 * length_ok / nb + 0.1 * (1 - weak / nb)
        ) - min(10, 3 * pron)
        impact_detail = f"{action}/{nb} bullets start with action verbs, {quant}/{nb} include numbers"
        if action / nb >= 0.75:
            strengths.append((2, f"Strong writing: {action} of {nb} bullets open with an action verb."))
        elif action / nb < 0.6:
            fixes.append((2, f"{nb - action} of {nb} bullets don't open with a strong action verb. Start each with "
                             "a verb like Built, Designed, Automated, Reduced or Led."))
        if quant / nb >= 0.5:
            strengths.append((1, f"Results are quantified: {quant} of {nb} bullets include a number."))
        else:
            have = f"Only {quant} of {nb} bullets include" if quant else f"None of your {nb} bullets include"
            fixes.append((1, f"{have} a number. Add scale and impact (users, requests/sec, % faster, $ saved, "
                             "dataset size, team size). Numbers are what recruiters skim for."))
        if weak:
            fixes.append((3, f"{weak} bullet(s) start with passive phrases like \"Responsible for\" or \"Worked on\". "
                             "Say what you built or changed instead."))
        if long_:
            fixes.append((5, f"{long_} bullet(s) run past 35 words. Keep each to one or two lines."))
        if pron:
            fixes.append((6, "Remove first-person pronouns (I, my, we) from bullets."))
    else:
        impact = 20
        impact_detail = "No bullet points detected"
        fixes.append((1, "We couldn't find bullet points. Describe each role and project with 2–4 bullets that "
                         "start with an action verb and end with a measurable result."))

    # --- experience -------------------------------------------------------
    points = (
        pr.internships * 30
        + max(0, pr.roles - pr.internships) * 18
        + pr.projects * 12
        + (15 if pr.research else 0)
        + (8 if pr.leadership else 0)
        + (5 if pr.awards else 0)
        + (5 if pr.open_source else 0)
    )
    expected = _EXPECTED_EXPERIENCE[level]
    experience = max(10.0, min(100.0, 100 * points / expected))
    exp_bits = []
    if pr.roles:
        exp_bits.append(f"{pr.roles} role(s)" + (f", {pr.internships} internship(s)" if pr.internships else ""))
    if pr.projects:
        exp_bits.append(f"{pr.projects} project(s)")
    if pr.research:
        exp_bits.append("research")
    if pr.leadership:
        exp_bits.append("leadership")
    exp_detail = (", ".join(exp_bits) or "little experience detected") + f" (compared with a typical {level.lower()})"
    if pr.internships:
        strengths.append((0, f"Prior internship experience ({pr.internships}). This is the strongest signal "
                             "for recruiters."))
    if experience < 60:
        if pr.projects < 2:
            fixes.append((0, f"For a {level.lower()}, the experience section is thin. Add 2–3 substantial projects "
                             "(with a GitHub link, tech stack and measurable outcome), research, or a TA/tutoring role."))
        else:
            fixes.append((1, "Add more experience: research, a campus job in tech, open-source contributions or "
                             "hackathons all count."))
    if pr.projects >= 2:
        strengths.append((3, f"{pr.projects} projects listed. Good evidence that you build things."))
    elif "projects" not in pr.sections:
        fixes.append((2, "Add a Projects section. For internship applicants it's often the most-read section."))

    # --- skills -----------------------------------------------------------
    aff = category_affinity(skills)
    best_cat, best_aff = max(aff.items(), key=lambda kv: kv[1])
    breadth = min(1.0, len(technical) / 16)
    skills_score = 100 * (0.5 * breadth + 0.4 * best_aff + 0.1 * ("skills" in pr.sections))
    skills_detail = f"{len(skills)} skills detected; strongest fit: {best_cat}"
    if len(technical) >= 12:
        strengths.append((4, f"Broad technical toolkit ({len(technical)} technologies detected)."))
    elif len(technical) < 8:
        fixes.append((2, f"Only {len(technical)} technical skills detected. List the languages, frameworks, databases "
                         "and tools you've actually used in a dedicated Skills section (ATS filters match on keywords)."))
    if "skills" not in pr.sections:
        fixes.append((3, "Add a clearly labeled \"Skills\" section. Applicant tracking systems look for it."))

    # --- academics --------------------------------------------------------
    academics = 0.8 * _gpa_points(gpa) + 0.2 * (100 if pr.has_coursework else 55)
    acad_detail = f"GPA {gpa:.2f}" if gpa is not None else "GPA not listed"
    if pr.major:
        acad_detail += f", {pr.major}"
    if gpa is None:
        fixes.append((4, "No GPA found. If it's 3.3 or higher, list it. Many internship screens filter on GPA."))
    elif gpa >= 3.6:
        strengths.append((5, f"Strong GPA ({gpa:.2f})."))
    elif gpa < 3.0:
        fixes.append((6, "A GPA under 3.0 can hurt automated screens. Consider leaving it off and leaning on projects."))
    if not pr.has_coursework and level in {"Freshman", "Sophomore"}:
        fixes.append((5, "Add a line of relevant coursework (e.g. Data Structures, Algorithms) to show fundamentals."))

    # --- format & completeness ---------------------------------------------
    fmt = 0.0
    for sec in ("education", "skills"):
        fmt += 20 if sec in pr.sections else 0
    fmt += 20 if ("experience" in pr.sections or "projects" in pr.sections) else 0
    fmt += 10 * pr.contact["email"] + 5 * pr.contact["phone"] + 5 * pr.contact["linkedin"]
    fmt += 5 * (pr.contact["github"] or pr.contact["portfolio"])
    too_long = (pr.pages or 0) > 1 or (pr.pages is None and pr.word_count > 850)
    too_short = pr.word_count < 250
    a_bit_short = not too_short and pr.word_count < 380
    fmt += 5 if too_long or too_short else 9 if a_bit_short else 15
    if "education" not in pr.sections:
        fixes.append((2, "Add an Education section with school, degree, major, expected graduation date and GPA."))
    if not pr.contact["email"]:
        fixes.append((1, "No email address found. Put your contact info at the top."))
    if not pr.contact["linkedin"]:
        fixes.append((6, "Add your LinkedIn URL to the header."))
    if not (pr.contact["github"] or pr.contact["portfolio"]):
        fixes.append((5, "Add a GitHub or portfolio link. Technical recruiters click it."))
    if too_long and level not in {"Graduate student"}:
        fixes.append((3, "Keep it to one page. For internships, one tight page beats two."))
    if too_short:
        fixes.append((2, f"Your resume is sparse ({pr.word_count} words). Aim for a full page (~400–650 words)."))
    elif a_bit_short:
        fixes.append((5, f"There's room for more detail ({pr.word_count} words). A full page is ~400–650 words: "
                         "add a project, coursework, or another bullet on your biggest result."))
    if fmt >= 90:
        strengths.append((6, "Well-structured: all the key sections and contact links are present."))

    subscores = [
        SubScore(key="impact", label="Impact & writing", score=round(impact), weight=0.25, detail=impact_detail),
        SubScore(key="experience", label="Experience", score=round(experience), weight=0.25, detail=exp_detail),
        SubScore(key="skills", label="Skills", score=round(skills_score), weight=0.20, detail=skills_detail),
        SubScore(key="academics", label="Academics", score=round(academics), weight=0.10, detail=acad_detail),
        SubScore(key="format", label="Format & completeness", score=round(fmt), weight=0.20,
                 detail=f"{len([s for s in pr.sections if s != 'header'])} sections, "
                        f"{pr.pages or '?'} page(s), {pr.word_count} words"),
    ]
    for s in subscores:
        s.score = max(0, min(100, s.score))
    overall = round(sum(s.score * s.weight for s in subscores))

    grouped: dict[str, list[str]] = {}
    for name in sorted(skills, key=lambda n: (-pr.skills.get(n, 0), n.lower())):
        grouped.setdefault(skill_group(name), []).append(name)

    weak_bullets = sorted(
        (b for b in bullets if b.issues()), key=lambda b: (-len(b.issues()), -b.words)
    )[:6]

    return ResumeReport(
        overall=overall,
        grade=_grade(overall),
        subscores=subscores,
        strengths=[m for _, m in sorted(strengths)][:6],
        improvements=[m for _, m in sorted(fixes)][:12],
        skills=grouped,
        skill_count=len(skills),
        sections=[s for s in pr.sections if s != "header"],
        contact=pr.contact,
        stats={
            "bullets": nb,
            "quantified_bullets": sum(b.quantified for b in bullets),
            "action_bullets": sum(b.action for b in bullets),
            "word_count": pr.word_count,
            "pages": pr.pages,
            "roles": pr.roles,
            "internships": pr.internships,
            "projects": pr.projects,
        },
        detected={
            "gpa": pr.gpa,
            "grad_year": pr.grad_year,
            "degree": pr.degree,
            "major": pr.major,
            "level": level,
        },
        weak_bullets=[BulletFeedback(text=b.text, issues=b.issues()) for b in weak_bullets],
    )
