"""Where internship postings come from.

* SimplifyJobs' community-maintained internship list (default; thousands of
  active tech internships, refreshed many times a day).
* Public job-board APIs for individual companies: Greenhouse, Lever and Ashby.
  These include full job descriptions, which makes matching much sharper.
* Description enrichment: for Simplify postings that link to a Greenhouse,
  Lever or Ashby page, we can fetch the real description for the top matches.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .models import Posting
from .skills import normalize_category

log = logging.getLogger(__name__)

USER_AGENT = "internmatch/0.1 (+https://github.com/tobedecided/to-be-decided)"
SIMPLIFY_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer{year}-Internships/dev/.github/scripts/listings.json"
)

_SPONSORSHIP = {
    "offers sponsorship": "offers",
    "does not offer sponsorship": "no_sponsorship",
    "u.s. citizenship is required": "citizenship_required",
}
_DEGREES = {
    "bachelor's": "bachelor",
    "bachelors": "bachelor",
    "master's": "master",
    "masters": "master",
    "phd": "phd",
    "associate's": "associate",
    "mba": "mba",
    "high school": "high_school",
}
_TERM_RX = re.compile(r"\b(summer|fall|autumn|winter|spring)\s*'?(20\d{2}|\d{2})\b", re.I)
_INTERN_RX = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bapprentice", re.I)


def simplify_urls(now: datetime | None = None) -> list[str]:
    """Candidate URLs for the Simplify listings feed, most relevant first.

    The repo is renamed each recruiting season (Summer2026-Internships,
    Summer2027-Internships, ...), so try next summer's repo first from July on.
    """
    override = os.environ.get("INTERNMATCH_LISTINGS_URL")
    if override:
        return [override]
    now = now or datetime.now(timezone.utc)
    years = [now.year + 1, now.year] if now.month >= 7 else [now.year, now.year + 1]
    return [SIMPLIFY_URL_TEMPLATE.format(year=y) for y in years]


def _ts(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            # Lever uses milliseconds, Simplify uses seconds.
            seconds = value / 1000 if value > 10**11 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def parse_simplify(data: Iterable[dict[str, Any]]) -> list[Posting]:
    postings: list[Posting] = []
    for row in data:
        if not row.get("active") or not row.get("is_visible", True):
            continue
        title = (row.get("title") or "").strip()
        company = (row.get("company_name") or "").strip()
        if not title or not company:
            continue
        terms = [t for t in (row.get("terms") or []) if t and t != "N/A"]
        postings.append(
            Posting(
                id=f"simplify:{row.get('id')}",
                source="Simplify",
                company=company,
                title=title,
                category=normalize_category(row.get("category")),
                locations=[loc for loc in (row.get("locations") or []) if loc],
                url=row.get("url") or "",
                terms=terms or terms_from_title(title),
                date_posted=_ts(row.get("date_posted")),
                sponsorship=_SPONSORSHIP.get((row.get("sponsorship") or "").strip().lower(), "unknown"),
                degrees=[_DEGREES.get(d.strip().lower(), d.strip().lower()) for d in row.get("degrees") or []],
            )
        )
    return postings


def terms_from_title(title: str) -> list[str]:
    out = []
    for season, year in _TERM_RX.findall(title):
        season = "Fall" if season.lower() == "autumn" else season.capitalize()
        year = year if len(year) == 4 else f"20{year}"
        out.append(f"{season} {year}")
    return out


def category_from_title(title: str) -> str:
    t = title.lower()
    if re.search(r"quant|trading|trader", t):
        return "Quant"
    if re.search(r"product manag|\bapm\b|\bpm\b|product design|designer|\bux\b", t):
        return "Product"
    if re.search(r"hardware|electrical|fpga|asic|analog|circuit|silicon|\brf\b|verification|pcb|mechanical", t):
        return "Hardware"
    if re.search(r"machine learning|\bml\b|\bai\b|data|analytic|analyst|research scien", t):
        return "AI/ML/Data"
    if re.search(r"software|engineer|developer|\bswe\b|backend|frontend|full[\s-]?stack|devops|security", t):
        return "Software"
    return "Other"


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)  # Greenhouse double-escapes its HTML
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)<\s*(br|/p|/div|/h\d|/ul|/ol)[^>]*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Company job boards
# ---------------------------------------------------------------------------

_BOARD_URL_PATTERNS = (
    ("greenhouse", re.compile(r"greenhouse\.io/(?:embed/job_board\?for=)?([\w.-]+)", re.I)),
    ("lever", re.compile(r"lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com/([\w.%-]+)", re.I)),
)


def parse_board_spec(spec: str) -> tuple[str, str] | None:
    """Accept "greenhouse:stripe", "lever:palantir", "ashby:ramp" or a board URL."""
    spec = spec.strip()
    if not spec:
        return None
    if "://" not in spec and ":" in spec:
        kind, _, slug = spec.partition(":")
        kind = kind.strip().lower()
        if kind in {"greenhouse", "lever", "ashby"} and slug.strip():
            return kind, slug.strip()
        return None
    for kind, rx in _BOARD_URL_PATTERNS:
        m = rx.search(spec)
        if m and m.group(1).lower() not in {"jobs", "job-boards", "boards"}:
            return kind, m.group(1)
    return None


def _is_internship(title: str, extra: str = "") -> bool:
    return bool(_INTERN_RX.search(title) or _INTERN_RX.search(extra))


def parse_greenhouse_board(slug: str, data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        title = job.get("title") or ""
        if not _is_internship(title):
            continue
        loc = (job.get("location") or {}).get("name") or ""
        out.append(
            Posting(
                id=f"greenhouse:{slug}:{job.get('id')}",
                source="Greenhouse",
                company=job.get("company_name") or slug.replace("-", " ").title(),
                title=title,
                category=category_from_title(title),
                locations=[p.strip() for p in re.split(r";|\|", loc) if p.strip()],
                url=job.get("absolute_url") or "",
                terms=terms_from_title(title),
                date_posted=_ts(job.get("first_published") or job.get("updated_at")),
                description=html_to_text(job.get("content") or ""),
            )
        )
    return out


def _lever_description(job: dict[str, Any]) -> str:
    parts = [job.get("descriptionPlain") or html_to_text(job.get("description") or "")]
    for block in job.get("lists") or []:
        parts.append(block.get("text") or "")
        parts.append(html_to_text(block.get("content") or ""))
    parts.append(job.get("additionalPlain") or "")
    return "\n".join(p for p in parts if p).strip()


def parse_lever_board(slug: str, data: list[dict[str, Any]]) -> list[Posting]:
    out = []
    for job in data:
        title = job.get("text") or ""
        cats = job.get("categories") or {}
        if not _is_internship(title, cats.get("commitment") or ""):
            continue
        locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
        out.append(
            Posting(
                id=f"lever:{slug}:{job.get('id')}",
                source="Lever",
                company=slug.replace("-", " ").title(),
                title=title,
                category=category_from_title(title),
                locations=locs,
                url=job.get("hostedUrl") or "",
                terms=terms_from_title(title),
                date_posted=_ts(job.get("createdAt")),
                description=_lever_description(job),
            )
        )
    return out


def parse_ashby_board(slug: str, data: dict[str, Any]) -> list[Posting]:
    out = []
    for job in data.get("jobs", []):
        title = job.get("title") or ""
        if not _is_internship(title, job.get("employmentType") or ""):
            continue
        if job.get("isListed") is False:
            continue
        locs = [job.get("location") or ""] + [
            s.get("location") or "" for s in job.get("secondaryLocations") or []
        ]
        if job.get("isRemote"):
            locs.append("Remote")
        out.append(
            Posting(
                id=f"ashby:{slug}:{job.get('id')}",
                source="Ashby",
                company=slug.replace("-", " ").title(),
                title=title,
                category=category_from_title(title),
                locations=[loc for loc in locs if loc],
                url=job.get("jobUrl") or "",
                terms=terms_from_title(title),
                date_posted=_ts(job.get("publishedAt")),
                description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""),
            )
        )
    return out


def board_api_url(kind: str, slug: str) -> str:
    if kind == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    if kind == "lever":
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"
    if kind == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    raise ValueError(f"unknown board type: {kind}")


def parse_board(kind: str, slug: str, data: Any) -> list[Posting]:
    return {"greenhouse": parse_greenhouse_board, "lever": parse_lever_board, "ashby": parse_ashby_board}[kind](
        slug, data
    )


# ---------------------------------------------------------------------------
# Description lookup for individual posting URLs
# ---------------------------------------------------------------------------

_GH_JOB = re.compile(r"greenhouse\.io/([\w.-]+)/jobs/(\d+)", re.I)
_LEVER_JOB = re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)/([0-9a-f-]{36})", re.I)
_ASHBY_JOB = re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)/([0-9a-f-]{36})", re.I)


def detail_source(url: str) -> tuple[str, str, str] | None:
    """(kind, board slug, job id) if we know how to fetch this posting's description."""
    for kind, rx in (("greenhouse", _GH_JOB), ("lever", _LEVER_JOB), ("ashby", _ASHBY_JOB)):
        m = rx.search(url or "")
        if m and m.group(1).lower() not in {"embed"}:
            return kind, m.group(1), m.group(2)
    return None


