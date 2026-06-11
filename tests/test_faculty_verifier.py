"""Tests for faculty verifier — rejects PhD students and postdocs."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.verification.faculty_verifier import FacultyVerifier
from app.schemas.researcher import ResearcherCandidate


def make_candidate(name="Test Person", h_index=0, works=0, citations=0, raw_title=None):
    return ResearcherCandidate(
        openalex_id="A123",
        name=name,
        institution="MIT",
        country_code="US",
        h_index=h_index,
        total_works=works,
        cited_by_count=citations,
        raw_title=raw_title,
    )


def test_professor_accepted():
    fv = FacultyVerifier()
    cand = make_candidate(h_index=20, works=80, citations=3000)
    confidence, title = fv.compute_faculty_confidence(cand, "Associate Professor of CS")
    assert confidence > 0.5, f"Expected confidence >0.5, got {confidence}"
    assert fv.is_eligible(confidence)


def test_phd_student_rejected():
    fv = FacultyVerifier()
    cand = make_candidate(h_index=2, works=3, citations=50, raw_title="PhD student")
    confidence, title = fv.compute_faculty_confidence(cand)
    assert confidence == 0.0, f"PhD student should have confidence 0, got {confidence}"
    assert not fv.is_eligible(confidence)


def test_postdoc_rejected():
    fv = FacultyVerifier()
    cand = make_candidate(h_index=5, works=12, citations=200)
    confidence, title = fv.compute_faculty_confidence(cand, "postdoctoral researcher")
    assert confidence == 0.0, f"Postdoc should have confidence 0, got {confidence}"


def test_doctoral_candidate_rejected():
    fv = FacultyVerifier()
    cand = make_candidate(h_index=3, works=5, citations=100)
    confidence, _ = fv.compute_faculty_confidence(cand, "doctoral candidate in NLP")
    assert confidence == 0.0


def test_no_title_heuristic():
    """High h-index without title should pass with moderate confidence."""
    fv = FacultyVerifier()
    cand = make_candidate(h_index=25, works=120, citations=8000)
    confidence, _ = fv.compute_faculty_confidence(cand)
    assert confidence >= 0.5, f"High h-index faculty should pass heuristic, got {confidence}"


def test_low_metrics_no_title_fails():
    fv = FacultyVerifier()
    cand = make_candidate(h_index=1, works=2, citations=10)
    confidence, _ = fv.compute_faculty_confidence(cand)
    assert not fv.is_eligible(confidence)


if __name__ == "__main__":
    test_professor_accepted()
    test_phd_student_rejected()
    test_postdoc_rejected()
    test_doctoral_candidate_rejected()
    test_no_title_heuristic()
    test_low_metrics_no_title_fails()
    print("All faculty_verifier tests passed.")
