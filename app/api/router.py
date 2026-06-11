"""
FastAPI router for the PhD Shortlist Builder.
Provides HTTP endpoints for building shortlists and loading feedback.
"""

import json
import os
import tempfile
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse

from app.schemas.student import StudentProfile
from app.schemas.output import ShortlistOutput
from app.services.pipeline import Pipeline
from app.feedback_learning.feedback_processor import FeedbackProcessor
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["shortlist"])

# Singleton instances (lazy init)
_pipeline: Optional[Pipeline] = None
_feedback: Optional[FeedbackProcessor] = None


def get_pipeline() -> Pipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline()
    return _pipeline


def get_feedback() -> FeedbackProcessor:
    global _feedback
    if _feedback is None:
        _feedback = FeedbackProcessor()
    return _feedback


@router.post("/shortlist", response_model=ShortlistOutput)
async def build_shortlist(profile: StudentProfile) -> ShortlistOutput:
    """
    Build a PhD supervisor shortlist for the given student profile.
    """
    try:
        pipeline = get_pipeline()
        feedback = get_feedback()
        result = pipeline.run(profile, feedback_adjuster=feedback)
        return result
    except Exception as exc:
        logger.error("Pipeline error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/feedback/load")
async def load_feedback(file: UploadFile = File(...)):
    """
    Upload outcomes CSV to update feedback adjustments.
    """
    try:
        contents = await file.read()
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".csv", delete=False
        ) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        feedback = get_feedback()
        feedback.load_outcomes(tmp_path)
        os.unlink(tmp_path)

        stats = feedback.get_stats()
        return JSONResponse({"status": "ok", "stats": stats})
    except Exception as exc:
        logger.error("Feedback load error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
async def health():
    return {"status": "ok", "service": "PhD Shortlist Builder"}
