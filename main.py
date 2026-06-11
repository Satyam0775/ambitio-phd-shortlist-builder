#!/usr/bin/env python3
"""
PhD Shortlist Builder — CLI entry point.

Usage:
    python main.py --input sample_profiles/student.json
    python main.py --input sample_profiles/student.json --feedback feedback_learning/outcomes.csv
    python main.py --serve   # start FastAPI server
"""

import argparse
import json
import os
import sys
import logging

from dotenv import load_dotenv

load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.schemas.student import StudentProfile
from app.schemas.output import ShortlistOutput
from app.services.pipeline import Pipeline
from app.feedback_learning.feedback_processor import FeedbackProcessor
from app.utils.logging import get_logger

logger = get_logger("main")


def load_profile(path: str) -> StudentProfile:
    """Load and validate student profile JSON."""
    if not os.path.exists(path):
        logger.error("Profile file not found: %s", path)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        profile = StudentProfile(**data)
        logger.info("Loaded profile: %s (%s)", profile.name, profile.student_id)
        return profile
    except Exception as exc:
        logger.error("Invalid profile JSON: %s", exc)
        sys.exit(1)


def save_output(output: ShortlistOutput, output_dir: str, student_id: str) -> str:
    """Save ShortlistOutput JSON to sample_output/<student_id>.json."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{student_id}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        # Use model_dump for Pydantic v2 serialization
        json.dump(output.model_dump(), f, indent=2, ensure_ascii=False, default=str)
    logger.info("Output saved to: %s", output_path)
    return output_path


def run_pipeline(args) -> None:
    """Main pipeline execution."""
    profile = load_profile(args.input)

    # Load feedback if provided
    feedback_processor = FeedbackProcessor()
    if args.feedback and os.path.exists(args.feedback):
        logger.info("Loading feedback from: %s", args.feedback)
        feedback_processor.load_outcomes(args.feedback)
        stats = feedback_processor.get_stats()
        logger.info("Feedback stats: %s", stats)
    elif args.feedback:
        logger.warning("Feedback file not found at %s — skipping.", args.feedback)

    # Run pipeline
    pipeline = Pipeline()
    output = pipeline.run(profile, feedback_adjuster=feedback_processor)

    # Log summary
    logger.info(
        "Generated %d recommendations for student %s",
        len(output.recommendations),
        profile.student_id,
    )

    tier_counts = {}
    for rec in output.recommendations:
        tier_counts[rec.tier] = tier_counts.get(rec.tier, 0) + 1
    logger.info("Tier breakdown: %s", tier_counts)

    if output.recommendations:
        top = output.recommendations[0]
        logger.info(
            "Top recommendation: %s @ %s (score=%.3f, tier=%s)",
            top.name, top.institution, top.score, top.tier,
        )

    # Save output
    output_dir = args.output_dir if hasattr(args, "output_dir") else "sample_output"
    save_output(output, output_dir, profile.student_id)


def run_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start FastAPI development server."""
    import uvicorn
    from fastapi import FastAPI
    from app.api.router import router

    app = FastAPI(
        title="PhD Shortlist Builder",
        description="Ambitio — PhD supervisor matching pipeline",
        version="1.0.0",
    )
    app.include_router(router)

    logger.info("Starting API server at http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    parser = argparse.ArgumentParser(
        description="PhD Shortlist Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --input sample_profiles/student.json
  python main.py --input sample_profiles/student.json --feedback feedback_learning/outcomes.csv
  python main.py --serve
        """,
    )
    parser.add_argument(
        "--input",
        type=str,
        help="Path to student profile JSON",
    )
    parser.add_argument(
        "--feedback",
        type=str,
        default=None,
        help="Path to outcomes CSV for feedback learning",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="sample_output",
        help="Directory to write output JSON (default: sample_output/)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start FastAPI HTTP server",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host for API server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for API server (default: 8000)",
    )

    args = parser.parse_args()

    if args.serve:
        run_server(args.host, args.port)
    elif args.input:
        run_pipeline(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
