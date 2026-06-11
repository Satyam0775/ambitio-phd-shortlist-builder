"""
Domain verifier — ensures a researcher genuinely works in the student's area.

Key failure modes addressed:
- Wrong-domain keyword overlap (biodegradable plastic != biomaterials)
- Discipline leakage (humanities vs STEM vs medical)
- Region/ecosystem label collisions

Strategy:
1. Concept overlap: match researcher OpenAlex concepts against student interests
   using both substring and individual word matching.
2. Keyword hit score: count student terms found in researcher text.
3. Title relevance bonus: reward researchers whose paper titles directly match
   student keywords (strong signal of genuine alignment).
4. Discipline guard: apply cross-bucket penalty when disciplines clearly mismatch.
5. Adaptive weighting: if paper titles available, weight them more heavily.

Score 0.0–1.0. Below threshold = reject.
"""

import re
from typing import Optional
from app.utils.logging import get_logger

logger = get_logger(__name__)

DISCIPLINE_BUCKETS: dict[str, list[str]] = {
    "humanities": [
        "literature", "history", "philosophy", "linguistics", "arts",
        "classical", "antiquity", "roman", "medieval", "poetry",
        "archaeology", "anthropology", "cultural studies", "museum",
        "archive", "manuscript", "rhetoric", "grief", "trauma studies",
        "literary", "narrative", "hermeneutics",
    ],
    "stem": [
        "machine learning", "deep learning", "neural network", "algorithm",
        "molecular", "genomics", "proteomics", "chemistry", "physics",
        "mathematics", "engineering", "robotics", "materials science",
        "quantum", "biotechnology", "nanotechnology", "semiconductor",
        "photonics", "thermodynamics", "ecology", "epidemiology",
        "computational", "bioinformatics", "signal processing",
        "natural language processing", "nlp", "transformer", "bert",
        "computer science", "information retrieval", "text mining",
        "clinical informatics", "biomedical informatics",
    ],
    "medical": [
        "clinical", "medicine", "surgery", "oncology", "cardiology",
        "neurology", "psychiatry", "therapy", "diagnosis", "treatment",
        "patient", "hospital", "drug", "pharmacology", "immunology",
        "pathology", "radiology", "nursing", "healthcare",
        "electronic health record", "ehr", "clinical notes",
    ],
    "social_science": [
        "economics", "sociology", "political science", "psychology",
        "education", "law", "policy", "management", "business",
        "public health", "demography", "urban", "geography",
    ],
}


def _bucket_scores(text: str) -> dict[str, float]:
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for bucket, terms in DISCIPLINE_BUCKETS.items():
        hits = sum(1 for t in terms if t in text_lower)
        scores[bucket] = hits / max(len(terms), 1)
    return scores


def _dominant_bucket(text: str) -> Optional[str]:
    scores = _bucket_scores(text)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 0.01:
        return None
    return best


