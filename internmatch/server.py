"""FastAPI app: JSON API plus the single-page UI in ./static."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import __version__, ai, config
from .engine import analyze, check, posting_from_text
from .models import Match, Profile, ResumeReport
from .resume import ResumeParseError, extract_text
from .skills import CATEGORIES, TRACKS
from .sources import ListingStore, default_terms, term_options

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_MATCHES = 5000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class AIReviewRequest(BaseModel):
    resume_text: str
    profile: Profile = Profile()
    report: ResumeReport | None = None
    matches: list[Match] = []


class SettingsUpdate(BaseModel):
    values: dict[str, str | None]


def _hostname(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header[: host_header.find("]") + 1].lower()
    return host_header.split(":")[0].lower()


async def _resume_from_form(resume: UploadFile | None, resume_text: str) -> tuple[str, int | None]:
    pages = None
    text = resume_text
    if resume is not None and resume.filename:
        data = await resume.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Resume file is too large (5 MB max).")
        text, pages = await run_in_threadpool(extract_text, resume.filename, data)
    if len(text.strip()) < 50:
        raise HTTPException(400, "Upload a resume (PDF, Word, Pages export or text) or paste its text.")
    return text, pages


def _profile(raw: str) -> Profile:
    try:
        return Profile.model_validate(json.loads(raw or "{}"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"Invalid profile: {exc}") from exc


def create_app(store: ListingStore | None = None, allow_any_host: bool | None = None) -> FastAPI:
    app = FastAPI(title="Internship Matcher", version=__version__)
    app.state.store = store or ListingStore()
    if allow_any_host is None:
        allow_any_host = os.environ.get("INTERNMATCH_ALLOW_ANY_HOST") == "1"

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # The app holds your resume and API keys, so only answer requests addressed to this computer
        # (blocks DNS-rebinding tricks) and reject writes coming from other websites (blocks CSRF).
        host = request.headers.get("host", "")
        if not allow_any_host and _hostname(host) not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Host not allowed."}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ResumeParseError)
    async def _resume_error(_: Request, exc: ResumeParseError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    async def health() -> dict:
        return {"app": "internmatch", "version": __version__}

    @app.get("/api/meta")
    async def meta() -> dict:
        s: ListingStore = app.state.store
        try:
            postings = await s.get_postings()
            error = None
        except RuntimeError as exc:
            postings, error = [], str(exc)
        options = term_options(postings)
        status = s.status()
        return {
            "version": __version__,
            "platform": sys.platform,
            "listings": {**status, "error": error or status.get("error")},
            "terms": options,
            "default_terms": default_terms(options),
            "tracks": {k: list(v) for k, v in TRACKS.items()},
            "categories": list(CATEGORIES),
            "ai": {"available": ai.available(), "model": ai.MODEL},
            "settings": config.describe(),
            "config_dir": str(config.config_dir()),
        }

    @app.post("/api/settings")
    async def save_settings(update: SettingsUpdate) -> dict:
        unknown = set(update.values) - set(config.SETTINGS)
        if unknown:
            raise HTTPException(422, f"Unknown settings: {', '.join(sorted(unknown))}")
        await run_in_threadpool(config.save, update.values)
        s: ListingStore = app.state.store
        s.invalidate()
        return {"settings": config.describe(), "ai": {"available": ai.available(), "model": ai.MODEL}}

    @app.post("/api/refresh")
    async def refresh() -> dict:
        s: ListingStore = app.state.store
        try:
            await s.get_postings(force=True)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return s.status()

    @app.post("/api/analyze")
    async def analyze_endpoint(
        resume: UploadFile | None = File(default=None),
        resume_text: str = Form(default=""),
        profile: str = Form(default="{}"),
        boards: str = Form(default=""),
        enrich: bool = Form(default=True),
        top: int = Form(default=30),
    ) -> dict:
        text, pages = await _resume_from_form(resume, resume_text)
        prof = _profile(profile)
        board_list = [b.strip() for b in boards.replace("\n", ",").split(",") if b.strip()]
        try:
            result = await analyze(
                text, prof, app.state.store, pages=pages, boards=board_list, enrich=enrich,
                top=max(5, min(top, 100)), limit=MAX_MATCHES,
            )
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        payload = result.model_dump(mode="json")
        # The full list can run to thousands of rows; job descriptions are only needed for the shortlist.
        for m in payload["matches"]:
            m["posting"]["description"] = ""
        for m in payload["matches"] + payload["recommended"]:
            m["posting"]["skills"] = {}
        return payload

    @app.post("/api/check")
    async def check_endpoint(
        title: str = Form(...),
        company: str = Form(default=""),
        location: str = Form(default=""),
        description: str = Form(default=""),
        url: str = Form(default=""),
        resume: UploadFile | None = File(default=None),
        resume_text: str = Form(default=""),
        profile: str = Form(default="{}"),
    ) -> dict:
        if not title.strip():
            raise HTTPException(400, "Give the posting a title.")
        text, pages = await _resume_from_form(resume, resume_text)
        posting = posting_from_text(title, company, description, location, url)
        report, match, blocker = await run_in_threadpool(check, text, _profile(profile), posting, pages)
        return {"match": match.model_dump(mode="json"), "blocker": blocker,
                "resume": {"overall": report.overall, "grade": report.grade}}

    @app.post("/api/ai-review")
    async def ai_review(req: AIReviewRequest) -> dict:
        if not ai.available():
            raise HTTPException(503, "AI review needs an Anthropic API key. Add one in Settings.")
        try:
            result = await run_in_threadpool(ai.review, req.resume_text, req.profile, req.report, req.matches)
        except ai.AIError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return result.model_dump(mode="json")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
