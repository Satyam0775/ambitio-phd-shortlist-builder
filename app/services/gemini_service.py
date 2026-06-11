"""
Gemini API service.
Used for:
1. Normalizing student research interests (called ONCE per pipeline run).
2. Optionally generating why_match explanations (off by default).

Safe-mode:
- Any 429 / quota / rate-limit error sets self._disabled = True immediately.
- All further calls return deterministic output without hitting the API.
- is_available() is the single check all callers use.
- No exception from this service ever propagates to the pipeline.
"""

import json
import time
import logging
from typing import Optional

from app.config.settings import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Substrings that indicate quota / rate-limit exhaustion in any Gemini SDK error
_QUOTA_SIGNALS = (
    "429",
    "quota",
    "rate limit",
    "rate_limit",
    "resource exhausted",
    "resourceexhausted",
    "too many requests",
    "quota exceeded",
    "billing",
    "unavailable",
)


def _is_quota_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(sig in msg for sig in _QUOTA_SIGNALS)


class GeminiService:
    def __init__(self) -> None:
        self._model = None
        self._api_key = settings.gemini_api_key
        self._disabled: bool = False  # set True on quota/rate-limit; never reset

    # ------------------------------------------------------------------
    # Availability gate
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """False when: no API key, disabled by quota error."""
        return bool(self._api_key) and not self._disabled

    def _disable(self, reason: str) -> None:
        """Permanently disable Gemini for this run. Logs only on first call."""
        if not self._disabled:
            logger.warning("Gemini disabled for this run: %s", reason)
            self._disabled = True

    # ------------------------------------------------------------------
    # Internal model loader
    # ------------------------------------------------------------------

    def _load(self):
        if self._model is None:
            if not self._api_key:
                raise ValueError("GEMINI_API_KEY is not set. Set it in your .env file.")
            try:
                import google.generativeai as genai
                genai.configure(api_key=self._api_key)
                self._model = genai.GenerativeModel("gemini-2.5-flash")
                logger.info("Gemini model loaded.")
            except Exception as exc:
                self._disable(f"model load failed: {exc}")
                raise
        return self._model

    # ------------------------------------------------------------------
    # Core generation — quota errors disable immediately, never retry
    # ------------------------------------------------------------------

    def generate_text(self, prompt: str, max_retries: int = 2) -> str:
        """
        Call Gemini. Quota/rate-limit errors disable immediately and raise.
        Transient errors retry up to max_retries then raise.
        """
        if not self.is_available():
            raise RuntimeError("Gemini is disabled or unavailable.")

        model = self._load()

        for attempt in range(max_retries):
            try:
                response = model.generate_content(prompt)
                return response.text.strip()
            except Exception as exc:
                if _is_quota_error(exc):
                    self._disable(f"quota/rate-limit: {exc}")
                    raise RuntimeError(f"Gemini quota exhausted: {exc}") from exc
                logger.warning("Gemini attempt %d/%d failed: %s", attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        self._disable("failed all retries")
        raise RuntimeError("Gemini API failed after retries.")

    # ------------------------------------------------------------------
    # Interest normalization — called ONCE per pipeline run
    # ------------------------------------------------------------------

    def normalize_interests(self, raw_interests: list[str], profile_summary: str) -> list[str]:
        """
        Normalize research interests via Gemini.
        Returns raw_interests if Gemini unavailable or response invalid. Never raises.
        """
        if not self.is_available():
            logger.info("Gemini unavailable — using raw interests.")
            return raw_interests

        prompt = f"""You are normalizing PhD student research interests for a database search.

Raw interests: {json.dumps(raw_interests)}
Student background: {profile_summary}

Task: Return a JSON array of 3-8 clean, specific research interest strings.
- Remove vague terms like "machine learning in general"
- Make each interest a specific subfield (e.g., "transformer-based NLP for clinical notes")
- Do NOT add new interests the student didn't mention
- Return ONLY the JSON array, no markdown, no explanation.

Example output: ["transformer architectures for NLP", "clinical text mining", "biomedical named entity recognition"]"""

        try:
            text = self.generate_text(prompt)
            text = text.strip()
            if text.startswith("```"):
                text = "\n".join(text.split("\n")[1:-1]).strip()
            result = json.loads(text)
            if isinstance(result, list) and result:
                cleaned = [str(r).strip() for r in result if str(r).strip()]
                if cleaned:
                    return cleaned
        except RuntimeError:
            pass  # quota — already disabled and logged
        except Exception as exc:
            logger.warning("Gemini normalize_interests failed: %s", exc)

        return raw_interests

    # ------------------------------------------------------------------
    # why_match — deterministic by default (enable_gemini_why_match=False)
    # ------------------------------------------------------------------

    def generate_why_match(
        self,
        student_name: str,
        student_interests: list[str],
        student_thesis: Optional[str],
        researcher_name: str,
        researcher_institution: str,
        researcher_focus: list[str],
        top_papers: list[dict],
        shared_topics: list[str],
    ) -> str:
        """
        Generate why_match explanation.
        Uses deterministic fallback by default (settings.enable_gemini_why_match=False).
        Any Gemini failure also falls back. Never raises.
        """
        if not getattr(settings, "enable_gemini_why_match", False) or not self.is_available():
            return self._deterministic_why_match(
                student_name, student_interests, researcher_name,
                researcher_institution, researcher_focus, top_papers, shared_topics,
            )

        papers_text = "\n".join(
            f"  - \"{p.get('title', '')}\" ({p.get('year', 'n.d.')}, {p.get('venue', '')})"
            for p in top_papers[:3]
        )

        prompt = f"""You are writing a personalized note for a PhD applicant explaining WHY a specific professor is a strong match.

Student: {student_name}
Student's research interests: {", ".join(student_interests)}
Student's thesis/project: {student_thesis or "not specified"}

Professor: {researcher_name}
Institution: {researcher_institution}
Research focus: {", ".join(researcher_focus)}
Shared topics: {", ".join(shared_topics) if shared_topics else "similar research area"}
Recent papers:
{papers_text if papers_text else "  - (papers not available)"}

Write a 2-3 sentence why_match explanation that:
1. References at least one specific paper title from the list above (if available)
2. Explains the specific topical overlap between student's work and the professor's research
3. Is factual, not generic — avoid phrases like "leading researcher" or "prestigious lab"
4. Is written for the student to understand why this professor is worth emailing

Output only the explanation text, no labels, no JSON."""

        try:
            result = self.generate_text(prompt)
            if result and len(result) > 20:
                return result
        except Exception as exc:
            logger.warning(
                "Gemini why_match failed for %s — using deterministic: %s", researcher_name, exc
            )

        return self._deterministic_why_match(
            student_name, student_interests, researcher_name,
            researcher_institution, researcher_focus, top_papers, shared_topics,
        )

    # ------------------------------------------------------------------
    # Deterministic fallback — zero API calls, always returns non-empty string
    # ------------------------------------------------------------------

    @staticmethod
    def _deterministic_why_match(
        student_name: str,
        student_interests: list[str],
        researcher_name: str,
        researcher_institution: str,
        researcher_focus: list[str],
        top_papers: list[dict],
        shared_topics: list[str],
    ) -> str:
        """
        Rule-based why_match. Uses shared_topics if available, else researcher_focus.
        Appends the best available paper reference. Always returns non-empty text.
        """
        topics = shared_topics[:3] if shared_topics else researcher_focus[:3]
        topic_str = ", ".join(topics) if topics else "related research areas"
        focus_str = ", ".join(researcher_focus[:3]) if researcher_focus else topic_str

        paper_sentence = ""
        for p in top_papers[:3]:
            title = (p.get("title") or "").strip()
            if title:
                year = p.get("year")
                year_part = f" ({year})" if year else ""
                paper_sentence = (
                    f' Recent publications such as "{title}"{year_part} '
                    f"demonstrate active research in this area."
                )
                break

        return (
            f"Prof. {researcher_name}'s work at {researcher_institution} aligns with "
            f"{student_name}'s interests in {topic_str}. "
            f"Their research focuses on {focus_str}."
            f"{paper_sentence}"
        )