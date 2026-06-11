"""
Ranking engine.

Score formula (see settings for weights):
  interest_match       = 40%  (FAISS cosine similarity)
  publication_relevance = 25%  (domain_match_score × paper quality)
  faculty_confidence   = 15%  (faculty verifier output)
  country_match        = 10%  (1.0 if in target countries, else 0 — should be pre-filtered)
  recent_activity      = 10%  (recency of publications)

Tier classification:
  reach  : score >= reach_threshold (default 0.75)
  target : score >= target_lower    (default 0.50)
  safety : score >= safety_threshold (default 0.30)
  Below safety_threshold → excluded from final output.
"""

import math
import logging
from datetime import datetime
from typing import Optional

from app.config.settings import settings
from app.schemas.researcher import VerifiedResearcher
from app.utils.logging import get_logger

logger = get_logger(__name__)

CURRENT_YEAR = datetime.utcnow().year

# Exclude researchers whose last publication is older than this.
# Inactive labs create shortlist contamination — they rarely respond.
MAX_INACTIVE_YEARS = 5


class RankingEngine:
    def __init__(self, feedback_adjuster=None) -> None:
        self.s = settings
        self.feedback_adjuster = feedback_adjuster  # optional feedback learning module

    def compute_score(
        self,
        researcher: VerifiedResearcher,
        interest_match_score: float,  # from FAISS / embedding similarity
        target_country_codes: set[str],
    ) -> float:
        """Compute composite ranking score."""

        # 1. Interest match (already 0-1 from cosine similarity)
        interest_match = max(0.0, min(interest_match_score, 1.0))

        # 2. Publication relevance
        pub_relevance = self._publication_relevance(researcher)

        # 3. Faculty confidence
        faculty_conf = max(0.0, min(researcher.faculty_confidence, 1.0))

        # 4. Country match (should always be 1.0 post-filter, but guard here)
        country_match = 1.0 if researcher.country_code in target_country_codes else 0.0

        # 5. Recent activity
        recent_activity = self._recent_activity_score(researcher)

        # Weighted sum
        score = (
            self.s.weight_interest_match * interest_match
            + self.s.weight_publication_relevance * pub_relevance
            + self.s.weight_faculty_confidence * faculty_conf
            + self.s.weight_country_match * country_match
            + self.s.weight_recent_activity * recent_activity
        )

        # Apply feedback adjustment if available
        if self.feedback_adjuster:
            adj = self.feedback_adjuster.get_adjustment(
                researcher.openalex_id, researcher.institution
            )
            score = max(0.0, min(score * adj, 1.0))

        logger.debug(
            "%s | interest=%.2f pub=%.2f faculty=%.2f country=%.1f recent=%.2f → %.3f",
            researcher.name,
            interest_match, pub_relevance, faculty_conf, country_match, recent_activity,
            score,
        )
        return round(score, 4)

    def _publication_relevance(self, researcher: VerifiedResearcher) -> float:
        """Score based on domain match, h-index, citation count, and paper quality."""
        domain = researcher.domain_match_score

        # H-index signal (log-scale, 20 = good, 40 = excellent)
        h_score = min(math.log1p(researcher.h_index) / math.log1p(40), 1.0)

        # Citation count signal
        cit_score = min(math.log1p(researcher.total_citations) / math.log1p(10000), 1.0)

        # Paper relevance (average relevance of top papers)
        paper_rel = 0.5
        if researcher.papers:
            top_papers = sorted(
                researcher.papers, key=lambda p: p.relevance_score, reverse=True
            )[:5]
            scored = [p for p in top_papers if p.relevance_score > 0]
            if scored:
                paper_rel = sum(p.relevance_score for p in scored) / len(scored)

        return round(
            0.5 * domain + 0.25 * h_score + 0.15 * cit_score + 0.10 * paper_rel, 3
        )

    def _recent_activity_score(self, researcher: VerifiedResearcher) -> float:
        """Higher score for more recently active researchers."""
        if not researcher.most_recent_paper_year:
            return 0.3  # unknown — neutral-low
        years_ago = CURRENT_YEAR - researcher.most_recent_paper_year
        if years_ago <= 1:
            return 1.0
        elif years_ago <= 2:
            return 0.85
        elif years_ago <= 3:
            return 0.70
        elif years_ago <= 5:
            return 0.50
        elif years_ago <= 8:
            return 0.25
        else:
            return 0.10

    def assign_tier(self, score: float) -> str:
        if score >= self.s.reach_threshold:
            return "reach"
        elif score >= self.s.target_lower:
            return "target"
        elif score >= self.s.safety_threshold:
            return "safety"
        else:
            return "exclude"

    def _is_inactive(self, researcher: VerifiedResearcher) -> bool:
        """True if last known publication is more than MAX_INACTIVE_YEARS ago."""
        if researcher.most_recent_paper_year is None:
            return False  # unknown — do not exclude on missing data
        return (CURRENT_YEAR - researcher.most_recent_paper_year) > MAX_INACTIVE_YEARS

    def rank_and_filter(
        self,
        researchers: list[VerifiedResearcher],
        interest_match_scores: dict[str, float],
        target_country_codes: set[str],
        max_output: int = 150,
    ) -> list[tuple[VerifiedResearcher, float, str]]:
        """
        Compute scores for all researchers, tier-classify, and return
        sorted list of (researcher, score, tier) — excluding 'exclude' tier.
        """
        scored: list[tuple[VerifiedResearcher, float, str]] = []
        excluded_faculty = 0
        excluded_inactive = 0

        for researcher in researchers:
            # Hard gate 1: faculty confidence — catches Step 8 relaxation pass-through
            if researcher.faculty_confidence < 0.15:
                excluded_faculty += 1
                logger.debug(
                    "Ranking skip %s: faculty_confidence=%.2f too low",
                    researcher.name, researcher.faculty_confidence,
                )
                continue

            # Hard gate 2: recency — inactive labs rarely respond to cold emails
            if self._is_inactive(researcher):
                excluded_inactive += 1
                logger.debug(
                    "Ranking skip %s: last pub %s (>%d yrs ago)",
                    researcher.name, researcher.most_recent_paper_year, MAX_INACTIVE_YEARS,
                )
                continue

            sim_score = interest_match_scores.get(researcher.openalex_id, 0.3)
            score = self.compute_score(researcher, sim_score, target_country_codes)
            tier = self.assign_tier(score)
            if tier != "exclude":
                scored.append((researcher, score, tier))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)

        # Enforce diversity: cap per-institution at 5
        institution_counts: dict[str, int] = {}
        filtered: list[tuple[VerifiedResearcher, float, str]] = []
        for item in scored:
            inst = item[0].institution
            count = institution_counts.get(inst, 0)
            if count >= 5:
                continue
            institution_counts[inst] = count + 1
            filtered.append(item)
            if len(filtered) >= max_output:
                break

        logger.info(
            "Ranked %d researchers → %d after tier filter + diversity cap "
            "(excl_faculty=%d excl_inactive=%d)",
            len(researchers), len(filtered), excluded_faculty, excluded_inactive,
        )
        return filtered