# ---------------------------------------------------------------------------
# Store with on-disk cache
# ---------------------------------------------------------------------------


def default_cache_dir() -> Path:
    env = os.environ.get("INTERNMATCH_CACHE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "internmatch"


class ListingStore:
    """Fetches and caches postings. Safe to share across requests."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_hours: float = 6,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.ttl = ttl_hours * 3600
        self._transport = transport
        self._postings: list[Posting] | None = None
        self._fetched_at: float | None = None
        self._source_url: str | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()
        self._descriptions: dict[str, str] | None = None
        self._board_cache: dict[str, tuple[float, list[Posting]]] = {}

    # -- plumbing ---------------------------------------------------------
    def _client(self, timeout: float = 30) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=self._transport,
        )

    @property
    def _listings_file(self) -> Path:
        return self.cache_dir / "simplify_postings.json"

    @property
    def _descriptions_file(self) -> Path:
        return self.cache_dir / "descriptions.json"

    def _write_json(self, path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(path)
        except OSError as exc:  # a read-only home dir shouldn't break the app
            log.warning("could not write cache %s: %s", path, exc)

    def _load_cached_listings(self) -> tuple[float, str, list[Posting]] | None:
        try:
            payload = json.loads(self._listings_file.read_text())
            postings = [Posting.model_validate(p) for p in payload["postings"]]
            return payload["fetched_at"], payload.get("source_url", ""), postings
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # -- Simplify feed ----------------------------------------------------
    async def get_postings(self, force: bool = False) -> list[Posting]:
        async with self._lock:
            now = time.time()
            if not force and self._postings is not None and now - (self._fetched_at or 0) < self.ttl:
                return self._postings
            cached = None if force else self._load_cached_listings()
            if cached and now - cached[0] < self.ttl:
                self._fetched_at, self._source_url, self._postings = cached
                return self._postings
            try:
                url, postings = await self._download_simplify()
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.warning("listing refresh failed: %s", self._last_error)
                stale = cached or self._load_cached_listings()
                if self._postings is not None:
                    return self._postings
                if stale:
                    self._fetched_at, self._source_url, self._postings = stale
                    return self._postings
                raise RuntimeError(f"Could not download internship listings ({self._last_error}).") from exc
            self._postings, self._fetched_at, self._source_url, self._last_error = postings, now, url, None
            self._write_json(
                self._listings_file,
                {
                    "fetched_at": now,
                    "source_url": url,
                    "postings": [p.model_dump(mode="json") for p in postings],
                },
            )
            return postings

    async def _download_simplify(self) -> tuple[str, list[Posting]]:
        errors = []
        async with self._client(timeout=60) as client:
            for url in simplify_urls():
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    postings = parse_simplify(resp.json())
                    if postings:
                        return url, postings
                    errors.append(f"{url}: no active postings")
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"{url}: {exc}")
        raise RuntimeError("; ".join(errors))

    def status(self) -> dict[str, Any]:
        return {
            "count": len(self._postings or []),
            "fetched_at": datetime.fromtimestamp(self._fetched_at, tz=timezone.utc).isoformat()
            if self._fetched_at
            else None,
            "source_url": self._source_url,
            "error": self._last_error,
        }

    # -- company boards ---------------------------------------------------
    async def fetch_boards(self, specs: Iterable[str]) -> tuple[list[Posting], list[str]]:
        parsed, errors = [], []
        for spec in specs:
            p = parse_board_spec(spec)
            if p is None:
                if spec.strip():
                    errors.append(f"Couldn't understand board '{spec}'. Use e.g. greenhouse:stripe or lever:palantir.")
                continue
            parsed.append(p)
        if not parsed:
            return [], errors

        async def one(client: httpx.AsyncClient, kind: str, slug: str) -> list[Posting]:
            key = f"{kind}:{slug.lower()}"
            hit = self._board_cache.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
            resp = await client.get(board_api_url(kind, slug))
            if resp.status_code == 404:
                raise LookupError(f"{key} not found")
            resp.raise_for_status()
            postings = parse_board(kind, slug, resp.json())
            self._board_cache[key] = (time.time(), postings)
            return postings

        out: list[Posting] = []
        async with self._client() as client:
            results = await asyncio.gather(*(one(client, k, s) for k, s in parsed), return_exceptions=True)
        for (kind, slug), res in zip(parsed, results):
            if isinstance(res, BaseException):
                errors.append(f"{kind}:{slug}: {res}")
            else:
                out.extend(res)
        return out, errors

    # -- description enrichment -------------------------------------------
    def _load_descriptions(self) -> dict[str, str]:
        if self._descriptions is None:
            try:
                self._descriptions = json.loads(self._descriptions_file.read_text())
            except (OSError, ValueError):
                self._descriptions = {}
        return self._descriptions

    async def enrich(self, postings: list[Posting], limit: int = 40, concurrency: int = 8) -> int:
        """Fill in ``description`` for up to ``limit`` postings. Returns how many were filled."""
        cache = self._load_descriptions()
        todo: list[tuple[Posting, tuple[str, str, str]]] = []
        filled = 0
        for p in postings:
            if p.description:
                continue
            if p.url in cache:
                p.description = cache[p.url]
                filled += bool(p.description)
                continue
            src = detail_source(p.url)
            if src and len(todo) < limit:
                todo.append((p, src))
        if not todo:
            return filled

        sem = asyncio.Semaphore(concurrency)
        ashby_boards: dict[str, asyncio.Task[dict[str, Any]]] = {}

        async def ashby_board(client: httpx.AsyncClient, slug: str) -> dict[str, Any]:
            resp = await client.get(board_api_url("ashby", slug))
            resp.raise_for_status()
            return resp.json()

        async def one(client: httpx.AsyncClient, posting: Posting, src: tuple[str, str, str]) -> str:
            kind, slug, job_id = src
            async with sem:
                if kind == "greenhouse":
                    host = "boards-api.eu.greenhouse.io" if ".eu.greenhouse.io" in posting.url else "boards-api.greenhouse.io"
                    resp = await client.get(f"https://{host}/v1/boards/{slug}/jobs/{job_id}")
                    resp.raise_for_status()
                    return html_to_text(resp.json().get("content") or "")
                if kind == "lever":
                    resp = await client.get(f"https://api.lever.co/v0/postings/{slug}/{job_id}")
                    resp.raise_for_status()
                    return _lever_description(resp.json())
                if slug not in ashby_boards:
                    ashby_boards[slug] = asyncio.ensure_future(ashby_board(client, slug))
                board = await ashby_boards[slug]
                for job in board.get("jobs", []):
                    if job.get("id") == job_id:
                        return job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
                return ""

        async with self._client(timeout=20) as client:
            results = await asyncio.gather(*(one(client, p, s) for p, s in todo), return_exceptions=True)
        for (posting, _), res in zip(todo, results):
            if isinstance(res, BaseException):
                log.info("description fetch failed for %s: %s", posting.url, res)
                continue
            cache[posting.url] = res
            if res:
                posting.description = res
                filled += 1
        self._write_json(self._descriptions_file, cache)
        return filled


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------

_SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def _term_key(term: str) -> tuple[int, int]:
    season, _, year = term.partition(" ")
    return (int(year) if year.isdigit() else 0, _SEASON_ORDER.get(season, 9))


def term_options(postings: Iterable[Posting], now: datetime | None = None) -> list[dict[str, Any]]:
    """Terms that still make sense to apply for, in calendar order, with posting counts."""
    now = now or datetime.now(timezone.utc)
    counts: dict[str, int] = {}
    for p in postings:
        for t in p.terms:
            counts[t] = counts.get(t, 0) + 1
    out = []
    for term, n in counts.items():
        year, season = _term_key(term)
        if not year:
            continue
        # Drop terms that have clearly started. "Winter <this year>" is used for both Jan and Dec
        # starts, so keep it.
        if year < now.year:
            continue
        if year == now.year and ((season in (1, 2) and now.month >= 6) or (season == 3 and now.month >= 9)):
            continue
        out.append({"term": term, "count": n})
    return sorted(out, key=lambda d: _term_key(d["term"]))


def default_terms(options: list[dict[str, Any]], now: datetime | None = None) -> list[str]:
    """Next summer if it has postings (that's when most students intern), else the busiest term."""
    now = now or datetime.now(timezone.utc)
    summer = f"Summer {now.year + 1 if now.month >= 7 else now.year}"
    if any(o["term"] == summer for o in options):
        return [summer]
    return [max(options, key=lambda o: o["count"])["term"]] if options else []
