"""
Faculty verifier.
Determines whether a researcher is a supervising-eligible faculty member.

Rejects:
- PhD students, postdocs, research assistants, junior researchers
- Industry-only researchers (Google, OpenAI, etc.) UNLESS title confirms faculty/PI

Accepts:
- Professors, Associate/Assistant Professors, PIs, Readers, Lecturers
- Senior Scientists at universities
- Industry researchers WITH explicit professor/PI/adjunct title

Strategy:
1. Hard reject negative title patterns (PhD student, postdoc, etc.)
2. Hard reject pure-industry institutions unless overridden by explicit faculty title
3. Positive title match → high confidence
4. Heuristic: h-index + works count for cases with no title data
5. Faculty confidence score 0.0–1.0; threshold 0.30 for eligibility
"""

import re
from typing import Optional
from app.schemas.researcher import ResearcherCandidate
from app.utils.logging import get_logger

logger = get_logger(__name__)

# --- Positive title patterns (supervising-eligible) ---
POSITIVE_PATTERNS = [
    r"\bprofessor\b",
    r"\bassociate professor\b",
    r"\bassistant professor\b",
    r"\bfull professor\b",
    r"\bprincipal investigator\b",
    r"\bpi\b",
    r"\bfaculty\b",
    r"\blecturer\b",
    r"\breader\b",
    r"\bchair\b",
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bsenior research scientist\b",
    r"\bresearch professor\b",
    r"\bsenior scientist\b",
    r"\bstaff scientist\b",
    r"\bgroup leader\b",
    r"\binvestigator\b",
    r"\bsenior lecturer\b",
    r"\badjunct\b",
    r"\btenured\b",
    r"\btenure[- ]track\b",
]

# --- Negative title patterns (not eligible to supervise) ---
NEGATIVE_PATTERNS = [
    r"\bphd (student|candidate|fellow)\b",
    r"\bdoctoral (student|candidate|researcher|fellow)\b",
    r"\bgraduate student\b",
    r"\bpostdoc(toral)?\b",
    r"\bpost-doc(toral)?\b",
    r"\bphd researcher\b",
    r"\bresearch assistant\b",
    r"\bjunior researcher\b",
    r"\bvisiting (student|scholar|researcher)\b",
    r"\bphd program\b",
    r"\benrolled in\b",
    r"\bcurrently pursuing\b",
    r"\bundergraduate\b",
    r"\bmaster'?s? student\b",
    r"\bmsca (fellow|postdoc)\b",
    r"\bnih f3[12]\b",
    r"\bukri studentship\b",
    r"\bintern\b",
    r"\bresearch trainee\b",
]

# --- Industry institutions that cannot supervise PhDs ---
# Researchers at these orgs are rejected UNLESS their title explicitly
# contains a faculty/PI marker (adjunct professor, visiting professor, etc.)
INDUSTRY_ORGS: set[str] = {
    "google",
    "google deepmind",
    "deepmind",
    "nvidia",
    "openai",
    "meta",
    "meta ai",
    "facebook",
    "microsoft research",
    "microsoft",
    "amazon",
    "aws",
    "apple",
    "ibm research",
    "ibm",
    "adobe research",
    "adobe",
    "salesforce research",
    "baidu research",
    "tencent",
    "alibaba",
    "bytedance",
    "huawei",
    "samsung research",
    "intel labs",
    "qualcomm",
    "bloomberg",
    "jpmorgan",
    "goldman sachs",
    "two sigma",
    "d. e. shaw",
}

# Industry org override: if title contains any of these, allow even for industry orgs
INDUSTRY_OVERRIDE_TITLES: list[str] = [
    "professor",
    "associate professor",
    "assistant professor",
    "adjunct professor",
    "visiting professor",
    "faculty",
    "principal investigator",
    " pi ",
    "research fellow",  # senior fellowship (not MSCA postdoc)
    "distinguished scientist",  # very senior
    "fellow",  # ACM/IEEE Fellow etc. at industry → can often co-supervise
]

# Heuristic thresholds
MIN_H_INDEX_FOR_FACULTY = 5
MIN_WORKS_FOR_FACULTY = 10
STRONG_H_INDEX = 20      # strong signal even without title
EXCELLENT_H_INDEX = 35   # near-certain faculty


