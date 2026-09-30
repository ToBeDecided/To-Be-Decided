"""Rough company selectivity tiers.

Selectivity is the single biggest factor in cold-application interview rates
that a resume can't change. This list is deliberately coarse and only covers
well-known, heavily-applied-to employers; everyone else is "standard".
"""

from __future__ import annotations

import re

ELITE = (
    # Quant / trading
    "jane street", "citadel", "citadel securities", "hudson river trading", "two sigma", "jump trading",
    "d e shaw", "de shaw", "optiver", "imc trading", "susquehanna", "tower research", "five rings",
    "radix trading", "millennium", "point72", "cubist", "bridgewater", "drw", "headlands", "xtx markets",
    "voleon", "renaissance technologies", "pdt partners", "aquatic capital",
    # Big tech / AI labs / top startups
    "google", "alphabet", "deepmind", "meta", "meta platforms", "facebook", "apple", "netflix", "openai",
    "anthropic", "stripe", "databricks", "nvidia", "palantir", "ramp", "figma", "notion", "waymo", "spacex", "scale ai", "jane street",
    "airbnb", "citadel llc", "mistral ai", "perplexity", "cursor", "anysphere", "hrt", "sig",
)
HIGH = (
    "microsoft", "amazon", "amazon web services", "aws", "linkedin", "uber", "lyft", "doordash", "pinterest",
    "snap", "snowflake", "coinbase", "robinhood", "plaid", "rippling", "verkada", "anduril", "duolingo",
    "datadog", "cloudflare", "roblox", "salesforce", "adobe", "bloomberg", "tesla", "qualcomm", "goldman sachs",
    "morgan stanley", "capital one", "akuna capital", "old mission", "belvedere trading", "chicago trading company",
    "peak6", "virtu", "imc", "hubspot", "shopify", "instacart", "atlassian", "dropbox", "reddit",
    "twitch", "spotify", "brex", "mercury", "discord", "palo alto networks", "crowdstrike", "mckinsey",
    "boston consulting group", "bcg", "bain", "zoox", "cruise", "aurora", "nuro", "tiktok", "bytedance",
    "blackrock", "de shaw group", "neuralink", "benchling", "asana", "github", "vercel", "retool",
    "sierra", "glean", "cohere", "hugging face", "character ai", "applied intuition", "skydio", "samsara",
)
_SUFFIXES = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|corp|corporation|co|company|technologies|technology|labs|group|holdings|plc|lp|"
    r"securities llc|usa|us|america|north america)\b\.?",
    re.I,
)
_EXACT_ONLY = {"sig", "imc", "hrt", "drw", "aws", "bcg", "snap", "cursor", "sierra", "mercury", "notion", "ramp",
               "meta", "apple", "aurora", "cruise", "bain", "virtu", "glean", "github", "discord"}


def normalize_company(name: str) -> str:
    n = name.lower().replace("&", " and ").replace(".", " ")
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
