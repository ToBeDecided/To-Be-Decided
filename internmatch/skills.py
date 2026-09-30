"""Skill taxonomy, skill extraction, and role "signatures" used for matching.

Focused on pre-law, business and humanities internships. Everything here is
plain data plus a few regex helpers, so the matcher stays deterministic and
runs offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Skill:
    name: str
    group: str
    aliases: tuple[str, ...] = ()
    # Short/ambiguous names ("R", "Excel") are only matched case-sensitively.
    case_sensitive: tuple[str, ...] = ()


def _s(name: str, group: str, *aliases: str, cs: tuple[str, ...] = (), name_alias: bool = True) -> Skill:
    # Case-sensitive skills and ambiguous names ("Operations", "Campaigns") can opt out of matching their own name.
    names = ((name,) if name_alias and not cs else ()) + aliases
    return Skill(name, group, tuple(a.lower() for a in names), cs)


SKILLS: tuple[Skill, ...] = (
    # Legal
    _s("Legal Research", "legal", "case law research", "case research", "statutory research", "legal analysis"),
    _s("Legal Writing", "legal", "legal memo", "legal memos", "legal memoranda", "legal briefs", "brief writing",
       "appellate brief", "drafted motions", "legal drafting"),
    _s("Westlaw", "legal"),
    _s("LexisNexis", "legal", "lexis nexis", "lexis"),
    _s("Bluebook", "legal", "bluebook citation", "legal citation"),
    _s("Contract Review", "legal", "contract drafting", "contracts review", "reviewing contracts", "drafting contracts",
       "contract management", "contract analysis"),
    _s("Litigation Support", "legal", "litigation", "document review", "e-discovery", "ediscovery", "relativity",
       "trial preparation", "trial prep"),
    _s("Case Management", "legal", "case files", "clio", "docketing", "case intake", "client intake"),
    _s("Legal Office Experience", "legal", "law firm", "law office", "courthouse", "district attorney",
       "public defender", "prosecutor's office", "legal aid", "judicial chambers", "judicial intern",
       "court of appeals", "superior court", "district court", "attorney general", "legal department",
       name_alias=False),
    _s("Law Coursework", "legal", "constitutional law", "criminal law", "civil procedure", "torts", "legal studies",
       "business law", "international law", "civil rights law", "criminal justice", "jurisprudence",
       "philosophy of law", "law and society", name_alias=False),
    _s("Paralegal", "legal", "legal assistant", "notary", "notary public", "paralegal certificate"),
    _s("Compliance", "legal", "regulatory compliance", "kyc", "aml", "anti-money laundering"),
    _s("Intellectual Property", "legal", "patent", "patents", "trademark", "trademarks", "copyright law"),
    _s("Immigration", "legal", "immigration law", "asylum", "daca", "refugee"),
    _s("Mock Trial", "legal"),
    _s("Moot Court", "legal"),
    _s("Debate", "legal", "speech and debate", "parliamentary debate", "policy debate", "lincoln-douglas",
       "debate team", "forensics team"),
    _s("LSAT Prep", "legal", "lsat", name_alias=False),
    # Government & policy
    _s("Policy Analysis", "policy", "policy research", "policy memo", "policy memos", "policy brief",
       "policy briefs", "public policy", "policy analyst"),
    _s("Legislative Research", "policy", "legislative", "legislation", "bill tracking", "bill analysis",
       "legislative analysis"),
    _s("Constituent Services", "policy", "constituent", "constituents", "constituent casework",
       "constituent correspondence"),
    _s("Campaigns", "policy", "political campaign", "election campaign", "congressional campaign",
       "canvassing", "canvassed", "phone banking", "phonebanking", "voter registration", "voter outreach",
       "get out the vote", "gotv", "field organizer", "field organizing", name_alias=False),
    _s("Government Relations", "policy", "government affairs", "lobbying", "lobbied", "legislative affairs"),
    _s("Model UN", "policy", "model united nations"),
    _s("International Relations", "policy", "foreign policy", "diplomacy", "international affairs",
       "global affairs", "national security"),
    _s("Political Science", "policy", "political theory", "comparative politics", "american politics",
       "government major"),
    _s("Grassroots Organizing", "policy", "community organizing", "grassroots", "coalition building"),
    _s("Economics", "policy", "microeconomics", "macroeconomics", "econometrics", "economic analysis",
       "economic research"),
    _s("Public Speaking", "general", "presentations", "presented to", "speeches", "toastmasters",
       "public presentations"),
    # Finance & accounting
    _s("Financial Modeling", "finance", "financial models", "financial model", "three-statement model",
       "3-statement model", "lbo model", "m&a model", "operating model"),
    _s("Valuation", "finance", "dcf", "discounted cash flow", "comparable company analysis",
       "comparable companies", "precedent transactions", "trading comps", "lbo"),
    _s("Accounting", "finance", "gaap", "financial accounting", "managerial accounting", "bookkeeping",
       "accounts payable", "accounts receivable", "reconciliation", "reconciliations", "journal entries",
       "general ledger", "intermediate accounting"),
    _s("QuickBooks", "finance", "xero", "netsuite"),
    _s("Auditing", "finance", "audit", "internal audit", "sox"),
    _s("Tax", "finance", "taxation", "tax preparation", "tax returns", "vita"),
    _s("Financial Analysis", "finance", "financial statements", "financial statement analysis", "ratio analysis",
       "budgeting", "forecasting", "variance analysis", "fp&a", "financial reporting"),
    _s("Equity Research", "finance", "stock pitch", "stock pitches", "investment thesis", "investment club",
       "investment fund", "student-managed fund", "student managed investment fund", "portfolio management",
       "asset management"),
    _s("Investment Banking", "finance", "m&a", "mergers and acquisitions", "capital markets", "pitch book",
       "pitch books", "pitchbooks", "deal team"),
    _s("Bloomberg Terminal", "finance", "bloomberg market concepts", "bloomberg certification", "bmc certification"),
    _s("Capital IQ", "finance", "capiq", "s&p capital iq"),
    _s("FactSet", "finance"),
    _s("PitchBook", "finance", "pitchbook data"),
    _s("CFA", "finance", "cfa level", "chartered financial analyst"),
    _s("Securities Licenses", "finance", "sie exam", "securities industry essentials", "series 7", "series 63",
       "series 65", name_alias=False),
    _s("Excel", "tools", "microsoft excel", "ms excel", "vlookup", "xlookup", "pivot table", "pivot tables",
       "spreadsheets", "spreadsheet", "google sheets", cs=("Excel", "EXCEL")),
    _s("VBA", "tools", "excel macros"),
    _s("Real Estate", "finance", "property management", "argus", "commercial real estate"),
    _s("Risk Analysis", "finance", "risk management", "credit analysis", "underwriting", "credit risk"),
    # Consulting & business
    _s("Market Research", "business", "market analysis", "industry research", "industry analysis",
       "competitive analysis", "competitor analysis", "market sizing", "customer research"),
    _s("Business Strategy", "business", "strategic planning", "go-to-market", "corporate strategy",
       "growth strategy", "strategy consulting", "business plan"),
    _s("Case Competitions", "business", "case competition", "case interview", "consulting club",
       "business competition", "pitch competition", "case study competition", name_alias=False),
    _s("Consulting", "business", "consultant", "client engagement", "pro bono consulting", "consulting group"),
    _s("Project Management", "business", "project manager", "managed projects", "asana", "trello", "monday.com",
       "gantt", "timeline management"),
    _s("Operations", "business", "process improvement", "supply chain", "logistics", "inventory management",
       "six sigma", name_alias=False),
    _s("Data Analysis", "business", "data analytics", "analyzed data", "dashboards", "dashboard",
       "quantitative analysis"),
    _s("SQL", "tools"),
    _s("Tableau", "tools", "power bi", "looker"),
    _s("PowerPoint", "tools", "microsoft powerpoint", "slide decks", "slide deck", "keynote", "google slides"),
    _s("Salesforce", "business", "crm", "zoho"),
    _s("Sales", "business", "business development", "lead generation", "cold calling", "prospecting",
       "account management", "retail sales"),
    _s("Customer Service", "business", "client service", "client services", "customer support", "front desk",
       "guest services", "hospitality"),
    _s("Human Resources", "business", "recruiting", "recruitment", "talent acquisition", "onboarding", "hr"),
    _s("Entrepreneurship", "business", "startup", "start-up", "small business", "co-founded", "founded a"),
    _s("Negotiation", "business", "negotiated", "negotiating", "negotiations"),
    # Marketing & communications
    _s("Social Media", "marketing", "instagram", "tiktok", "twitter", "facebook", "social media management",
       "hootsuite", "sprout social", "linkedin content", "social media strategy"),
    _s("Content Creation", "marketing", "content creator", "content strategy", "content calendar",
       "created content", "content marketing", "content production"),
    _s("Copywriting", "marketing", "copywriter", "marketing copy", "ad copy", "product copy"),
    _s("SEO", "marketing", "search engine optimization", "google ads"),
    _s("Email Marketing", "marketing", "mailchimp", "constant contact", "newsletter", "newsletters", "klaviyo"),
    _s("Google Analytics", "marketing", "ga4", "marketing analytics", "web analytics"),
    _s("HubSpot", "marketing"),
    _s("Canva", "marketing"),
    _s("Adobe Creative Suite", "marketing", "adobe creative cloud", "photoshop", "illustrator", "indesign",
       "lightroom", "premiere", "premiere pro", "after effects", "adobe"),
    _s("Video Editing", "marketing", "final cut", "final cut pro", "imovie", "davinci resolve", "videography",
       "video production"),
    _s("Photography", "marketing", "photographer", "photojournalism"),
    _s("Public Relations", "marketing", "press releases", "press release", "media relations", "media kit",
       "press kit", "media pitching", "pr"),
    _s("Brand Marketing", "marketing", "branding", "brand strategy", "brand management", "brand ambassador",
       "brand partnerships"),
    _s("Event Planning", "marketing", "event management", "event coordination", "organized events",
       "planned events", "conference planning", "event logistics"),
    _s("Marketing", "marketing", "digital marketing", "marketing strategy", "marketing campaigns",
       "marketing campaign", "advertising", "ad campaigns", "influencer marketing"),
    _s("Graphic Design", "marketing", "figma", "layout design", "visual design"),
    # Writing, media & publishing
    _s("Writing", "writing", "writer", "wrote", "authored", "written communication", "writing skills"),
    _s("Editing", "writing", "editor", "edited", "editor-in-chief", "managing editor", "section editor",
       "associate editor"),
    _s("Copyediting", "writing", "copy editing", "copy editor", "copyedited", "proofreading", "proofread",
       "proofreader"),
    _s("Journalism", "writing", "journalist", "reporter", "news reporting", "newsroom", "student newspaper",
       "student paper", "staff writer", "correspondent", "news writing", "beat reporter"),
    _s("Style Guides", "writing", "ap style", "associated press style", "chicago manual of style",
       "chicago style", "mla style", "apa style", name_alias=False),
    _s("Fact-Checking", "writing", "fact checking", "fact-checker", "fact checker", "fact-checked"),
    _s("Interviewing", "writing", "conducted interviews", "interviewed", "oral history", "oral histories",
       "interviewing sources"),
    _s("Publishing", "writing", "publisher", "manuscript", "manuscripts", "literary agency", "literary magazine",
       "literary journal", "slush pile", "book publishing"),
    _s("Creative Writing", "writing", "fiction", "poetry", "short stories", "screenwriting", "playwriting",
       "creative nonfiction", "memoir"),
    _s("Publications", "writing", "published", "publication", "co-authored", "coauthored", "journal article",
       "op-ed", "op-eds", "published articles", name_alias=False),
    _s("Blogging", "writing", "blog", "blogger", "wordpress", "substack", "squarespace", "wix"),
    _s("Podcasting", "writing", "podcast", "audio production", "audacity", "college radio", "radio show"),
    _s("Broadcasting", "writing", "broadcast", "on-air", "news anchor", "tv production", "television production"),
    _s("Translation", "writing", "translator", "translated", "interpreting", "interpreter"),
    # Arts & culture
    _s("Art History", "arts", "art historian", "visual culture", "art historical"),
    _s("Curatorial Research", "arts", "curatorial", "curator", "curating", "curated", "exhibition", "exhibitions",
       "gallery", "galleries", name_alias=False),
    _s("Collections Management", "arts", "cataloging", "cataloguing", "catalogued", "cataloged",
       "object handling", "registrar", "provenance", "pastperfect", "the museum system", "condition reports",
       "collections database"),
    _s("Archival Research", "arts", "archives", "archival", "archivist", "special collections",
       "primary source research", "finding aids", "digitization", "digitized", "metadata"),
    _s("Museum Education", "arts", "docent", "tour guide", "gallery guide", "visitor services",
       "public programs", "museum"),
    _s("Arts Administration", "arts", "arts management", "box office", "stage management", "stage manager",
       "house manager", "production assistant"),
    _s("Performing Arts", "arts", "theatre", "theater", "orchestra", "choir", "a cappella", "dance company",
       "film production", "music performance", name_alias=False),
    _s("Library", "arts", "librarian", "library science", "reference desk", "circulation desk",
       "library assistant"),
    _s("Historical Research", "arts", "historiography", "historic preservation", "heritage", "historical analysis",
       "public history"),
    # Education & research
    _s("Tutoring", "education", "tutor", "tutored", "writing center", "peer tutor", "peer mentor", "mentoring",
       "mentored"),
    _s("Teaching", "education", "teacher", "teaching assistant", "classroom", "lesson planning", "lesson plans",
       "curriculum", "curriculum development", "substitute teacher", "instructor"),
    _s("Youth Work", "education", "camp counselor", "youth programs", "after-school", "afterschool", "childcare",
       "youth mentor", "coaching"),
    _s("Academic Research", "research", "research assistant", "undergraduate research", "independent research",
       "honors thesis", "senior thesis", "thesis", "research fellow", "literature review", "annotated bibliography"),
    _s("Research", "research", "researched", "researching", "research project", "research skills"),
    _s("Qualitative Research", "research", "qualitative", "ethnography", "ethnographic", "focus groups",
       "nvivo", "atlas.ti", "coding interviews"),
    _s("Survey Research", "research", "survey design", "surveys", "questionnaire", "qualtrics", "surveymonkey"),
    _s("Statistics", "research", "statistical analysis", "statistical", "regression", "quantitative research"),
    _s("SPSS", "research"),
    _s("Stata", "research"),
    _s("R", "research", "rstudio", cs=("R",)),
    _s("Python", "tools"),
    _s("Citation Management", "research", "zotero", "endnote", "mendeley"),
    _s("Conference Presentations", "research", "conference presentation", "poster presentation", "symposium",
       "presented research", "presented at", name_alias=False),
    _s("GIS", "research", "arcgis", "qgis", "mapping software"),
    # Nonprofit & advocacy
    _s("Fundraising", "nonprofit", "fundraiser", "fundraisers", "fundraised", "donor relations",
       "donor stewardship", "annual fund", "capital campaign", "donors", "philanthropy"),
    _s("Grant Writing", "nonprofit", "grant proposals", "grant proposal", "grant applications", "grants"),
    _s("Volunteer Coordination", "nonprofit", "volunteer management", "coordinated volunteers",
       "recruited volunteers", "managed volunteers", "volunteer coordinator"),
    _s("Volunteering", "nonprofit", "volunteer", "volunteered", "community service", "americorps",
       "habitat for humanity", "service learning"),
    _s("Community Outreach", "nonprofit", "outreach", "community engagement", "community partnerships",
       "community relations"),
    _s("Advocacy", "nonprofit", "advocate", "advocated", "social justice", "human rights", "civil rights",
       "racial justice", "reproductive rights", "immigrant rights", "criminal justice reform"),
    _s("Program Coordination", "nonprofit", "program coordinator", "program management", "program evaluation",
       "program assistant"),
    _s("Social Services", "nonprofit", "social work", "crisis counseling", "crisis hotline", "case worker",
       "caseworker", "counseling"),
    _s("Donor Databases", "nonprofit", "raiser's edge", "raisers edge", "blackbaud", "donorperfect",
       "salesforce npsp"),
    # Office tools & general
    _s("Microsoft Office", "tools", "ms office", "office suite", "microsoft 365", "office 365", "microsoft word",
       "ms word", "outlook", "microsoft outlook"),
    _s("Google Workspace", "tools", "g suite", "gsuite", "google docs", "google drive"),
    _s("Data Entry", "tools", "database management", "records management", "recordkeeping", "record keeping",
       "filing"),
    _s("Administrative Support", "tools", "scheduling", "calendar management", "administrative", "clerical",
       "office management", "receptionist", "reception"),
    _s("Communication", "general", "communication skills", "verbal communication", "interpersonal",
       "written and verbal"),
    _s("HTML/CSS", "tools", "html", "css"),
)

# Foreign languages are detected separately (only on lines that talk about language ability), so that
# "French Revolution" or "Latin honors" don't count.
LANGUAGES = (
    "Spanish", "French", "Mandarin", "Chinese", "Cantonese", "Arabic", "German", "Portuguese", "Japanese",
    "Korean", "Italian", "Russian", "Hindi", "Urdu", "Hebrew", "Latin", "Ancient Greek", "Greek", "Vietnamese",
    "Tagalog", "Farsi", "Persian", "Turkish", "Swahili", "Polish", "Haitian Creole", "Bengali", "Punjabi",
    "Gujarati", "Tamil", "Telugu", "Ukrainian", "Dutch", "Swedish", "Yiddish", "Amharic", "Somali",
    "American Sign Language", "ASL",
)
_LANGUAGE_CONTEXT = re.compile(
    r"language|fluen|proficien|native|bilingual|trilingual|multilingual|conversational|intermediate|advanced|"
    r"working knowledge|elementary|\bbasic\b|speak|spoken|reading knowledge|heritage speaker|\([^)]*\)|"
    r"preferred|required|a plus|desired",
    re.I,
)
_LANGUAGE_RX = re.compile(r"\b(" + "|".join(re.escape(lang) for lang in LANGUAGES) + r")\b")
_LANGUAGE_CANON = {"Chinese": "Mandarin", "Persian": "Farsi", "ASL": "American Sign Language"}
LANGUAGE_NAMES = frozenset(_LANGUAGE_CANON.get(lang, lang) for lang in LANGUAGES)

SKILL_BY_NAME: dict[str, Skill] = {s.name.lower(): s for s in SKILLS}


def canonical(name: str) -> str | None:
    """Map a user-typed skill (or alias, or language) to its canonical display name."""
    key = name.strip().lower()
    if not key:
        return None
    if key in SKILL_BY_NAME:
        return SKILL_BY_NAME[key].name
    for skill in SKILLS:
        if key in skill.aliases or name.strip() in skill.case_sensitive:
            return skill.name
    for lang in LANGUAGES:
        if key == lang.lower():
            return _LANGUAGE_CANON.get(lang, lang)
    return None


# Tokens keep internal "&", "'", "." and "-" so "m&a", "raiser's", "monday.com" and "fact-checking" stay whole.
# A second pass splits on hyphens so "policy-focused" still counts as "policy".
_TOKEN_RX = re.compile(r"[a-z0-9]+(?:['&.-][a-z0-9]+)*")
_TOKEN_NOHYPHEN_RX = re.compile(r"[a-z0-9]+(?:['&.][a-z0-9]+)*")


@lru_cache(maxsize=1)
def _alias_index() -> tuple[dict[tuple[str, ...], str], int]:
    index: dict[tuple[str, ...], str] = {}
    for skill in SKILLS:
        for alias in skill.aliases:
            key = tuple(_TOKEN_RX.findall(alias))
            if key:
                index.setdefault(key, skill.name)
    return index, max(len(k) for k in index)


@lru_cache(maxsize=1)
def _case_sensitive() -> list[tuple[str, re.Pattern[str]]]:
    # Bare "R" / "Excel": must stand alone and not be an initial ("John R. Smith") or part of "R&D".
    return [
        (skill.name, re.compile("|".join(rf"(?<![\w+#./&-]){re.escape(a)}(?![\w+#&-])(?!\.\w)(?!\.\s+[A-Z][a-z])"
                                         for a in skill.case_sensitive)))
        for skill in SKILLS if skill.case_sensitive
    ]


def extract_languages(text: str) -> dict[str, int]:
    """Foreign languages mentioned on lines that describe language ability."""
    found: dict[str, int] = {}
    for line in text.splitlines():
        if not _LANGUAGE_CONTEXT.search(line):
            continue
        for m in _LANGUAGE_RX.finditer(line):
            name = _LANGUAGE_CANON.get(m.group(1), m.group(1))
            # "Latin" and "Greek" also show up as "Latin honors", "Latin America", "Greek life".
            after = line[m.end():m.end() + 12].lower()
            if name in {"Latin", "Greek"} and re.match(r"\s*(honors|america|life|american|studies)", after):
                continue
            found[name] = found.get(name, 0) + 1
    return found


def extract_skills(text: str) -> dict[str, int]:
    """Return {canonical skill name: mention count} found in ``text`` (languages included).

    Matching is a dictionary lookup over word n-grams, so it stays fast on long job descriptions.
    """
    if not text:
        return {}
    lower = text.lower().replace("\u2019", "'")
    index, max_n = _alias_index()
    hits: set[tuple[str, int]] = set()  # (skill, character offset): both tokenizations can find the same mention
    for rx in (_TOKEN_RX, _TOKEN_NOHYPHEN_RX):
        tokens = [(m.group(0), m.start()) for m in rx.finditer(lower)]
        words = [t for t, _ in tokens]
        for i, (_, pos) in enumerate(tokens):
            for n in range(1, max_n + 1):
                skill = index.get(tuple(words[i:i + n]))
                if skill:
                    hits.add((skill, pos))
    found: dict[str, int] = {}
    for skill, _ in hits:
        found[skill] = found.get(skill, 0) + 1
    for name, rx in _case_sensitive():
        n = len(rx.findall(text))
        if n:
            found[name] = found.get(name, 0) + n
    for lang, n in extract_languages(text).items():
        found[lang] = found.get(lang, 0) + n
    return found


def skill_group(name: str) -> str:
    if name in LANGUAGE_NAMES:
        return "language"
    skill = SKILL_BY_NAME.get(name.lower())
    return skill.group if skill else "other"


# ---------------------------------------------------------------------------
# Categories and tracks
# ---------------------------------------------------------------------------

CATEGORIES = (
    "Legal",
    "Government & Policy",
    "Finance & Accounting",
    "Consulting & Business",
    "Marketing & Communications",
    "Media & Writing",
    "Arts & Culture",
    "Education & Research",
    "Nonprofit & Advocacy",
)
TRACKS: dict[str, tuple[str, ...]] = {
    "Pre-Law": ("Legal", "Government & Policy", "Nonprofit & Advocacy"),
    "Business": ("Finance & Accounting", "Consulting & Business", "Marketing & Communications"),
    "Humanities": ("Media & Writing", "Arts & Culture", "Education & Research", "Nonprofit & Advocacy"),
}


def categories_for(tracks: list[str] | tuple[str, ...], categories: list[str] | tuple[str, ...] = ()) -> set[str]:
    """Categories selected by some tracks plus explicit categories. Nothing selected means all of them."""
    out = {c for c in categories if c in CATEGORIES}
    for t in tracks:
        out.update(TRACKS.get(t, ()))
    return out or set(CATEGORIES)


# Weighted skill profile for each category.
CATEGORY_SIGNATURES: dict[str, dict[str, float]] = {
    "Legal": {
        "Legal Research": 3, "Legal Writing": 3, "Westlaw": 2, "LexisNexis": 2, "Bluebook": 1, "Law Coursework": 2,
        "Legal Office Experience": 2, "Mock Trial": 2, "Moot Court": 2, "Debate": 1.5, "Contract Review": 1.5,
        "Litigation Support": 1.5, "Paralegal": 1.5, "Compliance": 1, "Writing": 2, "Research": 2,
        "Microsoft Office": 1, "Case Management": 1, "LSAT Prep": 1, "Public Speaking": 1, "Advocacy": 1,
    },
    "Government & Policy": {
        "Policy Analysis": 3, "Legislative Research": 2, "Research": 2, "Writing": 2, "Political Science": 2,
        "Campaigns": 2, "Constituent Services": 2, "Government Relations": 1.5, "Model UN": 1.5,
        "International Relations": 1.5, "Economics": 1, "Grassroots Organizing": 1, "Public Speaking": 1,
        "Microsoft Office": 1, "Debate": 1, "Advocacy": 1, "Survey Research": 0.5, "Stata": 0.5,
    },
    "Finance & Accounting": {
        "Excel": 3, "Financial Modeling": 3, "Valuation": 2, "Accounting": 3, "Financial Analysis": 2,
        "Equity Research": 2, "Investment Banking": 1.5, "Bloomberg Terminal": 1, "Capital IQ": 1, "FactSet": 0.5,
        "PitchBook": 0.5, "PowerPoint": 1.5, "QuickBooks": 1, "Auditing": 1, "Tax": 1, "Economics": 1.5, "CFA": 1,
        "Securities Licenses": 0.5, "VBA": 0.5, "Risk Analysis": 0.5, "Data Analysis": 1,
    },
    "Consulting & Business": {
        "Market Research": 2.5, "Business Strategy": 2, "Case Competitions": 2, "Consulting": 2, "Data Analysis": 2,
        "Excel": 2.5, "PowerPoint": 2, "Project Management": 2, "Operations": 1.5, "Public Speaking": 1, "SQL": 1,
        "Tableau": 1, "Sales": 1, "Human Resources": 1, "Entrepreneurship": 1, "Salesforce": 0.5,
        "Negotiation": 0.5, "Economics": 1,
    },
    "Marketing & Communications": {
        "Social Media": 3, "Content Creation": 2.5, "Marketing": 2.5, "Copywriting": 2, "Canva": 1.5,
        "Adobe Creative Suite": 2, "Public Relations": 2, "Brand Marketing": 1.5, "Google Analytics": 1.5, "SEO": 1,
        "Email Marketing": 1, "HubSpot": 1, "Event Planning": 1, "Video Editing": 1, "Photography": 0.5,
        "Graphic Design": 1, "Writing": 1.5, "Market Research": 1,
    },
    "Media & Writing": {
        "Writing": 3, "Editing": 2.5, "Journalism": 3, "Copyediting": 2, "Style Guides": 1.5, "Fact-Checking": 1.5,
        "Interviewing": 1.5, "Publishing": 2, "Creative Writing": 1.5, "Blogging": 1, "Podcasting": 1,
        "Broadcasting": 1, "Social Media": 1, "Research": 1.5, "Publications": 1.5, "Adobe Creative Suite": 0.5,
        "Translation": 0.5,
    },
    "Arts & Culture": {
        "Art History": 3, "Curatorial Research": 2.5, "Collections Management": 2, "Archival Research": 2.5,
        "Museum Education": 2, "Arts Administration": 1.5, "Performing Arts": 1, "Library": 1.5,
        "Historical Research": 2, "Research": 2, "Writing": 1.5, "Event Planning": 0.5, "Photography": 0.5,
        "Adobe Creative Suite": 0.5, "Translation": 0.5,
    },
    "Education & Research": {
        "Tutoring": 2.5, "Teaching": 2.5, "Youth Work": 1.5, "Academic Research": 3, "Research": 2,
        "Qualitative Research": 1.5, "Survey Research": 1.5, "Statistics": 1.5, "SPSS": 1, "Stata": 1, "R": 0.5,
        "Citation Management": 1, "Writing": 2, "Public Speaking": 1, "Publications": 1,
        "Conference Presentations": 1,
    },
    "Nonprofit & Advocacy": {
        "Fundraising": 2.5, "Grant Writing": 2.5, "Volunteering": 1.5, "Volunteer Coordination": 2,
        "Community Outreach": 2, "Advocacy": 2.5, "Program Coordination": 2, "Event Planning": 1.5,
        "Social Services": 1, "Donor Databases": 1, "Writing": 1.5, "Social Media": 1, "Microsoft Office": 1,
        "Grassroots Organizing": 1,
    },
}


# ---------------------------------------------------------------------------
# Classifying postings
# ---------------------------------------------------------------------------

# Checked in order; the first match wins. "Strong" rules are specific enough to override off-focus words
# ("Healthcare Policy Intern" is a policy role); weak ones only apply when nothing off-focus matched.
_STRONG_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    (r"paralegal|\blegal\b|law clerk|law firm|litigation|attorney|judicial|\bcourts?\b|district attorney|"
     r"public defender|prosecutor|\blaw\b|compliance|counsel\b", "Legal"),
    (r"policy|legislat|government|public affairs|congress|senat|house of representatives|state assembly|"
     r"political|civic|elections?\b|diplomac|foreign service|embassy|consulate|state department|mayor|"
     r"city council|governor|white house|campaign (intern|fellow|organizer)|field organiz", "Government & Policy"),
    (r"financ|accounting|accountant|\baudit|\btax\b|treasury|investment|banking|equity research|private equity|"
     r"wealth management|asset management|capital markets|credit|underwrit|insurance|actuar|fp&a|\bm&a\b|"
     r"hedge fund|summer analyst|bookkeep|payroll|accounts? (payable|receivable)|\bbilling\b", "Finance & Accounting"),
    (r"marketing|\bbrand|advertis|communications|public relations|\bpr\b|social media|\bcontent\b|"
     r"media relations|\bevents?\b|graphic design|\bgrowth\b|e-?commerce|digital media|publicity|influencer",
     "Marketing & Communications"),
    (r"human resources|\bhr\b|recruit(ing|er|ment)|talent acquisition|people (ops|operations)|category manag|"
     r"merchandis|\bbuyer\b|\bsales\b|account (executive|manager|management)|supply chain|procurement",
     "Consulting & Business"),
    (r"editorial|\beditor|journalis|reporter|newsroom|\bwriter|\bwriting|copy ?edit|proofread|fact[- ]?check|"
     r"publishing|literary|magazine|newspaper|podcast|\bradio\b|broadcast|documentary|\bnews\b|\bbooks?\b",
     "Media & Writing"),
    (r"museum|curator|curatorial|collections|archiv|gallery|exhibit|librar|historic|heritage|preservation|"
     r"theat(er|re)|\bmusic|\bdance|\bopera\b|symphony|orchestra|\bfilm|\barts?\b|cultural", "Arts & Culture"),
    (r"non-?profit|fundrais|\bgrants?\b|donor|philanthrop|advoca|outreach|social impact|volunteer|human rights|"
     r"\bjustice\b|development (intern|associate|assistant)|community (engagement|organiz)", "Nonprofit & Advocacy"),
    (r"teach|tutor|education|classroom|curriculum|\bschool|instruct|literacy|youth|camp counselor",
     "Education & Research"),
)
_OFF_FOCUS = re.compile(
    r"software|developer|engineer|devops|machine learning|\bml\b|\bai\b|data scien|cyber|\bit\b|"
    r"information technology|network|hardware|electrical|mechanical|chemical|biolog|chemist|laborator|\blab\b|"
    r"clinical|nurs|medical|pharma|physician|dental|veterinar|health ?care|scientist|geolog|physics|manufactur|"
    r"technician|mechanic|construction|welding|electrician|plumb|hvac|\bdriver|warehouse|\bcook\b|chef|culinary|"
    r"pilot|aviation|security guard|environmental|ecolog|wildlife|forestry|fisheries|biomedical|\bbio|nuclear|"
    r"\br&d\b|research and development|supplier quality|quality (engineer|assurance|control)|lawn|landscap|"
    r"automotive|plant operations|field service|maintenance|patholog|anatom|histolog|specimen|diagnostic|"
    r"hygien|\benv\b|\bconst\b|\bpvd\b|chips?\b|voltage|firmware|robotic|\bsafety\b|\bes&h\b|\behs\b|"
    r"\belec\b|\beng\b|\baero|industrializ|turbine|propulsion",
    re.I,
)
# Phrases in a job description that mark a technical, scientific or medical role.
_OFF_FOCUS_DESCRIPTION = re.compile(
    r"degree in (engineering|computer science|biology|chemistry|physics|environmental science|nursing|geology)|"
    r"(engineering|computer science|biology|chemistry|physics|nursing) (degree|major|students?)|"
    r"(mechanical|electrical|civil|chemical|industrial|software|process) engineering|environmental science|"
    r"laboratory|clinical|patient care|programming languages|software development|construction management|"
    r"industrial hygiene|semiconductor|manufacturing process|cad software|autocad|solidworks|matlab|"
    r"python|java\b|c\+\+|machine learning|data science|nuclear|reactor|power plant",
    re.I,
)
# Employers whose weakly-titled roles ("Intern, Year Round") are almost always technical.
_OFF_FOCUS_EMPLOYER = re.compile(r"laborator|engineering|hospital|health system|medical center|pharmaceutical|"
                                 r"semiconductor|aerospace", re.I)
_WEAK_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    (r"research|fellow|scholar|think tank", "Education & Research"),
    (r"consult|strategy|business|operations|\bsales\b|\baccount|human resources|\bhr\b|recruit|talent|"
     r"supply chain|logistics|procurement|real estate|entrepreneur|project manag|management|analyst|admin|"
     r"\boffice\b|coordinator|customer success|partnerships|retail|merchandis|buying|venture|analytics",
     "Consulting & Business"),
)
_STRONG_COMPILED = tuple((re.compile(p, re.I), c) for p, c in _STRONG_CATEGORY_RULES)
_WEAK_COMPILED = tuple((re.compile(p, re.I), c) for p, c in _WEAK_CATEGORY_RULES)


def classify(title: str) -> tuple[str | None, str]:
    """(category, confidence) for a job title. Confidence is "strong", "off" (off-focus), "weak" or "none"."""
    if not title:
        return None, "none"
    for rx, cat in _STRONG_COMPILED:
        if rx.search(title):
            return cat, "strong"
    if _OFF_FOCUS.search(title):
        return "Other", "off"
    for rx, cat in _WEAK_COMPILED:
        if rx.search(title):
            return cat, "weak"
    return None, "none"


def off_focus_employer(company: str) -> bool:
    return bool(company and _OFF_FOCUS_EMPLOYER.search(company))


def category_weights(skills: dict[str, int] | set[str], generic_weight: float = 1.0) -> dict[str, float]:
    """How strongly a set of skills points at each category (sum of signature weights).

    ``generic_weight`` scales skills nearly every posting mentions (Word, "research", "writing"), so that a corporate
    description asking for research and writing isn't mistaken for an education or media role.
    """
    return {
        cat: sum(w * (generic_weight if skill in GENERIC_SKILLS else 1.0) for skill, w in sig.items() if skill in skills)
        for cat, sig in CATEGORY_SIGNATURES.items()
    }


# Skills nearly every posting asks for. They count for little when judging fit.
GENERIC_SKILLS = frozenset({
    "Microsoft Office", "Google Workspace", "Excel", "PowerPoint", "Writing", "Research", "Communication",
    "Public Speaking", "Customer Service", "Data Entry", "Administrative Support", "Project Management",
    "Data Analysis",
})
GENERIC_DISCOUNT = 0.3  # how much a generic skill counts relative to a specific one


def classify_description(
    skills: dict[str, int] | set[str], min_weight: float = 3, generic_weight: float = 1.0
) -> tuple[str | None, float]:
    """Best-matching category for a job description's skills: (category or None if unclear, weight)."""
    scores = category_weights(skills, generic_weight)
    best = max(scores, key=scores.get)
    return (best, scores[best]) if scores[best] >= min_weight else (None, scores[best])


