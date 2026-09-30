"""FastAPI app: JSON API plus the single-page UI in ./static."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import ai
from .engine import analyze
from .models import Match, Profile, ResumeReport
from .resume import ResumeParseError, extract_text
from .skills import CATEGORIES
from .sources import ListingStore, default_terms, term_options

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_MATCHES = 5000


class AIReviewRequest(BaseModel):
    resume_text: str
    profile: Profile = Profile()
    report: ResumeReport | None = None
    matches: list[Match] = []


def create_app(store: ListingStore | None = None) -> FastAPI:
    app = FastAPI(title="Internship Matcher", version="0.1.0")
    app.state.store = store or ListingStore()

    @app.exception_handler(ResumeParseError)
    async def _resume_error(_: Request, exc: ResumeParseError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/meta")
    async def meta() -> dict:
        s: ListingStore = app.state.store
        try:
            postings = await s.get_postings()
            error = None
        except RuntimeError as exc:
            postings, error = [], str(exc)
        options = term_options(postings)
        return {
            "listings": {**s.status(), "error": error or s.status().get("error")},
            "terms": options,
            "default_terms": default_terms(options),
            "categories": list(CATEGORIES),
            "ai": {"available": ai.available(), "model": ai.MODEL},
        }

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
        pages = None
        text = resume_text
        if resume is not None and resume.filename:
            data = await resume.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Resume file is too large (5 MB max).")
            text, pages = await run_in_threadpool(extract_text, resume.filename, data)
        if len(text.strip()) < 50:
            raise HTTPException(400, "Upload a resume (PDF, DOCX or TXT) or paste its text.")
        try:
            prof = Profile.model_validate(json.loads(profile or "{}"))
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, f"Invalid profile: {exc}") from exc
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
        return payload

    @app.post("/api/ai-review")
    async def ai_review(req: AIReviewRequest) -> dict:
        if not ai.available():
            raise HTTPException(
                503,
                "AI review needs an Anthropic API key. Set ANTHROPIC_API_KEY (or run `ant auth login`) "
                "and restart the app.",
            )
        try:
            result = await run_in_threadpool(ai.review, req.resume_text, req.profile, req.report, req.matches)
        except ai.AIError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return result.model_dump(mode="json")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
