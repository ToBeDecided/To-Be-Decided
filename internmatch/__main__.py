"""Command-line entry point.

    internmatch serve                      # web UI at http://127.0.0.1:8000
    internmatch analyze resume.pdf --term "Summer 2027" --location NYC --top 25
    internmatch refresh                    # re-download listings now
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import textwrap
import threading
import webbrowser
from pathlib import Path

from .models import AnalysisResult, Profile


def _profile_from_args(args: argparse.Namespace) -> Profile:
    return Profile(
        degree_level=args.degree,
        grad_year=args.grad_year,
        gpa=args.gpa,
        work_authorization=args.work_auth,
        target_terms=args.term or [],
        target_categories=args.category or [],
        locations=args.location or [],
        remote_ok=not args.no_remote,
        location_strict=args.strict_location,
        extra_skills=[s.strip() for s in (args.skills or "").split(",") if s.strip()],
        max_age_days=args.max_age,
    )


def _bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _print_report(res: AnalysisResult, show_all: bool) -> None:
    r, p = res.resume, res.profile
    print(f"\nRESUME SCORE  {r.overall}/100  (grade {r.grade})    candidate strength {p.candidate_strength}/100"
          f"    level: {p.level}")
    for s in r.subscores:
        print(f"  {s.label:<22} {_bar(s.score)} {s.score:>3}   {s.detail}")
    if r.strengths:
        print("\nStrengths")
        for s in r.strengths:
            print(textwrap.fill(s, 100, initial_indent="  + ", subsequent_indent="    "))
    if r.improvements:
        print("\nTop fixes")
        for s in r.improvements:
            print(textwrap.fill(s, 100, initial_indent="  - ", subsequent_indent="    "))
    print("\nBest-fit categories: " + ", ".join(f"{c.category} {c.fit}" for c in p.category_fit[:3]))
    for n in p.notes:
        print(textwrap.fill(n, 100, initial_indent="  * ", subsequent_indent="    "))

    st = res.stats
    print(f"\nScanned {st['postings_considered']} postings -> {st['eligible']} you can apply to "
          f"({st['likely']} likely, {st['target']} target, {st['reach']} reach)")
    rows = res.matches if show_all else res.recommended
    print(f"\n{'RECOMMENDED APPLICATIONS' if not show_all else 'ALL MATCHES'}")
    print(f"  {'Tier':<7}{'Odds':>5}{'Fit':>5}  {'Company':<24}{'Role':<46}{'Location':<22}Posted")
    for m in rows:
        post = m.posting
        age = post.age_days()
        posted = "?" if age is None else ("today" if age < 1 else f"{int(age)}d ago")
        loc = (post.locations[0] if post.locations else "") + (f" +{len(post.locations) - 1}"
                                                                 if len(post.locations) > 1 else "")
        print(f"  {m.tier:<7}{m.likelihood:>5}{m.match_score:>5}  {post.company[:23]:<24}{post.title[:45]:<46}"
              f"{loc[:21]:<22}{posted}")
        print(f"  {'':<17}{post.url}")


def _write_csv(res: AnalysisResult, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "odds", "fit", "company", "title", "category", "locations", "terms", "posted",
                    "selectivity", "matched_skills", "missing_skills", "url"])
        for m in res.matches:
            p = m.posting
            w.writerow([m.tier, m.likelihood, m.match_score, p.company, p.title, p.category, "; ".join(p.locations),
                        "; ".join(p.terms), p.date_posted.date().isoformat() if p.date_posted else "",
                        m.selectivity, ", ".join(m.matched_skills), ", ".join(m.missing_skills), p.url])


def cmd_analyze(args: argparse.Namespace) -> int:
    from .engine import analyze
    from .resume import ResumeParseError, extract_text
    from .sources import ListingStore, default_terms, term_options

    path = Path(args.resume)
    try:
        text, pages = extract_text(path.name, path.read_bytes())
    except (OSError, ResumeParseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    profile = _profile_from_args(args)
    store = ListingStore()

    async def run() -> AnalysisResult:
        if not profile.target_terms:
            profile.target_terms = default_terms(term_options(await store.get_postings()))
            if profile.target_terms:
                print(f"(targeting {', '.join(profile.target_terms)}; use --term to change)", file=sys.stderr)
        return await analyze(text, profile, store, pages=pages, boards=args.board or [], enrich=not args.no_enrich,
                             top=args.top, limit=10_000)

    try:
        res = asyncio.run(run())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        payload = res.model_dump(mode="json", exclude={"resume_text"})
        print(json.dumps(payload, indent=2))
    else:
        _print_report(res, args.all)
    if args.csv:
        _write_csv(res, args.csv)
        print(f"\nWrote {len(res.matches)} matches to {args.csv}", file=sys.stderr)

    if args.ai:
        from . import ai

        if not ai.available():
            print("\nAI review skipped: set ANTHROPIC_API_KEY to enable it.", file=sys.stderr)
            return 0
        print(f"\nAsking {ai.MODEL} for a review...", file=sys.stderr)
        try:
            review = ai.review(text, profile, res.resume, res.recommended)
        except ai.AIError as exc:
            print(f"AI review failed: {exc}", file=sys.stderr)
            return 1
        print(f"\nAI REVIEW  {review.score}/100\n{textwrap.fill(review.summary, 100)}")
        for title, items in (("Strengths", review.strengths), ("Weaknesses", review.weaknesses),
                             ("Next steps", review.next_steps)):
            print(f"\n{title}")
            for it in items:
                print(textwrap.fill(it, 100, initial_indent="  - ", subsequent_indent="    "))
        for rw in review.bullet_rewrites:
            print(f"\n  before: {rw.original}\n  after:  {rw.improved}\n  why:    {rw.why}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Internship Matcher running at {url}  (Ctrl+C to stop)")
    uvicorn.run("internmatch.server:app", host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_refresh(_: argparse.Namespace) -> int:
    from .sources import ListingStore

    store = ListingStore()
    try:
        postings = asyncio.run(store.get_postings(force=True))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded {len(postings)} active internship postings from {store.status()['source_url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internmatch", description="Rate your resume and find internships "
                                                                     "you're likely to get interviews for.")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.set_defaults(func=cmd_serve)

    a = sub.add_parser("analyze", help="score a resume and rank internships in the terminal")
    a.add_argument("resume", help="PDF, DOCX or TXT resume")
    a.add_argument("--term", action="append", help='e.g. "Summer 2027" (repeatable; default: next summer)')
    a.add_argument("--category", action="append", choices=["Software", "AI/ML/Data", "Hardware", "Quant", "Product",
                                                           "Other"], help="repeatable")
    a.add_argument("--location", action="append", help="preferred location, e.g. NYC, CA, Seattle (repeatable)")
    a.add_argument("--strict-location", action="store_true", help="hide postings outside your locations")
    a.add_argument("--no-remote", action="store_true", help="don't treat remote roles as a location match")
    a.add_argument("--grad-year", type=int)
    a.add_argument("--degree", choices=["high_school", "associate", "bachelor", "master", "phd", "mba"])
    a.add_argument("--gpa", type=float)
    a.add_argument("--work-auth", choices=["citizen", "authorized", "needs_sponsorship"], default="citizen")
    a.add_argument("--skills", help="extra skills not on your resume, comma-separated")
    a.add_argument("--board", action="append", help="also search a company board, e.g. greenhouse:stripe")
    a.add_argument("--max-age", type=int, help="only postings from the last N days")
    a.add_argument("--top", type=int, default=25, help="size of the recommended list")
    a.add_argument("--all", action="store_true", help="print every eligible match, not just the shortlist")
    a.add_argument("--no-enrich", action="store_true", help="don't fetch job descriptions")
    a.add_argument("--json", action="store_true", help="print JSON instead of a report")
    a.add_argument("--csv", help="also write all matches to this CSV file")
    a.add_argument("--ai", action="store_true", help="add a Claude-powered review (needs ANTHROPIC_API_KEY)")
    a.set_defaults(func=cmd_analyze)

    r = sub.add_parser("refresh", help="re-download the internship listings")
    r.set_defaults(func=cmd_refresh)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
