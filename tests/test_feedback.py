"""Tests for feedback learning system."""

import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.feedback_learning.feedback_processor import FeedbackProcessor


SAMPLE_CSV = """student_id,supervisor_id,institution,area,sent_at,outcome
001,SUP001,MIT,NLP,2025-01-01,ADMIT
001,SUP002,Stanford,NLP,2025-01-01,REJECT
001,SUP003,Harvard,NLP,2025-01-01,WRONG_PERSON
001,SUP004,MIT,NLP,2025-01-01,NO_REPLY
"""


def test_admit_boosts_supervisor():
    fp = FeedbackProcessor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(SAMPLE_CSV)
        path = f.name
    fp.load_outcomes(path)
    os.unlink(path)

    # ADMIT supervisor should be boosted
    adj = fp.get_adjustment("SUP001", "MIT")
    assert adj > 1.0, f"ADMIT should boost, got {adj}"


def test_wrong_person_penalizes():
    fp = FeedbackProcessor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(SAMPLE_CSV)
        path = f.name
    fp.load_outcomes(path)
    os.unlink(path)

    adj = fp.get_adjustment("SUP003", "Harvard")
    assert adj < 1.0, f"WRONG_PERSON should penalize, got {adj}"


def test_reject_penalizes():
    fp = FeedbackProcessor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(SAMPLE_CSV)
        path = f.name
    fp.load_outcomes(path)
    os.unlink(path)

    adj = fp.get_adjustment("SUP002", "Stanford")
    assert adj < 1.0, f"REJECT should penalize, got {adj}"


def test_unknown_supervisor_neutral():
    fp = FeedbackProcessor()
    adj = fp.get_adjustment("UNKNOWN_SUP", "Unknown University")
    assert adj == 1.0, f"Unknown supervisor should have neutral adjustment, got {adj}"


def test_missing_csv_graceful():
    fp = FeedbackProcessor()
    fp.load_outcomes("/nonexistent/path/outcomes.csv")
    adj = fp.get_adjustment("ANY", "ANY")
    assert adj == 1.0


if __name__ == "__main__":
    test_admit_boosts_supervisor()
    test_wrong_person_penalizes()
    test_reject_penalizes()
    test_unknown_supervisor_neutral()
    test_missing_csv_graceful()
    print("All feedback tests passed.")
