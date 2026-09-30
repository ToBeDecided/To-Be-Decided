"""Rough employer selectivity tiers for pre-law, business and humanities internships.

Selectivity is the biggest factor in cold-application interview rates that a
resume can't change. The lists are deliberately coarse and only cover
well-known, heavily applied-to employers; everyone else is "standard".
"""

from __future__ import annotations

import re

ELITE = (
    # Investment banking, private equity, hedge funds
    "goldman sachs", "morgan stanley", "j p morgan", "jpmorgan", "jpmorgan chase", "evercore", "lazard",
    "centerview partners", "pjt partners", "moelis", "perella weinberg", "qatalyst", "blackstone", "kkr",
    "apollo global management", "carlyle", "tpg", "warburg pincus", "bain capital", "bridgewater", "citadel",
    "jane street", "two sigma", "d e shaw",
    # Management consulting (MBB)
    "mckinsey", "boston consulting group", "bcg", "bain and company", "bain",
    # Elite law firms
    "wachtell lipton", "cravath", "sullivan and cromwell", "skadden", "davis polk", "simpson thacher",
    "latham and watkins", "kirkland and ellis", "paul weiss", "cleary gottlieb", "debevoise",
    # Courts, White House, international institutions
    "supreme court of the united states", "white house", "united nations", "world bank",
    "international monetary fund", "imf",
    # Media and culture
    "new york times", "the new yorker", "the atlantic", "conde nast", "vogue",
    "metropolitan museum of art", "museum of modern art", "moma", "smithsonian", "getty", "getty museum",
    "j paul getty trust",
    # Policy
    "brookings", "council on foreign relations", "carnegie endowment",
    # Big tech business roles
    "google", "apple", "meta", "netflix",
)
HIGH = (
    # Banks and asset managers
    "bank of america", "citi", "citigroup", "citibank", "barclays", "ubs", "deutsche bank", "wells fargo",
    "rbc capital markets", "jefferies", "houlihan lokey", "william blair", "piper sandler", "lincoln international",
    "guggenheim", "blackrock", "fidelity", "vanguard", "state street", "pimco", "t rowe price", "capital one",
    "american express", "federal reserve",
    # Consulting and accounting
    "deloitte", "pwc", "pricewaterhousecoopers", "ey", "ernst and young", "kpmg", "accenture", "oliver wyman",
    "l e k consulting", "kearney", "booz allen hamilton",
    # Large law firms
    "jones day", "sidley austin", "gibson dunn", "ropes and gray", "white and case", "covington and burling",
    "wilmerhale", "weil gotshal", "milbank", "cooley", "orrick", "hogan lovells", "dla piper", "baker mckenzie",
    "mayer brown", "morgan lewis", "paul hastings", "proskauer", "quinn emanuel", "fried frank", "willkie farr",
    "akin gump", "arnold and porter", "king and spalding", "cadwalader", "vinson and elkins", "winston and strawn",
    "dechert",
    # Government and policy
    "department of justice", "department of state", "u s department of state", "federal bureau of investigation",
    "fbi", "central intelligence agency", "securities and exchange commission", "library of congress",
    "national archives", "rand corporation", "american enterprise institute", "heritage foundation",
    "cato institute", "center for strategic and international studies", "csis", "aclu",
    "american civil liberties union", "human rights watch", "amnesty international", "naacp",
    "southern poverty law center",
    # Media, publishing, entertainment
    "washington post", "wall street journal", "dow jones", "npr", "cnn", "nbcuniversal", "abc news", "cbs news",
    "pbs", "associated press", "reuters", "bloomberg", "politico", "axios", "penguin random house", "harpercollins",
    "simon and schuster", "macmillan", "hachette", "scholastic", "hearst", "walt disney", "disney",
    "warner bros", "paramount", "universal music", "sony music", "spotify",
    # Consumer brands and agencies
    "procter and gamble", "p and g", "l oreal", "loreal", "unilever", "pepsico", "coca cola", "estee lauder",
    "lvmh", "nike", "edelman", "ogilvy", "wpp", "fleishmanhillard", "weber shandwick", "publicis", "omnicom",
    # Museums and performing arts
    "guggenheim museum", "whitney museum", "national gallery of art", "art institute of chicago", "lacma",
    "museum of fine arts", "brooklyn museum", "lincoln center", "carnegie hall",
    # Big tech (non-engineering roles)
    "amazon", "microsoft", "salesforce", "linkedin",
)
_SUFFIXES = re.compile(
    r"\b(inc|llc|llp|l l p|pllc|ltd|corp|corporation|co|company|group|holdings|plc|lp|pc|p c|usa|us|"
    r"north america|the)\b",
    re.I,
)
# Short or common names that must match the whole (normalized) employer name.
_EXACT_ONLY = {"bain", "bcg", "imf", "moma", "getty", "vogue", "citi", "ubs", "ey", "fbi", "csis", "aclu", "naacp",
               "npr", "cnn", "pbs", "wpp", "nike", "meta", "apple", "google", "netflix", "tpg", "kkr", "cooley",
               "milbank", "dechert", "orrick", "disney", "amazon", "citadel", "p and g", "bloomberg"}


def normalize_company(name: str) -> str:
    n = name.lower().replace("&", " and ").replace(".", " ").replace("'", " ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = _SUFFIXES.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def _matches(norm: str, key: str) -> bool:
    if key in _EXACT_ONLY:
        return norm == key
    return norm == key or re.search(rf"(?:^| ){re.escape(key)}(?: |$)", norm) is not None


_ELITE_KEYS = tuple({normalize_company(k) for k in ELITE})
_HIGH_KEYS = tuple({normalize_company(k) for k in HIGH})


def selectivity(company: str) -> str:
    norm = normalize_company(company)
    if any(_matches(norm, k) for k in _ELITE_KEYS):
        return "elite"
    if any(_matches(norm, k) for k in _HIGH_KEYS):
        return "high"
    return "standard"
