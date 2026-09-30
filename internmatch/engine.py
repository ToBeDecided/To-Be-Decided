"""End-to-end pipeline: resume -> report, candidate -> ranked postings."""

from __future__ import annotations

from datetime import datetime, timezone

from .matcher import build_candidate, check_posting, rank, recommend, summarize_profile
from .models import AnalysisResult, Match, Posting, Profile, ResumeReport
from .resume import parse_resume, score_resume
from .skills import classify
from .sources import ListingStore, detail_source, detect_pay, terms_from_text


async def analyze(
    resume_text: str,
    profile: Profile,
    store: ListingStore,
    *,
    pages: int | None = None,
    boards: list[str] | None = None,
    enrich: bool = True,
    enrich_limit: int = 40,
    top: int = 30,
    limit: int = 300,
    postings: list[Posting] | None = None,
) -> AnalysisResult:
    parsed = parse_resume(resume_text, pages=pages)
    report = score_resume(parsed, profile)
    candidate = build_candidate(parsed, report, profile)

    board_errors: list[str] = []
    if postings is None:
        postings = list(await store.get_postings())
    if boards:
        extra, board_errors = await store.fetch_boards(boards)
        postings = extra + postings  # board postings (with descriptions) win de-duplication

    now = datetime.now(timezone.utc)
    matches, excluded = rank(postings, candidate, now)

    enriched = 0
    if enrich and matches:
        # Pull real job descriptions for the most promising roles that lack one, then re-score with them.
        candidates = [m.posting for m in matches[: enrich_limit * 3] if not m.posting.description
                      and detail_source(m.posting.url)][:enrich_limit]
        if candidates:
            enriched = await store.enrich(candidates, limit=enrich_limit)
            if enriched:
                matches, excluded = rank(postings, candidate, now)

    recommended = recommend(matches, n=top)
    summary = summarize_profile(candidate, report, excluded)
    tiers = {t: sum(1 for m in matches if m.tier == t) for t in ("Likely", "Target", "Reach")}
    return AnalysisResult(
        resume=report,
        profile=summary,
        recommended=recommended,
        matches=matches[:limit],
        stats={
            "postings_considered": len(postings),
            "eligible": len(matches),
            "likely": tiers["Likely"],
            "target": tiers["Target"],
            "reach": tiers["Reach"],
            "descriptions_fetched": enriched,
            **{f"excluded_{k.replace(' ', '_')}": v for k, v in excluded.items()},
            "board_errors": "; ".join(board_errors) or None,
            "listings_updated": store.status().get("fetched_at"),
        },
        resume_text=resume_text,
    )


def posting_from_text(title: str, company: str = "", description: str = "", location: str = "",
                      url: str = "") -> Posting:
    """Build a Posting from details the user pasted in (from Handshake, LinkedIn, a firm's site...)."""
    category, how = classify(title)
    if category is None:
        category = "Consulting & Business"
    pay, pay_detail = detect_pay(f"{title}\n{description}")
    return Posting(
        id="pasted",
        source="Pasted",
        company=company.strip() or "This employer",
        title=title.strip(),
        category=category,
        locations=[x.strip() for x in location.split(";") if x.strip()] if location else [],
        url=url.strip(),
        terms=terms_from_text(title, description),
        description=description.strip(),
        pay=pay,  # type: ignore[arg-type]
        pay_detail=pay_detail,
        source_category="off-focus" if how == "off" else "",
    )


def check(resume_text: str, profile: Profile, posting: Posting, pages: int | None = None
          ) -> tuple[ResumeReport, Match, str | None]:
    parsed = parse_resume(resume_text, pages=pages)
    report = score_resume(parsed, profile)
    candidate = build_candidate(parsed, report, profile)
    match, blocker = check_posting(posting, candidate)
    if posting.source_category == "off-focus":
        match.warnings.insert(0, "This looks like a tech, science or healthcare role, which is outside this app's "
                                 "focus, so the fit estimate is rough.")
    return report, match, blocker
