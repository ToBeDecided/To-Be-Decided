"""Command-line entry point.

    internmatch serve                      # web UI at http://127.0.0.1:8000
    internmatch analyze resume.pdf --track Pre-Law --location DC --top 25
    internmatch doctor                     # check your setup and the job sources
    internmatch config set usajobs_api_key KEY
    internmatch refresh                    # re-download listings now
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import shutil
import socket
import sys
import textwrap
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .models import AnalysisResult, Profile
from .skills import CATEGORIES, TRACKS


def _profile_from_args(args: argparse.Namespace) -> Profile:
    return Profile(
        degree_level=args.degree,
        grad_year=args.grad_year,
        gpa=args.gpa,
        work_authorization=args.work_auth,
        target_terms=args.term or [],
        include_unknown_terms=not args.only_stated_terms,
        target_tracks=args.track or [],
        target_categories=args.category or [],
        paid_only=args.paid_only,
        locations=args.location or [],
        remote_ok=not args.no_remote,
        location_strict=args.strict_location,
        us_only=not args.include_international,
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
        print(f"  {s.label:<25} {_bar(s.score)} {s.score:>3}   {s.detail}")
    if r.strengths:
        print("\nStrengths")
        for s in r.strengths:
            print(textwrap.fill(s, 100, initial_indent="  + ", subsequent_indent="    "))
    if r.improvements:
        print("\nTop fixes")
        for s in r.improvements:
            print(textwrap.fill(s, 100, initial_indent="  - ", subsequent_indent="    "))
    print("\nTrack fit: " + ", ".join(f"{t.category} {t.fit}" for t in p.track_fit))
    print("Best-fit areas: " + ", ".join(f"{c.category} {c.fit}" for c in p.category_fit[:3]))
    for n in p.notes:
        print(textwrap.fill(n, 100, initial_indent="  * ", subsequent_indent="    "))

    st = res.stats
    print(f"\nScanned {st['postings_considered']} postings -> {st['eligible']} you can apply to "
          f"({st['likely']} likely, {st['target']} target, {st['reach']} reach)")
    rows = res.matches if show_all else res.recommended
    print(f"\n{'RECOMMENDED APPLICATIONS' if not show_all else 'ALL MATCHES'}")
    print(f"  {'Tier':<7}{'Odds':>5}{'Fit':>5}  {'Employer':<26}{'Role':<44}{'Location':<22}Posted")
    for m in rows:
        post = m.posting
        age = post.age_days()
        posted = "?" if age is None else ("today" if age < 1 else f"{int(age)}d ago")
        loc = (post.locations[0] if post.locations else "") + (f" +{len(post.locations) - 1}"
                                                                 if len(post.locations) > 1 else "")
        pay = f"  [{post.pay_detail or post.pay}]" if post.pay != "unknown" else ""
        print(f"  {m.tier:<7}{m.likelihood:>5}{m.match_score:>5}  {post.company[:25]:<26}{post.title[:43]:<44}"
              f"{loc[:21]:<22}{posted}{pay}")
        print(f"  {'':<17}{post.category} · {post.url}")


def _write_csv(res: AnalysisResult, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "odds", "fit", "employer", "title", "category", "locations", "terms", "posted", "deadline",
                    "pay", "selectivity", "matched_skills", "missing_skills", "source", "url"])
        for m in res.matches:
            p = m.posting
            w.writerow([m.tier, m.likelihood, m.match_score, p.company, p.title, p.category, "; ".join(p.locations),
                        "; ".join(p.terms), p.date_posted.date().isoformat() if p.date_posted else "",
                        p.deadline.date().isoformat() if p.deadline else "", p.pay_detail or p.pay, m.selectivity,
                        ", ".join(m.matched_skills), ", ".join(m.missing_skills), p.source, p.url])


def cmd_analyze(args: argparse.Namespace) -> int:
    from .engine import analyze
    from .resume import ResumeParseError, extract_text
    from .sources import ListingStore, default_terms, term_options

    path = Path(args.resume).expanduser()
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
                print(f"(targeting {', '.join(profile.target_terms)} plus postings with no stated term; "
                      "use --term to change)", file=sys.stderr)
        return await analyze(text, profile, store, pages=pages, boards=args.board or [], enrich=not args.no_enrich,
                             top=args.top, limit=10_000)

    try:
        res = asyncio.run(run())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(res.model_dump(mode="json", exclude={"resume_text"}), indent=2))
    else:
        _print_report(res, args.all)
    if args.csv:
        _write_csv(res, args.csv)
        print(f"\nWrote {len(res.matches)} matches to {args.csv}", file=sys.stderr)

    if args.ai:
        from . import ai

        if not ai.available():
            print("\nAI review skipped: add an Anthropic key with `internmatch config set anthropic_api_key ...`.",
                  file=sys.stderr)
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


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def _port_in_use(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _is_ours(port: int) -> bool:
    import httpx

    try:
        with httpx.Client(trust_env=False, timeout=1.5) as client:
            return client.get(f"http://127.0.0.1:{port}/api/health").json().get("app") == "internmatch"
    except (httpx.HTTPError, ValueError):
        return False


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    port = args.port
    if _port_in_use(port):
        if _is_ours(port):
            url = f"http://127.0.0.1:{port}"
            print(f"Internship Matcher is already running at {url}. Opening it in your browser.")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        free = next((p for p in range(port + 1, port + 30) if not _port_in_use(p)), None)
        if free is None:
            print(f"error: ports {port}-{port + 29} are all busy. Try --port 9000.", file=sys.stderr)
            return 1
        print(f"Port {port} is busy, using {free} instead.")
        port = free

    loopback = args.host in {"127.0.0.1", "localhost", "::1"}
    if not loopback:
        os.environ["INTERNMATCH_ALLOW_ANY_HOST"] = "1"
        print("Warning: serving on your network. Anyone who can reach this computer can use the app.")
    url = f"http://127.0.0.1:{port}"
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Internship Matcher {__version__} running at {url}")
    print("Keep this window open while you use the app. Press Ctrl+C to stop.")
    uvicorn.run("internmatch.server:app", host=args.host, port=port, log_level="warning")
    return 0


# ---------------------------------------------------------------------------
# refresh / config / doctor
# ---------------------------------------------------------------------------


def cmd_refresh(_: argparse.Namespace) -> int:
    from .sources import SOURCES, ListingStore

    store = ListingStore()
    try:
        postings = asyncio.run(store.get_postings(force=True))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Loaded {len(postings)} internship postings.")
    for name, st in store.status()["sources"].items():
        if st["enabled"]:
            print(f"  {SOURCES[name].label}: {st['count']}" + (f"  ({st['error']})" if st["error"] else ""))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    from . import config

    if args.action in {"set", "unset"}:
        if args.name not in config.SETTINGS:
            print(f"error: unknown setting {args.name!r}. Choose from: {', '.join(config.SETTINGS)}", file=sys.stderr)
            return 2
        if args.action == "set" and not args.value:
            print("error: give a value, e.g. internmatch config set usajobs_email you@example.com", file=sys.stderr)
            return 2
        config.save({args.name: args.value if args.action == "set" else None})
        print(f"{'Saved' if args.action == 'set' else 'Removed'} {args.name} ({config.config_dir() / 'config.json'})")
        return 0
    print(f"Settings file: {config.config_dir() / 'config.json'}")
    for name, info in config.describe().items():
        where = f" (from {info['source']})" if info["source"] else ""
        print(f"  {name:<20} {info['value'] if info['configured'] else '-'}{where}")
    return 0


def _ok(flag: bool) -> str:
    return "OK  " if flag else "FAIL"


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import ai, config
    from .sources import SOURCES, ListingStore

    failures = 0
    os_name = f"macOS {platform.mac_ver()[0]}" if sys.platform == "darwin" else platform.platform()
    print(f"Internship Matcher {__version__}")
    print(f"Python {platform.python_version()} ({sys.executable})")
    print(f"System {os_name} ({platform.machine()})\n")

    py_ok = sys.version_info >= (3, 10)
    failures += not py_ok
    print(f"[{_ok(py_ok)}] Python 3.10 or newer")
    missing = []
    for mods in (("fastapi",), ("uvicorn",), ("httpx",), ("pypdf",), ("docx",), ("python_multipart", "multipart"),
                 ("anthropic",), ("pydantic",)):
        for mod in mods:
            try:
                __import__(mod)
                break
            except ImportError:
                continue
        else:
            missing.append(mods[0])
    failures += bool(missing)
    print(f"[{_ok(not missing)}] Required packages" + (f" (missing: {', '.join(missing)})" if missing else ""))
    for label, folder in (("Settings folder", config.config_dir()), ("Cache folder", config.cache_dir())):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-test"
            probe.write_text("ok")
            probe.unlink()
            writable = True
        except OSError:
            writable = False
        failures += not writable
        print(f"[{_ok(writable)}] {label} writable: {folder}")
    if sys.platform == "darwin":
        print(f"[{'OK  ' if shutil.which('textutil') else 'WARN'}] textutil (reads .doc and .rtf resumes)")
    port_busy = _port_in_use(8000)
    print(f"[{'OK  ' if not port_busy or _is_ours(8000) else 'WARN'}] Port 8000 "
          + ("free" if not port_busy else "in use" + (" by Internship Matcher" if _is_ours(8000) else
                                                      " by another program (the app will pick another port)")))
    print(f"[{'OK  ' if ai.available() else 'INFO'}] AI review (Claude): "
          + ("configured" if ai.available() else "not configured (optional)"))

    print("\nInternship sources (live check):")
    store = ListingStore(cache_dir=config.cache_dir())
    live_failures = 0
    for name, info in SOURCES.items():
        if not store.enabled(name):
            print(f"  [INFO] {info.label}: not configured. Free key: {info.signup}")
            continue
        start = time.time()
        try:
            postings, detail = asyncio.run(store.fetch_source(name))
        except Exception as exc:  # report every kind of failure, this is a diagnostic
            live_failures += 1
            print(f"  [FAIL] {info.label}: {type(exc).__name__}: {exc}")
            continue
        ok = bool(postings)
        live_failures += not ok
        print(f"  [{_ok(ok)}] {info.label}: {len(postings)} relevant internships ({time.time() - start:.1f}s)")
        if args.verbose:
            for q, d in (detail.get("queries") or {}).items():
                print(f"         {q}: {d}")
            by_cat: dict[str, int] = {}
            for p in postings:
                by_cat[p.category] = by_cat.get(p.category, 0) + 1
            print(f"         by category: {dict(sorted(by_cat.items(), key=lambda kv: -kv[1]))}")
            for p in postings[:6]:
                print(f"         e.g. {p.title} | {p.company} | {p.category} | {', '.join(p.locations[:2])} | "
                      f"terms={p.terms} pay={p.pay}:{p.pay_detail} posted={p.date_posted} url={p.url[:70]}")
    print()
    if failures:
        print("Some setup checks failed. Re-run ./start.command (it reinstalls what's missing).")
    elif live_failures:
        print("Setup is fine, but a job source couldn't be reached. Check your internet connection.")
    else:
        print("Everything looks good.")
    return 1 if failures or (args.strict and live_failures) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internmatch", description="Rate your resume and find pre-law, "
                                                                     "business and humanities internships.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="start the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    s.set_defaults(func=cmd_serve)

    a = sub.add_parser("analyze", help="score a resume and rank internships in the terminal")
    a.add_argument("resume", help="PDF, Word, RTF or text resume")
    a.add_argument("--track", action="append", choices=list(TRACKS), help="repeatable")
    a.add_argument("--category", action="append", choices=list(CATEGORIES), help="repeatable")
    a.add_argument("--term", action="append", help='e.g. "Summer 2027" (repeatable; default: next summer)')
    a.add_argument("--only-stated-terms", action="store_true", help="hide postings that don't name a term")
    a.add_argument("--paid-only", action="store_true", help="hide postings that say they're unpaid")
    a.add_argument("--location", action="append", help="preferred location, e.g. DC, NYC, Chicago, TX (repeatable)")
    a.add_argument("--strict-location", action="store_true", help="hide postings outside your locations")
    a.add_argument("--no-remote", action="store_true", help="don't treat remote roles as a location match")
    a.add_argument("--include-international", action="store_true", help="also show postings outside the U.S.")
    a.add_argument("--grad-year", type=int)
    a.add_argument("--degree", choices=["high_school", "associate", "bachelor", "master", "phd", "mba", "jd"])
    a.add_argument("--gpa", type=float)
    a.add_argument("--work-auth", choices=["citizen", "authorized", "needs_sponsorship"], default="citizen")
    a.add_argument("--skills", help="extra skills not on your resume, comma-separated")
    a.add_argument("--board", action="append", help="also search a company board, e.g. greenhouse:nytimes")
    a.add_argument("--max-age", type=int, help="only postings from the last N days")
    a.add_argument("--top", type=int, default=25, help="size of the recommended list")
    a.add_argument("--all", action="store_true", help="print every eligible match, not just the shortlist")
    a.add_argument("--no-enrich", action="store_true", help="don't fetch job descriptions")
    a.add_argument("--json", action="store_true", help="print JSON instead of a report")
    a.add_argument("--csv", help="also write all matches to this CSV file")
    a.add_argument("--ai", action="store_true", help="add a Claude-powered review (needs an Anthropic key)")
    a.set_defaults(func=cmd_analyze)

    d = sub.add_parser("doctor", help="check your setup and whether the job sources are reachable")
    d.add_argument("--verbose", "-v", action="store_true", help="show per-query details and sample postings")
    d.add_argument("--strict", action="store_true", help="exit non-zero if a job source fails")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("config", help="show or change API keys and other settings")
    c.add_argument("action", nargs="?", choices=["show", "set", "unset"], default="show")
    c.add_argument("name", nargs="?")
    c.add_argument("value", nargs="?")
    c.set_defaults(func=cmd_config)

    r = sub.add_parser("refresh", help="re-download the internship listings")
    r.set_defaults(func=cmd_refresh)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
