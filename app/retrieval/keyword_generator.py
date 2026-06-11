"""
Uses Gemini to expand a student's research interests into
retrieval keywords and OpenAlex concept queries.
"""

import json
import logging
from typing import Optional

from app.utils.logging import get_logger
from app.schemas.student import StudentProfile

logger = get_logger(__name__)


class KeywordGenerator:
    """
    Generates structured retrieval keywords from a student profile.
    Falls back to rule-based expansion if Gemini is unavailable.
    """

    def __init__(self, gemini_service=None) -> None:
        self.gemini = gemini_service

    def generate(self, profile: StudentProfile) -> dict:
        """
        Returns a dict:
        {
          "primary_keywords": [...],   # exact match queries
          "secondary_keywords": [...], # broader context
          "concept_terms": [...],      # for OpenAlex concept search
          "normalized_interests": [...] # clean interest strings
        }
        """
        if self.gemini:
            try:
                return self._generate_with_gemini(profile)
            except Exception as exc:
                logger.warning("Gemini keyword gen failed, falling back: %s", exc)

        return self._generate_fallback(profile)

    def _generate_with_gemini(self, profile: StudentProfile) -> dict:
        interests_text = "\n".join(f"- {i}" for i in profile.research_interests)
        thesis_text = ""
        for edu in profile.education:
            if edu.thesis:
                thesis_text += f"\n- {edu.thesis}"

        prompt = f"""You are helping build a PhD supervisor search engine.

Student research interests:
{interests_text}

Thesis/project titles:{thesis_text or " (none stated)"}

Skills: {", ".join(profile.skills[:15])}

Task: Generate retrieval keywords for searching academic databases.

Return ONLY valid JSON with this structure:
{{
  "normalized_interests": ["clean version of each stated interest"],
  "primary_keywords": ["specific keyword 1", "specific keyword 2", ...],
  "secondary_keywords": ["broader related term 1", ...],
  "concept_terms": ["OpenAlex concept term 1", ...]
}}

Rules:
- primary_keywords: 8-15 specific technical terms from the student's domain
- secondary_keywords: 5-10 broader umbrella terms
- concept_terms: 5-8 terms likely to match OpenAlex concept taxonomy
- normalized_interests: clean, standardized version of each interest
- No explanations, no markdown — raw JSON only."""

        response_text = self.gemini.generate_text(prompt)
        # Strip markdown fences if present
        response_text = response_text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        data = json.loads(response_text)
        logger.info(
            "Gemini generated %d primary keywords",
            len(data.get("primary_keywords", [])),
        )
        return data

    def _generate_fallback(self, profile: StudentProfile) -> dict:
        """Rule-based keyword expansion."""
        from app.utils.text import extract_keywords_from_text, clean_text

        all_text = " ".join(profile.research_interests)
        for edu in profile.education:
            if edu.thesis:
                all_text += " " + edu.thesis
            if edu.field:
                all_text += " " + edu.field
        for proj in profile.projects:
            all_text += " " + proj.title + " " + (proj.description or "")

        keywords = extract_keywords_from_text(clean_text(all_text))
        # Deduplicate, take top 20 by length as proxy for specificity
        seen = set()
        unique = []
        for k in sorted(keywords, key=len, reverse=True):
            if k not in seen:
                seen.add(k)
                unique.append(k)

        primary = unique[:12]
        secondary = profile.research_interests[:5]

        return {
            "normalized_interests": profile.research_interests,
            "primary_keywords": primary,
            "secondary_keywords": secondary,
            "concept_terms": secondary[:5],
        }
