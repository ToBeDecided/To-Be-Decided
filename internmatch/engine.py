"""End-to-end pipeline: resume -> report, candidate -> ranked postings."""

from __future__ import annotations

from datetime import datetime, timezone

from .matcher import build_candidate, rank, recommend, summarize_profile
from .models import AnalysisResult, Posting, Profile
from .resume import parse_resume, score_resume
from .sources import ListingStore, detail_source


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
        # Pull real job descriptions for the most promising roles, then re-score with them.
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
            **{f"excluded_{k}": v for k, v in excluded.items()},
            "board_errors": "; ".join(board_errors) or None,
            "listings_updated": store.status().get("fetched_at"),
        },
        resume_text=resume_text,
    )