class DomainVerifier:
    def __init__(self) -> None:
        pass

    def compute_domain_match(
        self,
        researcher_concepts: list[str],
        researcher_paper_titles: list[str],
        student_interests: list[str],
        student_keywords: list[str],
    ) -> float:
        """
        Returns a domain match score 0.0–1.0.

        Scoring components:
        - concept_score:    substring/word overlap between researcher concepts
                            and student interest terms (both directions).
        - keyword_score:    count of student terms found in researcher full text
                            (concepts + paper titles combined).
        - title_bonus:      dedicated score from paper title matching; rewards
                            researchers whose papers explicitly use student keywords.
        - discipline_penalty: cross-bucket multiplier that near-zeros out clear
                            discipline mismatches (humanities vs STEM, etc.).

        Adaptive weighting:
        - If paper titles available: 0.35 concept + 0.35 keyword + 0.30 title_bonus
        - If only concepts available: concept_score directly (no title penalty)
        """
        if not student_interests and not student_keywords:
            return 0.5

        all_student_terms = list(dict.fromkeys(student_interests + student_keywords))

        researcher_text = " ".join(
            researcher_concepts + researcher_paper_titles
        ).lower()
        student_text = " ".join(all_student_terms).lower()

        # 1. Concept overlap (bi-directional substring + word matching)
        concept_score = self._concept_overlap(researcher_concepts, all_student_terms)

        # 2. Full-text keyword hit score
        keyword_score = self._keyword_hit_score(researcher_text, all_student_terms)

        # 3. Paper title relevance bonus (strong signal when available)
        title_bonus = self._title_relevance_score(researcher_paper_titles, all_student_terms)

        # 4. Discipline penalty
        discipline_penalty = self._discipline_penalty(researcher_text, student_text)

        # Adaptive weighting
        if researcher_paper_titles:
            raw = 0.35 * concept_score + 0.35 * keyword_score + 0.30 * title_bonus
        else:
            # No paper titles — rely on concepts, with keyword as secondary
            raw = 0.6 * concept_score + 0.4 * keyword_score

        final = raw * discipline_penalty

        logger.debug(
            "Domain match: concept=%.3f kw=%.3f title=%.3f penalty=%.2f has_papers=%s → %.3f",
            concept_score, keyword_score, title_bonus,
            discipline_penalty, bool(researcher_paper_titles), final,
        )
        return round(min(final, 1.0), 3)

    def _concept_overlap(
        self, researcher_concepts: list[str], student_terms: list[str]
    ) -> float:
        """
        Bi-directional substring match + individual word match.
        Partial credit (0.5) for word-level overlap in multi-word terms.
        """
        if not researcher_concepts or not student_terms:
            return 0.0

        rc_lower = [c.lower() for c in researcher_concepts]
        rc_set = set(rc_lower)
        hits = 0.0

        for s in student_terms:
            s_lower = s.lower()

            # Direct substring match (both directions)
            if any(s_lower in r or r in s_lower for r in rc_set):
                hits += 1.0
                continue

            # Word-level match: any significant word (>3 chars) in any concept
            s_words = [w for w in s_lower.split() if len(w) > 3]
            if s_words and any(w in r for w in s_words for r in rc_lower):
                hits += 0.5  # partial credit

        return min(hits / max(len(student_terms), 1), 1.0)

    def _keyword_hit_score(self, researcher_text: str, student_terms: list[str]) -> float:
        """Count how many student terms appear in the combined researcher text."""
        if not student_terms or not researcher_text:
            return 0.0
        hits = sum(1 for t in student_terms if t.lower() in researcher_text)
        return min(hits / max(len(student_terms), 1), 1.0)

    def _title_relevance_score(
        self, paper_titles: list[str], student_terms: list[str]
    ) -> float:
        """
        Score based on how many student terms appear in paper titles.
        This is a stronger signal than concept overlap because paper titles
        are precise and directly reflect what was researched.
        Uses max-over-titles: a researcher needs at least ONE highly relevant
        paper, not all papers to be relevant.
        """
        if not paper_titles or not student_terms:
            return 0.0

        per_title_scores: list[float] = []
        for title in paper_titles[:15]:
            title_lower = (title or "").lower()
            hits = sum(1 for t in student_terms if t.lower() in title_lower)
            per_title_scores.append(hits / max(len(student_terms), 1))

        if not per_title_scores:
            return 0.0

        # Blend: best title (50%) + average of top-3 (50%)
        best = max(per_title_scores)
        top3_avg = sum(sorted(per_title_scores, reverse=True)[:3]) / 3
        return min(0.5 * best + 0.5 * top3_avg, 1.0) * 3  # scale up — title hits are rare but meaningful

    def _discipline_penalty(self, researcher_text: str, student_text: str) -> float:
        """
        Returns a multiplier (0.05–1.0).
        Humanities vs STEM is near-zero. Other cross-bucket mismatches are softer.
        """
        researcher_bucket = _dominant_bucket(researcher_text)
        student_bucket = _dominant_bucket(student_text)

        if researcher_bucket is None or student_bucket is None:
            return 1.0

        if researcher_bucket == student_bucket:
            return 1.0

        penalty_map = {
            ("humanities", "stem"): 0.05,
            ("humanities", "medical"): 0.08,
            ("humanities", "social_science"): 0.5,
            ("stem", "humanities"): 0.05,
            ("stem", "social_science"): 0.6,
            ("medical", "humanities"): 0.08,
            ("medical", "stem"): 0.85,   # medical+stem often co-occur (biomedical NLP etc.)
            ("stem", "medical"): 0.85,
            ("social_science", "humanities"): 0.5,
            ("social_science", "stem"): 0.6,
            ("social_science", "medical"): 0.7,
        }
        penalty = penalty_map.get((researcher_bucket, student_bucket), 0.3)
        logger.debug(
            "Discipline mismatch: researcher=%s student=%s penalty=%.2f",
            researcher_bucket, student_bucket, penalty,
        )
        return penalty

    def passes_domain_threshold(self, score: float, threshold: float = 0.25) -> bool:
        return score >= threshold