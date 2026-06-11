"""Tests for domain verifier — especially discipline penalty logic."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.verification.domain_verifier import DomainVerifier


def test_exact_domain_match():
    dv = DomainVerifier()
    score = dv.compute_domain_match(
        researcher_concepts=["machine learning", "natural language processing", "transformers"],
        researcher_paper_titles=["BERT for clinical NER", "clinical text mining"],
        student_interests=["biomedical NLP", "clinical NER"],
        student_keywords=["NLP", "clinical text", "transformers"],
    )
    assert score > 0.3, f"Expected >0.3, got {score}"


def test_humanities_vs_stem_penalty():
    """Roman antiquity researcher should NOT match a STEM student."""
    dv = DomainVerifier()
    score = dv.compute_domain_match(
        researcher_concepts=["classical literature", "Roman antiquity", "grief", "rhetoric"],
        researcher_paper_titles=["Trauma and grief in Roman literary history"],
        student_interests=["clinical NLP", "transformer models"],
        student_keywords=["machine learning", "deep learning"],
    )
    assert score < 0.1, f"Expected <0.1 (humanities penalty), got {score}"


def test_unrelated_domain_leakage():
    """Military ammunition should not match biomaterials student."""
    dv = DomainVerifier()
    score = dv.compute_domain_match(
        researcher_concepts=["ammunition", "ballistics", "cartridge design"],
        researcher_paper_titles=["Biodegradable plastic cartridges for military use"],
        student_interests=["biodegradable polymers for drug delivery", "biomaterials"],
        student_keywords=["scaffold", "tissue engineering", "hydrogel"],
    )
    assert score < 0.25, f"Expected <0.25, got {score}"


def test_threshold():
    dv = DomainVerifier()
    assert dv.passes_domain_threshold(0.3) is True
    assert dv.passes_domain_threshold(0.1) is False


if __name__ == "__main__":
    test_exact_domain_match()
    test_humanities_vs_stem_penalty()
    test_unrelated_domain_leakage()
    test_threshold()
    print("All domain_verifier tests passed.")