class FacultyVerifier:
    def __init__(self) -> None:
        self._pos_re = [re.compile(p, re.IGNORECASE) for p in POSITIVE_PATTERNS]
        self._neg_re = [re.compile(p, re.IGNORECASE) for p in NEGATIVE_PATTERNS]

    def _is_industry_org(self, institution: str) -> bool:
        """Return True if institution name matches a known non-academic org."""
        inst_lower = (institution or "").lower()
        return any(org in inst_lower for org in INDUSTRY_ORGS)

    def _has_industry_override_title(self, title_text: str) -> bool:
        """Return True if title explicitly qualifies an industry researcher as faculty-like."""
        t = title_text.lower()
        return any(override in t for override in INDUSTRY_OVERRIDE_TITLES)

    def compute_faculty_confidence(
        self,
        candidate: ResearcherCandidate,
        extra_title_text: Optional[str] = None,
    ) -> tuple[float, str]:
        """
        Returns (confidence_score 0.0–1.0, detected_title_string).
        Higher = more likely to be a supervising-eligible faculty member.
        """
        title_text = " ".join(
            filter(None, [candidate.raw_title, extra_title_text])
        ).lower()

        # ------------------------------------------------------------------
        # 1. Hard reject on negative title patterns
        # ------------------------------------------------------------------
        for pat in self._neg_re:
            if pat.search(title_text):
                logger.debug(
                    "Rejecting %s — negative pattern: %s", candidate.name, pat.pattern
                )
                return 0.0, f"rejected:{pat.pattern}"

        # ------------------------------------------------------------------
        # 2. Industry institution check
        #    Reject industry-only researchers unless title confirms faculty/PI
        # ------------------------------------------------------------------
        institution = candidate.institution or ""
        if self._is_industry_org(institution):
            if self._has_industry_override_title(title_text):
                # Industry researcher with explicit faculty/PI title — allow with penalty
                logger.debug(
                    "%s @ %s: industry org but title qualifies ('%s')",
                    candidate.name, institution, title_text[:60],
                )
                # Will be scored below; cap at 0.65 (not as trustworthy as pure academia)
                industry_cap = 0.65
            else:
                logger.debug(
                    "Rejecting %s @ %s — industry org without faculty title",
                    candidate.name, institution,
                )
                return 0.0, f"rejected:industry:{institution}"
        else:
            industry_cap = 1.0  # no cap for academic institutions

        # ------------------------------------------------------------------
        # 3. Positive title match
        # ------------------------------------------------------------------
        positive_score = 0.0
        matched_title = ""
        for pat in self._pos_re:
            if pat.search(title_text):
                positive_score = 1.0
                matched_title = pat.pattern
                break

        # ------------------------------------------------------------------
        # 4. Heuristic: publication metrics
        # ------------------------------------------------------------------
        heuristic_score = 0.0
        if candidate.h_index >= MIN_H_INDEX_FOR_FACULTY:
            heuristic_score += 0.35
        if candidate.h_index >= STRONG_H_INDEX:
            heuristic_score += 0.20
        if candidate.h_index >= EXCELLENT_H_INDEX:
            heuristic_score += 0.15
        if candidate.total_works >= MIN_WORKS_FOR_FACULTY:
            heuristic_score += 0.15
        if candidate.cited_by_count >= 500:
            heuristic_score += 0.10
        if candidate.cited_by_count >= 2000:
            heuristic_score += 0.05
        heuristic_score = min(heuristic_score, 0.85)

        # ------------------------------------------------------------------
        # 5. Combine scores
        # ------------------------------------------------------------------
        if positive_score > 0:
            # Title confirmed → base 0.55 + heuristic contribution
            confidence = 0.55 + 0.45 * heuristic_score
            if candidate.h_index >= STRONG_H_INDEX:
                confidence = min(confidence + 0.05, 1.0)
        elif title_text.strip():
            # Title present but unrecognised → cautious
            confidence = heuristic_score * 0.55
        else:
            # No title — rely entirely on metrics
            confidence = heuristic_score

        # Apply industry cap
        confidence = min(confidence, industry_cap)

        # Hard reject if evidence too thin
        if confidence < 0.20 and candidate.total_works < MIN_WORKS_FOR_FACULTY:
            return 0.0, "insufficient_evidence"

        return round(confidence, 3), matched_title or "heuristic"

    def is_eligible(self, confidence: float, threshold: float = 0.30) -> bool:
        return confidence >= threshold