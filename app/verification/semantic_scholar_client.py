"""
Semantic Scholar verification client.
Used as a secondary source to cross-check author identity, citations,
and topic consistency — reducing same-name collisions.

Rate-limit safe-mode:
- On HTTP 429, the client does NOT sleep and retry.
  Instead it sets self._disabled = True immediately and returns _unverified().
- All subsequent calls return _unverified() instantly, keeping the pipeline moving.
- One sleep (3s back-off) is attempted on the FIRST 429 only. If the retry also
  hits 429, the client disables itself permanently for the run.
- Transient network errors (non-429) still retry via tenacity (max 3 attempts).
- is_available() lets callers check before attempting a call.
"""

import time
import logging
from typing import Optional
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config.settings import settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

SS_BASE = settings.semantic_scholar_base_url
SS_KEY = settings.semantic_scholar_api_key

AUTHOR_FIELDS = (
    "authorId,name,affiliations,homepage,paperCount,citationCount,"
    "hIndex,papers.title,papers.year,papers.fieldsOfStudy,"
    "papers.citationCount,papers.externalIds"
)

PAPER_FIELDS = "title,year,fieldsOfStudy,citationCount,authors,externalIds,venue"

# Minimum score to accept an SS candidate as a match
MIN_MATCH_SCORE = 0.35


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens longer than 2 chars from an institution name."""
    import re
    return {w.lower() for w in re.findall(r"\b[a-z]{3,}\b", text.lower())}


def _institution_overlap(inst_a: str, inst_b: str) -> float:
    """Token-overlap similarity between two institution name strings. Returns 0–1."""
    if not inst_a or not inst_b:
        return 0.0
    tokens_a = _tokenize(inst_a)
    tokens_b = _tokenize(inst_b)
    if not tokens_a or not tokens_b:
        return 0.0
    shared = tokens_a & tokens_b
    return len(shared) / min(len(tokens_a), len(tokens_b))


class SemanticScholarClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if SS_KEY:
            self.session.headers.update({"x-api-key": SS_KEY})
        # Rate-limit safe-mode flag — set True on unrecoverable 429
        self._disabled: bool = False

    # ------------------------------------------------------------------
    # Availability gate
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """False once the client has been disabled by a persistent 429."""
        return not self._disabled

    def _disable(self, reason: str) -> None:
        """Permanently disable SS for this run. Logs only on first call."""
        if not self._disabled:
            logger.warning(
                "Semantic Scholar disabled for this run: %s. "
                "Pipeline will continue without SS verification.",
                reason,
            )
            self._disabled = True

    # ------------------------------------------------------------------
    # HTTP layer — fail-fast on 429, retry only on transient errors
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _get(self, url: str, params: dict) -> dict:
        """
        HTTP GET with tenacity retry for transient network errors.
        429 is handled separately: one short back-off then disable.
        Raises requests.HTTPError on non-429 HTTP errors (caught by callers).
        """
        resp = self.session.get(url, params=params, timeout=settings.request_timeout)

        if resp.status_code == 429:
            # Do NOT raise here — raise_for_status would trigger tenacity retry.
            # Instead raise a distinct exception so the caller can handle it.
            raise _RateLimitError("HTTP 429 from Semantic Scholar")

        resp.raise_for_status()
        return resp.json()

    def _get_safe(self, url: str, params: dict) -> Optional[dict]:
        """
        Wrapper around _get that handles 429 gracefully:
        - First 429: log, wait 3s, retry once.
        - Second 429 or persistent failure: disable client, return None.
        Callers treat None as an empty / unverified result.
        """
        if self._disabled:
            return None

        for attempt in range(2):  # max 2 attempts before disabling
            try:
                return self._get(url, params)
            except _RateLimitError:
                if attempt == 0:
                    logger.warning(
                        "Semantic Scholar rate limit (attempt %d) — waiting 3s before retry.",
                        attempt + 1,
                    )
                    time.sleep(3)
                else:
                    self._disable("persistent HTTP 429 after retry")
                    return None
            except Exception as exc:
                logger.debug("SS request failed: %s", exc)
                return None

        return None  # unreachable but satisfies type checker

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def search_author(self, name: str, limit: int = 10) -> list[dict]:
        """Search author by name. Returns empty list if SS is unavailable."""
        if self._disabled:
            return []
        data = self._get_safe(
            f"{SS_BASE}/author/search",
            {"query": name, "fields": AUTHOR_FIELDS, "limit": limit},
        )
        if data is None:
            return []
        return data.get("data", [])

    def get_author_by_id(self, ss_author_id: str) -> Optional[dict]:
        """Fetch full author record by Semantic Scholar ID."""
        if self._disabled:
            return None
        return self._get_safe(
            f"{SS_BASE}/author/{ss_author_id}",
            {"fields": AUTHOR_FIELDS},
        )

    def get_author_papers(self, ss_author_id: str, limit: int = 20) -> list[dict]:
        if self._disabled:
            return []
        data = self._get_safe(
            f"{SS_BASE}/author/{ss_author_id}/papers",
            {"fields": PAPER_FIELDS, "limit": limit},
        )
        if data is None:
            return []
        return data.get("data", [])

    def _lookup_by_orcid(self, orcid: str) -> Optional[dict]:
        """Try to resolve an author via ORCID string search."""
        if self._disabled:
            return None
        data = self._get_safe(
            f"{SS_BASE}/author/search",
            {"query": orcid, "fields": AUTHOR_FIELDS, "limit": 3},
        )
        if not data:
            return None
        for cand in data.get("data", []):
            papers = cand.get("papers") or []
            for p in papers:
                ext = p.get("externalIds") or {}
                if orcid.replace("https://orcid.org/", "") in str(ext):
                    return cand
        return None

    def verify_author_identity(
        self,
        name: str,
        institution: str,
        target_topics: list[str],
        orcid: Optional[str] = None,
    ) -> dict:
        """
        Cross-verify an author against Semantic Scholar.
        Returns _unverified() immediately if the client is disabled (rate-limited).

        Scoring breakdown (max 1.0):
          name_score:        0.0–0.40  (exact/substring name match)
          institution_score: 0.0–0.35  (token overlap)
          topic_score:       0.0–0.25  (fieldsOfStudy + paper title keywords)
        """
        if self._disabled:
            return self._unverified()

        # ORCID fast-path
        if orcid:
            orcid_match = self._lookup_by_orcid(orcid)
            if orcid_match:
                logger.debug("SS ORCID match: %s → %s", name, orcid_match.get("authorId"))
                return self._build_result(orcid_match, target_topics, score=0.95)

        # Name search
        candidates = self.search_author(name, limit=10)
        if not candidates:
            return self._unverified()

        best = None
        best_score = 0.0

        for cand in candidates:
            score = 0.0

            # Name sub-score (0–0.40)
            cand_name = (cand.get("name") or "").lower()
            name_lower = name.lower()
            if cand_name == name_lower:
                score += 0.40
            elif name_lower in cand_name or cand_name in name_lower:
                score += 0.30
            else:
                name_tokens = set(name_lower.split())
                cand_tokens = set(cand_name.split())
                shared_t = name_tokens & cand_tokens
                if shared_t:
                    score += 0.20 * len(shared_t) / max(len(name_tokens), 1)

            # Institution sub-score (0–0.35)
            raw_affiliations = cand.get("affiliations") or []
            aff_strings: list[str] = []
            for a in raw_affiliations:
                if isinstance(a, dict):
                    aff_strings.append(a.get("name") or "")
                elif isinstance(a, str):
                    aff_strings.append(a)

            if institution and aff_strings:
                best_inst = max(_institution_overlap(institution, aff) for aff in aff_strings)
                score += 0.35 * best_inst

            # Topic sub-score (0–0.25)
            papers = cand.get("papers") or []
            cand_fields: set[str] = set()
            cand_title_words: set[str] = set()
            for p in papers[:25]:
                for f in p.get("fieldsOfStudy") or []:
                    cand_fields.add(f.lower())
                title = (p.get("title") or "").lower()
                cand_title_words.update(w for w in title.split() if len(w) > 4)

            if target_topics:
                field_hits = sum(
                    1 for t in target_topics if any(t.lower() in f for f in cand_fields)
                )
                title_hits = sum(
                    1 for t in target_topics
                    if any(w in cand_title_words for w in t.lower().split() if len(w) > 4)
                )
                topic_score = min(
                    (field_hits + 0.5 * title_hits) / max(len(target_topics), 1), 1.0
                )
                score += 0.25 * topic_score

            if score > best_score:
                best_score = score
                best = cand

        if not best or best_score < MIN_MATCH_SCORE:
            return self._unverified()

        return self._build_result(best, target_topics, score=best_score)

    def _build_result(self, cand: dict, target_topics: list[str], score: float) -> dict:
        """Build verified result dict from a matched SS candidate."""
        papers = cand.get("papers") or []
        fields_set: set[str] = set()
        recent_titles: list[str] = []
        for p in papers[:20]:
            for f in (p.get("fieldsOfStudy") or []):
                fields_set.add(f)
            if p.get("title"):
                recent_titles.append(p["title"])

        if target_topics and fields_set:
            overlap_count = sum(
                1 for t in target_topics
                if any(t.lower() in f.lower() for f in fields_set)
            )
            topic_overlap = min(overlap_count / max(len(target_topics), 1), 1.0)
        else:
            topic_overlap = 0.5

        return {
            "verified": True,
            "ss_id": cand.get("authorId"),
            "confidence": round(score, 3),
            "h_index": cand.get("hIndex", 0),
            "citation_count": cand.get("citationCount", 0),
            "topic_overlap": topic_overlap,
            "fields_of_study": list(fields_set)[:10],
            "recent_papers": recent_titles[:10],
        }

    def _unverified(self) -> dict:
        return {
            "verified": False,
            "ss_id": None,
            "confidence": 0.0,
            "h_index": 0,
            "citation_count": 0,
            "topic_overlap": 0.0,
            "fields_of_study": [],
            "recent_papers": [],
        }


class _RateLimitError(Exception):
    """Raised by _get() on HTTP 429 to bypass tenacity retry logic."""