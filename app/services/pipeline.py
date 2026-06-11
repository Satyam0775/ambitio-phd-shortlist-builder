"""
Main pipeline orchestrator.
Runs all 13 steps end-to-end.
"""

import time
import logging
from typing import Optional
from datetime import datetime

from app.config.settings import settings
from app.schemas.student import StudentProfile
from app.schemas.researcher import ResearcherCandidate, VerifiedResearcher, PaperEvidence
from app.schemas.output import ShortlistOutput, SupervisorRecommendation, EvidenceBlock
from app.retrieval.openalex_client import OpenAlexClient
from app.retrieval.keyword_generator import KeywordGenerator
from app.verification.semantic_scholar_client import SemanticScholarClient
from app.verification.faculty_verifier import FacultyVerifier
from app.verification.domain_verifier import DomainVerifier
from app.embeddings.embedding_service import EmbeddingService
from app.embeddings.faiss_service import FAISSService
from app.ranking.ranking_engine import RankingEngine
from app.services.gemini_service import GeminiService
from app.utils.country_map import get_target_codes, ISO2_TO_NAME
from app.utils.text import (
    build_researcher_text_blob,
    build_student_text_blob,
    normalize_name,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Minimum raw candidates before triggering expanded search
MIN_CANDIDATE_TARGET = 100

# Domain threshold: 0.08 retains good recall while blocking clear mismatches.
# At threshold=0.05 with 2115 candidates, 178 passed — reasonable for biomedical NLP.
# Raising to 0.08 tightens quality without starving the ranking step.
DOMAIN_THRESHOLD = 0.15

# Faculty threshold for eligibility
FACULTY_THRESHOLD = 0.25

# SS verification cap.
# 30 candidates × ~1–2s each ≈ 30–60s. Avoids repeated 429 sleeping.
MAX_TO_VERIFY = 30


class Pipeline:
    def __init__(self) -> None:
        self.openalex = OpenAlexClient()
        self.ss_client = SemanticScholarClient()
        self.faculty_verifier = FacultyVerifier()
        self.domain_verifier = DomainVerifier()
        self.embedding_service = EmbeddingService()
        self.faiss_service = FAISSService()
        self.gemini = GeminiService()
        self.keyword_generator = KeywordGenerator(gemini_service=self.gemini)

    def _add_candidates(
        self,
        raw_authors: list[dict],
        seen_oa_ids: set[str],
        raw_candidates: list[ResearcherCandidate],
    ) -> int:
        """Parse and deduplicate author dicts into candidates. Returns count added."""
        added = 0
        for a in raw_authors:
            cand = self.openalex.parse_author_to_candidate(a)
            if cand and cand.openalex_id not in seen_oa_ids:
                seen_oa_ids.add(cand.openalex_id)
                raw_candidates.append(cand)
                added += 1
        return added

    def run(self, profile: StudentProfile, feedback_adjuster=None) -> ShortlistOutput:
        start_time = time.time()
        logger.info("=" * 60)
        logger.info("Starting pipeline for student: %s (%s)", profile.name, profile.student_id)
        logger.info("=" * 60)

        ranking_engine = RankingEngine(feedback_adjuster=feedback_adjuster)

        # ----------------------------------------------------------------
        # STEP 2: Normalize research interests via Gemini
        # ----------------------------------------------------------------
        logger.info("[STEP 2] Normalizing research interests via Gemini...")
        profile_summary = self._build_profile_summary(profile)
        try:
            normalized_interests = self.gemini.normalize_interests(
                profile.research_interests, profile_summary
            )
            logger.info("Normalized interests: %s", normalized_interests)
        except Exception as exc:
            logger.warning("Gemini interest normalization failed: %s", exc)
            normalized_interests = profile.research_interests

        # ----------------------------------------------------------------
        # STEP 3: Generate retrieval keywords
        # ----------------------------------------------------------------
        logger.info("[STEP 3] Generating retrieval keywords...")
        keyword_data = self.keyword_generator.generate(profile)
        primary_keywords = keyword_data.get("primary_keywords", [])
        concept_terms = keyword_data.get("concept_terms", [])
        all_keywords = list(dict.fromkeys(normalized_interests + primary_keywords))

        logger.info("Primary keywords (%d): %s", len(primary_keywords), primary_keywords)
        logger.info("Concept terms (%d): %s", len(concept_terms), concept_terms)
        logger.info("All keywords combined (%d): %s", len(all_keywords), all_keywords)

        # ----------------------------------------------------------------
        # STEP 4: Query OpenAlex (works-based primary retrieval)
        # ----------------------------------------------------------------
        logger.info("[STEP 4] Querying OpenAlex...")
        target_codes = get_target_codes(profile.target_countries)
        logger.info(
            "Target country codes: %s (from %s)", target_codes, profile.target_countries
        )
        if not target_codes:
            logger.warning("No valid target country codes — proceeding without.")

        raw_candidates: list[ResearcherCandidate] = []
        seen_oa_ids: set[str] = set()

        # --- 4a. PRIMARY: Works-based bulk retrieval ---
        logger.info("[STEP 4a] Works-based author retrieval...")
        all_retrieval_keywords = list(
            dict.fromkeys(all_keywords + concept_terms + normalized_interests)
        )
        logger.info(
            "  Retrieval keywords (%d): %s",
            len(all_retrieval_keywords), all_retrieval_keywords,
        )

        raw_author_dicts = self.openalex.retrieve_authors_via_works(
            keywords=all_retrieval_keywords,
            country_codes=target_codes,
            per_page=50,
            max_pages=4,
            min_candidates=MIN_CANDIDATE_TARGET,
        )
        added = self._add_candidates(raw_author_dicts, seen_oa_ids, raw_candidates)
        logger.info("[STEP 4a] Works-based retrieval: %d unique candidates", added)

        # --- 4b. SECONDARY: Direct keyword author search ---
        if len(raw_candidates) < MIN_CANDIDATE_TARGET:
            logger.info(
                "[STEP 4b] Supplementing: have %d, need %d...",
                len(raw_candidates), MIN_CANDIDATE_TARGET,
            )
            for kw in all_keywords[:10]:
                if len(raw_candidates) >= MIN_CANDIDATE_TARGET:
                    break
                authors = self.openalex.search_authors_by_keyword(
                    kw, target_codes, per_page=50, max_pages=3,
                    use_country_filter=bool(target_codes),
                )
                new = self._add_candidates(authors, seen_oa_ids, raw_candidates)
                if new:
                    logger.info("    '%s' +%d (total: %d)", kw, new, len(raw_candidates))

                if not authors and target_codes:
                    authors = self.openalex.search_authors_by_keyword(
                        kw, target_codes, per_page=50, max_pages=2,
                        use_country_filter=False,
                    )
                    new = self._add_candidates(authors, seen_oa_ids, raw_candidates)
                    if new:
                        logger.info(
                            "    '%s' (no country) +%d (total: %d)", kw, new, len(raw_candidates)
                        )
        else:
            logger.info("[STEP 4b] Skip — already have %d candidates", len(raw_candidates))

        # --- 4c. ENRICHMENT: Concept/topic-based (deprecated x_concepts, best-effort) ---
        if len(raw_candidates) < MIN_CANDIDATE_TARGET:
            logger.info(
                "[STEP 4c] Concept enrichment: have %d, need %d...",
                len(raw_candidates), MIN_CANDIDATE_TARGET,
            )
            for term in (concept_terms[:5] or all_keywords[:3]):
                if len(raw_candidates) >= MIN_CANDIDATE_TARGET:
                    break
                concepts_found = self.openalex.concept_search(term)
                for concept in concepts_found[:2]:
                    cid = concept.get("id", "")
                    if not cid:
                        continue
                    authors = self.openalex.search_authors_by_concept(
                        cid, target_codes, per_page=50, max_pages=2,
                        use_country_filter=bool(target_codes),
                    )
                    if authors:
                        new = self._add_candidates(authors, seen_oa_ids, raw_candidates)
                        logger.info(
                            "    Concept '%s' +%d (total: %d)",
                            concept.get("display_name", ""), new, len(raw_candidates),
                        )
        else:
            logger.info("[STEP 4c] Skip — have %d candidates", len(raw_candidates))

        # --- 4d. Safety net: expand without country filter ---
        if len(raw_candidates) < MIN_CANDIDATE_TARGET:
            logger.warning(
                "[STEP 4d] SAFETY NET: only %d — expanding without country filter...",
                len(raw_candidates),
            )
            broader = list(dict.fromkeys(
                normalized_interests + primary_keywords + concept_terms
            ))
            for kw in broader[:15]:
                if len(raw_candidates) >= MIN_CANDIDATE_TARGET:
                    break
                works = self.openalex.search_works_by_keyword(
                    kw, set(), per_page=100, max_pages=4, use_country_filter=False,
                )
                if works:
                    extracted = self.openalex.extract_all_authors_from_works(works)
                    new = self._add_candidates(extracted, seen_oa_ids, raw_candidates)
                    logger.info("  Safety '%s' +%d (total: %d)", kw, new, len(raw_candidates))
            logger.info("[STEP 4d] After expansion: %d", len(raw_candidates))

        logger.info("[STEP 4 COMPLETE] Total raw candidates: %d", len(raw_candidates))

        # ----------------------------------------------------------------
        # STEP 5: Country filtering (hard constraint)
        # ----------------------------------------------------------------
        logger.info("[STEP 5] Country filter (target: %s)...", target_codes)
        if target_codes:
            country_filtered = [
                c for c in raw_candidates if c.country_code in target_codes
            ]
            # Guard: if filter is too aggressive, include unknown-country candidates
            if len(country_filtered) < MIN_CANDIDATE_TARGET and len(raw_candidates) >= MIN_CANDIDATE_TARGET:
                logger.warning(
                    "[STEP 5] Country filter: %d → %d. Including unknown-country candidates.",
                    len(raw_candidates), len(country_filtered),
                )
                country_filtered = [
                    c for c in raw_candidates
                    if c.country_code in target_codes or not c.country_code
                ]
        else:
            country_filtered = raw_candidates

        if not country_filtered and raw_candidates:
            logger.warning("[STEP 5] Country filter removed ALL — using unfiltered.")
            country_filtered = raw_candidates

        logger.info("[STEP 5] After country filter: %d / %d", len(country_filtered), len(raw_candidates))

        # ----------------------------------------------------------------
        # STEP 6: Domain verification
        # ----------------------------------------------------------------
        logger.info("[STEP 6] Domain verification (threshold=%.2f)...", DOMAIN_THRESHOLD)
        domain_passed: list[tuple[ResearcherCandidate, float]] = []
        domain_scores_log: list[tuple[str, str, float]] = []

        for cand in country_filtered:
            paper_titles = [p.title for p in cand.recent_papers if p.title]
            domain_score = self.domain_verifier.compute_domain_match(
                researcher_concepts=cand.concepts,
                researcher_paper_titles=paper_titles,
                student_interests=normalized_interests,
                student_keywords=primary_keywords,
            )
            domain_scores_log.append((cand.name, cand.institution or "", domain_score))
            if self.domain_verifier.passes_domain_threshold(domain_score, threshold=DOMAIN_THRESHOLD):
                domain_passed.append((cand, domain_score))

        # Log top 20 domain scores for transparency
        domain_scores_log.sort(key=lambda x: x[2], reverse=True)
        logger.info("[STEP 6] Top 20 domain scores:")
        for name, inst, score in domain_scores_log[:20]:
            logger.info("  %.3f | %s @ %s", score, name, inst)
        if len(domain_scores_log) > 20:
            logger.info("  ... (%d more) ...", len(domain_scores_log) - 20)

        logger.info(
            "[STEP 6] After domain filter: %d / %d (threshold=%.2f)",
            len(domain_passed), len(country_filtered), DOMAIN_THRESHOLD,
        )

        # Safety: if domain filter is too aggressive, take top N by score.
        # Cap at 30 to prioritize quality and research relevance over candidate volume.
        DOMAIN_FALLBACK_CAP = 30
        if len(domain_passed) < DOMAIN_FALLBACK_CAP and len(country_filtered) >= MIN_CANDIDATE_TARGET:
            logger.warning("[STEP 6] Too few passed — taking top %d by score.", DOMAIN_FALLBACK_CAP)
            name_inst_to_cand = {(c.name, c.institution or ""): c for c in country_filtered}
            domain_passed = []
            for name, inst, score in domain_scores_log[:DOMAIN_FALLBACK_CAP]:
                cand = name_inst_to_cand.get((name, inst))
                if cand:
                    domain_passed.append((cand, score))
            logger.info("[STEP 6] After relaxation: %d candidates", len(domain_passed))

        if not domain_passed and country_filtered:
            logger.warning("[STEP 6] Domain removed ALL — passing all through.")
            domain_passed = [(c, 0.01) for c in country_filtered]

        # ----------------------------------------------------------------
        # STEP 7: Works fetch + Semantic Scholar verification
        # ----------------------------------------------------------------
        logger.info("[STEP 7] Works fetch + Semantic Scholar verification...")
        verified_researchers: list[VerifiedResearcher] = []
        ss_verified_count = 0

        # Sort by domain score — strongest matches verified first.
        # Cap at MAX_TO_VERIFY to prevent repeated 429 sleeping.
        domain_passed.sort(key=lambda x: x[1], reverse=True)
        candidates_to_verify = domain_passed[:MAX_TO_VERIFY]
        logger.info(
            "[STEP 7] Verifying %d candidates (cap=%d)...",
            len(candidates_to_verify), MAX_TO_VERIFY,
        )

        for i, (cand, domain_score) in enumerate(candidates_to_verify):
            if i % 25 == 0:
                logger.info("  Verifying %d / %d...", i, len(candidates_to_verify))

            # Fetch recent works from OpenAlex
            works = self.openalex.get_author_works(cand.openalex_id, per_page=25, max_pages=1)
            papers = self.openalex.parse_works_to_papers(works)

            for paper in papers:
                paper.relevance_score = self._paper_relevance(
                    paper.title, normalized_interests + primary_keywords
                )

            most_recent_year = max((p.year for p in papers if p.year), default=None)

            # Extract topics from works
            topics_from_works: list[str] = []
            for w in works[:15]:
                for concept in w.get("concepts", [])[:3]:
                    dn = concept.get("display_name", "")
                    if dn and dn not in topics_from_works:
                        topics_from_works.append(dn)

            all_researcher_topics = list(
                dict.fromkeys(cand.concepts + topics_from_works)
            )[:20]

            # Re-score domain using actual fetched paper titles
            if papers:
                refined_domain = self.domain_verifier.compute_domain_match(
                    researcher_concepts=cand.concepts,
                    researcher_paper_titles=[p.title for p in papers],
                    student_interests=normalized_interests,
                    student_keywords=primary_keywords,
                )
                domain_score = max(domain_score, refined_domain)

            # Semantic Scholar verification
            ss_result = self.ss_client.verify_author_identity(
                name=cand.name,
                institution=cand.institution or "",
                target_topics=normalized_interests[:5],
                orcid=cand.orcid,
            )
            if ss_result["verified"]:
                ss_verified_count += 1
                domain_score = max(
                    domain_score,
                    domain_score * 0.7 + ss_result["topic_overlap"] * 0.3,
                )
                h_idx = max(cand.h_index, ss_result["h_index"])
                total_cit = max(cand.cited_by_count, ss_result["citation_count"])
            else:
                h_idx = cand.h_index
                total_cit = cand.cited_by_count

            country_name = ISO2_TO_NAME.get(cand.country_code, cand.country_code)

            vr = VerifiedResearcher(
                openalex_id=cand.openalex_id,
                semantic_scholar_id=ss_result.get("ss_id"),
                name=cand.name,
                institution=cand.institution or "Unknown",
                country_code=cand.country_code or "",
                country_name=country_name,
                email=cand.email,
                research_focus=all_researcher_topics[:10],
                papers=papers[:20],
                faculty_confidence=0.0,  # filled in step 8
                domain_match_score=domain_score,
                semantic_scholar_verified=ss_result["verified"],
                total_citations=total_cit,
                h_index=h_idx,
                most_recent_paper_year=most_recent_year,
                orcid=cand.orcid,
                homepage_url=cand.homepage_url,
            )
            verified_researchers.append(vr)

        logger.info("[STEP 7] SS verified: %d / %d", ss_verified_count, len(candidates_to_verify))

        # ----------------------------------------------------------------
        # STEP 8: Faculty verification
        # ----------------------------------------------------------------
        logger.info("[STEP 8] Faculty verification (threshold=%.2f)...", FACULTY_THRESHOLD)
        faculty_eligible: list[VerifiedResearcher] = []
        industry_rejected = 0

        for vr in verified_researchers:
            extra_title = None
            # Only fetch full details for marginal cases to save API quota
            if vr.h_index < 5 and vr.total_citations < 100:
                details = self.openalex.get_author_details(vr.openalex_id)
                if details:
                    for aff in (details.get("affiliations") or []):
                        role = aff.get("institution", {}).get("type", "")
                        if role:
                            extra_title = role
                            break

            from app.schemas.researcher import ResearcherCandidate as RC
            mock_cand = RC(
                openalex_id=vr.openalex_id,
                name=vr.name,
                institution=vr.institution,
                country_code=vr.country_code,
                h_index=vr.h_index,
                total_works=len(vr.papers),
                cited_by_count=vr.total_citations,
                raw_title=extra_title,
            )
            confidence, title = self.faculty_verifier.compute_faculty_confidence(
                mock_cand, extra_title
            )
            vr.faculty_confidence = confidence
            vr.faculty_title = title

            if "rejected:industry" in title:
                industry_rejected += 1

            if self.faculty_verifier.is_eligible(confidence, threshold=FACULTY_THRESHOLD):
                faculty_eligible.append(vr)

        logger.info(
            "[STEP 8] Faculty eligible: %d / %d (industry rejected: %d)",
            len(faculty_eligible), len(verified_researchers), industry_rejected,
        )

        # Safety: relax threshold if filter too aggressive
        if not faculty_eligible and verified_researchers:
            logger.warning("[STEP 8] All removed — relaxing to confidence >= 0.10...")
            faculty_eligible = [vr for vr in verified_researchers if vr.faculty_confidence >= 0.10]
            if not faculty_eligible:
                logger.warning("[STEP 8] Still empty — using all verified researchers.")
                faculty_eligible = verified_researchers

        # ----------------------------------------------------------------
        # STEP 9 & 10: Embeddings + FAISS
        # ----------------------------------------------------------------
        logger.info("[STEP 9] Generating embeddings...")

        thesis_texts = [edu.thesis for edu in profile.education if edu.thesis]
        student_blob = build_student_text_blob(
            name=profile.name,
            interests=normalized_interests,
            thesis_titles=thesis_texts,
            skills=profile.skills,
        )
        student_embedding = self.embedding_service.encode_single(student_blob)

        researcher_blobs = []
        researcher_ids = []
        for vr in faculty_eligible:
            blob = build_researcher_text_blob(
                name=vr.name,
                institution=vr.institution,
                topics=vr.research_focus,
                paper_titles=[p.title for p in vr.papers[:10]],
            )
            researcher_blobs.append(blob)
            researcher_ids.append(vr.openalex_id)

        interest_match_scores: dict[str, float] = {}
        if researcher_blobs:
            import numpy as np
            researcher_embeddings = self.embedding_service.encode(researcher_blobs, batch_size=64)

            logger.info("[STEP 10] FAISS index: %d vectors", len(researcher_ids))
            self.faiss_service.build_index(researcher_ids, researcher_embeddings)

            top_k = min(len(researcher_ids), settings.final_shortlist_size * 2)
            faiss_results = self.faiss_service.search(student_embedding, top_k=top_k)
            interest_match_scores = {r_id: score for r_id, score in faiss_results}

        # ----------------------------------------------------------------
        # STEP 11: Ranking
        # ----------------------------------------------------------------
        logger.info("[STEP 11] Ranking %d candidates...", len(faculty_eligible))
        ranked = ranking_engine.rank_and_filter(
            researchers=faculty_eligible,
            interest_match_scores=interest_match_scores,
            target_country_codes=target_codes,
            max_output=settings.final_shortlist_size,
        )

        # ----------------------------------------------------------------
        # STEP 12 & 13: why_match generation + output assembly
        # ----------------------------------------------------------------
        logger.info("[STEP 12+13] Building output with why_match...")
        recommendations: list[SupervisorRecommendation] = []

        for vr, score, tier in ranked:
            vr_topic_set = {t.lower() for t in vr.research_focus}
            shared_topics = [
                t for t in normalized_interests
                if any(t.lower() in vt or vt in t.lower() for vt in vr_topic_set)
            ]

            top_papers_for_llm = [
                {"title": p.title, "year": p.year, "venue": p.venue}
                for p in sorted(vr.papers, key=lambda x: x.relevance_score, reverse=True)[:3]
            ]

            why_match = self.gemini.generate_why_match(
                student_name=profile.name,
                student_interests=normalized_interests[:4],
                student_thesis=thesis_texts[0] if thesis_texts else None,
                researcher_name=vr.name,
                researcher_institution=vr.institution,
                researcher_focus=vr.research_focus[:5],
                top_papers=top_papers_for_llm,
                shared_topics=shared_topics,
            )

            evidence_papers = [
                {
                    "title": p.title,
                    "year": p.year,
                    "url": p.url,
                    "venue": p.venue,
                    "citation_count": p.citation_count,
                }
                for p in sorted(vr.papers, key=lambda x: x.citation_count, reverse=True)[:5]
            ]

            rec = SupervisorRecommendation(
                supervisor_id=vr.openalex_id,
                name=vr.name,
                institution=vr.institution,
                country=vr.country_name,
                email=vr.email,
                research_focus=vr.research_focus[:8],
                evidence=EvidenceBlock(
                    papers=evidence_papers,
                    grants=vr.grants[:3] if hasattr(vr, "grants") and vr.grants else [],
                ),
                why_match=why_match,
                tier=tier,
                score=round(score, 4),
                programs=vr.programs,
                faculty_confidence=round(vr.faculty_confidence, 3),
                h_index=vr.h_index,
                total_citations=vr.total_citations,
                most_recent_paper_year=vr.most_recent_paper_year,
                homepage_url=vr.homepage_url,
                orcid=vr.orcid,
            )
            recommendations.append(rec)

        elapsed = time.time() - start_time

        # ----------------------------------------------------------------
        # DIAGNOSTICS
        # ----------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("PIPELINE DIAGNOSTICS")
        logger.info("=" * 60)
        logger.info("  Raw candidates:       %d", len(raw_candidates))
        logger.info("  Country passed:       %d", len(country_filtered))
        logger.info("  Domain passed:        %d", len(domain_passed))
        logger.info("  Verified (SS):        %d", len(verified_researchers))
        logger.info("  Faculty eligible:     %d", len(faculty_eligible))
        logger.info("  Industry rejected:    %d", industry_rejected)
        logger.info("  Final ranked:         %d", len(ranked))
        logger.info("  Recommendations:      %d", len(recommendations))
        logger.info("  Elapsed:              %.1f seconds", elapsed)
        logger.info("=" * 60)

        if not recommendations:
            logger.error("PIPELINE PRODUCED 0 RECOMMENDATIONS. Review logs above.")

        return ShortlistOutput(
            student_id=profile.student_id,
            generated_at=datetime.utcnow().isoformat(),
            total_candidates_evaluated=len(raw_candidates),
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_profile_summary(self, profile: StudentProfile) -> str:
        parts = []
        for edu in profile.education:
            parts.append(f"{edu.degree} from {edu.institution}")
            if edu.thesis:
                parts.append(f"thesis: {edu.thesis}")
        if profile.intro_call_summary:
            parts.append(profile.intro_call_summary[:300])
        return "; ".join(parts)

    def _paper_relevance(self, title: str, keywords: list[str]) -> float:
        """
        Estimate paper relevance against student keywords.
        Exact keyword substring hit (full credit) + word-level partial hit (0.4 credit).
        """
        if not title or not keywords:
            return 0.0
        title_lower = title.lower()
        exact_hits = sum(1 for kw in keywords if kw.lower() in title_lower)
        word_hits = 0.0
        for kw in keywords:
            kw_words = [w for w in kw.lower().split() if len(w) > 3]
            if kw_words and any(w in title_lower for w in kw_words):
                if kw.lower() not in title_lower:
                    word_hits += 0.4
        return min((exact_hits + word_hits) / max(len(keywords), 1) * 3, 1.0)