def off_focus_description(text: str) -> int:
    """How many distinct technical/scientific/medical phrases a description contains."""
    return len({m.group(0).lower() for m in _OFF_FOCUS_DESCRIPTION.finditer(text[:8000])})


def classify_title(title: str) -> str | None:
    """Our category for a job title, "Other" for off-focus roles (tech, science, medicine...), or None if unclear."""
    return classify(title)[0]


# ---------------------------------------------------------------------------
# Title requirement rules
# ---------------------------------------------------------------------------

# Each rule adds "requirement groups": a group is satisfied if the candidate has ANY skill in it.
# "@language" means any foreign language.
TITLE_RULES: tuple[tuple[str, str, tuple[tuple[str, ...], ...]], ...] = (
    (r"paralegal|\blegal\b|law clerk|law firm|litigation|attorney|judicial|\bcourts?\b|district attorney|"
     r"public defender|prosecutor|\blaw\b|counsel\b", "Legal",
     (("Legal Research", "Research"), ("Legal Writing", "Writing"),
      ("Westlaw", "LexisNexis", "Law Coursework", "Legal Office Experience", "Mock Trial", "Moot Court",
       "Microsoft Office"))),
    (r"compliance|regulatory|\bkyc\b|\baml\b", "Compliance",
     (("Compliance", "Legal Research", "Research"), ("Excel", "Microsoft Office"), ("Writing", "Financial Analysis"))),
    (r"policy|legislat|government|public affairs|congress|senat|assembly|mayor|city council|political|civic|"
     r"elections?\b|campaign (intern|fellow|organizer)|field organiz", "Policy & government",
     (("Policy Analysis", "Legislative Research", "Research"), ("Writing",),
      ("Constituent Services", "Campaigns", "Public Speaking", "Government Relations", "Political Science"))),
    (r"diplomac|foreign|international (affairs|relations)|embassy|consulate|united nations|global affairs",
     "International affairs",
     (("International Relations", "Policy Analysis"), ("Research", "Writing"), ("@language", "Model UN"))),
    (r"investment bank|\bibd\b|m&a|mergers|capital markets|private equity|venture capital|equity research|"
     r"sales (and|&) trading|asset management|wealth management|hedge fund|summer analyst|investment",
     "Investment finance",
     (("Financial Modeling", "Valuation", "Equity Research", "Investment Banking"), ("Excel",),
      ("Accounting", "Financial Analysis", "Economics"),
      ("PowerPoint", "Bloomberg Terminal", "Capital IQ", "FactSet", "PitchBook"))),
    (r"accounting|accountant|\baudit|assurance|\btax\b|bookkeep|payroll|accounts (payable|receivable)", "Accounting",
     (("Accounting",), ("Excel", "QuickBooks"), ("Auditing", "Tax", "Financial Analysis"))),
    (r"financ|treasury|fp&a|budget|banking|credit|underwrit|insurance|\brisk\b", "Finance",
     (("Financial Analysis", "Accounting", "Financial Modeling"), ("Excel",),
      ("Economics", "Data Analysis", "PowerPoint"))),
    (r"consult|strategy|business analyst|business intern|operations|management (intern|trainee)|"
     r"corporate development|entrepreneur", "Consulting & strategy",
     (("Market Research", "Business Strategy", "Consulting", "Case Competitions", "Data Analysis"), ("Excel",),
      ("PowerPoint", "Project Management", "Public Speaking"))),
    (r"real estate|property|leasing", "Real estate",
     (("Real Estate", "Financial Modeling", "Financial Analysis"), ("Excel",),
      ("Sales", "Customer Service", "Market Research"))),
    (r"\bsales\b|business development|account (executive|manager|management)|client (services|relations)|"
     r"customer success", "Sales",
     (("Sales", "Customer Service"), ("Salesforce", "Microsoft Office", "Excel"),
      ("Public Speaking", "Negotiation", "Communication"))),
    (r"human resources|\bhr\b|recruit|talent|people (ops|operations|team)", "HR & recruiting",
     (("Human Resources",), ("Microsoft Office", "Excel", "Google Workspace"),
      ("Communication", "Data Entry", "Administrative Support"))),
    (r"supply chain|logistics|procurement|purchasing|merchandis|buying|retail|category manag", "Operations",
     (("Operations", "Data Analysis"), ("Excel",), ("Project Management", "Negotiation"))),
    (r"marketing|\bbrand|\bgrowth\b|advertis|digital media|e-?commerce|partnerships", "Marketing",
     (("Marketing", "Brand Marketing", "Content Creation", "Social Media"),
      ("Canva", "Adobe Creative Suite", "HubSpot", "Google Analytics", "SEO", "Email Marketing"),
      ("Copywriting", "Writing", "Market Research"))),
    (r"communications|public relations|\bpr\b|media relations|\bpress\b|publicity|spokes", "Communications & PR",
     (("Public Relations", "Writing"), ("Copywriting", "Style Guides", "Editing"),
      ("Social Media", "Content Creation"))),
    (r"social media|\bcontent\b|influencer|community manag|digital", "Social media & content",
     (("Social Media",), ("Content Creation", "Copywriting"),
      ("Canva", "Adobe Creative Suite", "Video Editing", "Photography"))),
    (r"\bevents?\b", "Events",
     (("Event Planning",), ("Microsoft Office", "Google Workspace", "Social Media"),
      ("Communication", "Customer Service"))),
    (r"graphic design|designer|creative|visual", "Design",
     (("Graphic Design", "Adobe Creative Suite", "Canva"), ("Photography", "Video Editing", "Social Media"))),
    (r"editorial|\beditor|journalis|reporter|newsroom|\bnews\b|\bwriter|\bwriting|copy ?edit|proofread|"
     r"fact[- ]?check|magazine|newspaper", "Editorial & journalism",
     (("Writing", "Journalism"), ("Editing", "Copyediting", "Fact-Checking", "Style Guides"),
      ("Interviewing", "Research", "Publications"))),
    (r"publishing|literary|\bbooks?\b", "Publishing",
     (("Publishing", "Editing", "Writing"), ("Copyediting", "Creative Writing"),
      ("Microsoft Office", "Social Media", "Marketing"))),
    (r"podcast|\bradio\b|broadcast|\bvideo\b|\bfilm\b|production|studio|\btv\b|television|documentary",
     "Media production",
     (("Video Editing", "Podcasting", "Broadcasting", "Performing Arts"), ("Adobe Creative Suite", "Photography"),
      ("Writing", "Social Media"))),
    (r"museum|curatorial|curator|collections?|archiv|gallery|exhibit|librar|historic|heritage|preservation|"
     r"cultural|\barts?\b", "Museums, archives & arts",
     (("Art History", "Curatorial Research", "Historical Research", "Archival Research"),
      ("Collections Management", "Library", "Museum Education", "Arts Administration"), ("Research", "Writing"))),
    (r"theat(er|re)|\bmusic|\bdance|\bopera\b|symphony|orchestra|perform", "Performing arts",
     (("Performing Arts", "Arts Administration"), ("Event Planning", "Marketing", "Social Media"),
      ("Writing", "Customer Service"))),
    (r"teach|tutor|education|classroom|curriculum|\bschool|instruct|\bcamp\b|youth|literacy", "Education",
     (("Teaching", "Tutoring"), ("Youth Work", "Public Speaking"), ("Writing", "Microsoft Office", "Google Workspace"))),
    (r"research|fellow|scholar|think tank", "Research",
     (("Academic Research", "Research", "Policy Analysis"), ("Writing",),
      ("Data Analysis", "Survey Research", "Statistics", "Qualitative Research", "Excel"))),
    (r"non-?profit|development (intern|associate|assistant)|fundrais|\bgrants?\b|donor|philanthrop|foundation|"
     r"advoca|community|outreach|social impact|volunteer|human rights|\bjustice\b", "Nonprofit",
     (("Fundraising", "Grant Writing", "Advocacy", "Community Outreach", "Program Coordination"), ("Writing",),
      ("Event Planning", "Volunteer Coordination", "Volunteering", "Microsoft Office"))),
    (r"translat|interpret|bilingual|spanish|french|mandarin|chinese|arabic|korean|japanese|portuguese|language",
     "Languages", (("@language",), ("Writing", "Translation"))),
    (r"\bdata\b|analytics|analyst", "Analytics",
     (("Excel", "SQL", "Tableau", "Data Analysis"), ("Research", "Statistics"), ("PowerPoint", "Writing"))),
    (r"admin|\boffice\b|reception|clerical|executive assistant|coordinator", "Administrative",
     (("Microsoft Office", "Google Workspace", "Excel"), ("Administrative Support", "Data Entry"),
      ("Communication", "Customer Service"))),
    (r"intern|trainee|assistant|associate|fellow|apprentice|co-?op", "General",
     (("Microsoft Office", "Google Workspace", "Excel"), ("Writing", "Communication", "Public Speaking"),
      ("Research", "Data Analysis"))),
)

# Rules naming what the intern does vs. rules naming the kind of organization or product they do it for.
_FUNCTION_RULES = {"Accounting", "Finance", "HR & recruiting", "Sales", "Operations"}
_DOMAIN_RULES = {"Editorial & journalism", "Publishing", "Media production", "Museums, archives & arts",
                 "Performing arts", "Education", "Nonprofit"}

_TITLE_RULES_COMPILED = tuple((re.compile(p, re.I), label, groups) for p, label, groups in TITLE_RULES)


def title_requirements(title: str) -> list[tuple[str, tuple[tuple[str, ...], ...]]]:
    """Return the (label, requirement groups) rules that apply to a job title.

    The generic rule is only used when nothing more specific matched.
    """
    matched = [(label, groups) for rx, label, groups in _TITLE_RULES_COMPILED if rx.search(title)]
    specific = [m for m in matched if m[0] != "General"]
    if any(label in _FUNCTION_RULES for label, _ in specific):
        specific = [m for m in specific if m[0] not in _DOMAIN_RULES]
    return specific or matched
