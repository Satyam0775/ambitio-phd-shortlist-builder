"""Tests for the ranking engine."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ranking.ranking_engine import RankingEngine
from app.schemas.researcher import VerifiedResearcher


def make_researcher(
    oa_id="A001",
    name="Prof Test",
    institution="MIT",
    country="US",
    h_index=20,
    citations=5000,
    domain_score=0.7,
    faculty_conf=0.9,
    recent_year=2024,
):
    return VerifiedResearcher(
        openalex_id=oa_id,
        name=name,
        institution=institution,
        country_code=country,
        country_name="United States",
        h_index=h_index,
        total_citations=citations,
        domain_match_score=domain_score,
        faculty_confidence=faculty_conf,
        most_recent_paper_year=recent_year,
    )


def test_high_quality_researcher_scores_well():
    engine = RankingEngine()
    r = make_researcher(h_index=35, citations=10000, domain_score=0.8, faculty_conf=0.95)
    score = engine.compute_score(r, interest_match_score=0.85, target_country_codes={"US"})
    assert score > 0.6, f"High quality researcher should score >0.6, got {score}"


def test_country_mismatch_penalizes():
    engine = RankingEngine()
    r = make_researcher(country="DE")
    score_with = engine.compute_score(r, 0.8, target_country_codes={"US"})
    score_without = engine.compute_score(r, 0.8, target_country_codes={"DE"})
    assert score_without > score_with, "Country match should increase score"


def test_old_publication_penalizes():
    engine = RankingEngine()
    recent = make_researcher(recent_year=2024)
    old = make_researcher(recent_year=2010, oa_id="A002")
    score_recent = engine.compute_score(recent, 0.7, {"US"})
    score_old = engine.compute_score(old, 0.7, {"US"})
    assert score_recent > score_old, "Recent publication should outscore old"


def test_tier_assignment():
    engine = RankingEngine()
    assert engine.assign_tier(0.80) == "reach"
    assert engine.assign_tier(0.60) == "target"
    assert engine.assign_tier(0.40) == "safety"
    assert engine.assign_tier(0.20) == "exclude"


def test_rank_and_filter_deduplicates_institutions():
    engine = RankingEngine()
    researchers = [
        make_researcher(oa_id=f"A{i:03d}", name=f"Prof {i}", institution="MIT")
        for i in range(10)
    ]
    scores = {r.openalex_id: 0.8 for r in researchers}
    results = engine.rank_and_filter(researchers, scores, {"US"}, max_output=100)
    # Should cap MIT at 5
    assert len(results) <= 5


if __name__ == "__main__":
    test_high_quality_researcher_scores_well()
    test_country_mismatch_penalizes()
    test_old_publication_penalizes()
    test_tier_assignment()
    test_rank_and_filter_deduplicates_institutions()
    print("All ranking tests passed.")
