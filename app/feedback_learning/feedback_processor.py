"""
Feedback learning module.

Ingests outcomes.csv and produces per-supervisor and per-institution
adjustment multipliers that the ranking engine applies.

Outcome weights (calibrated to assignment goals):
  ADMIT           → +0.40  (strongest signal: this PI admitted a student)
  INTERVIEW       → +0.25  (PI is actively recruiting and responsive)
  POSITIVE_REPLY  → +0.12  (PI engaged; worth contacting again)
  OUT_OF_OFFICE   → +0.02  (neutral — email reached real person)
  NO_REPLY        → -0.03  (mild signal; many legitimate reasons)
  REJECT          → -0.12  (PI reviewed and declined)
  NOT_RECRUITING  → -0.20  (PI not taking students — strong negative for now)
  BOUNCE          → -0.08  (bad email — erodes confidence in contact data)
  WRONG_PERSON    → -0.35  (system surfaced wrong human — strongest penalty)

Multiplier formula:
  base = 1.0
  For each outcome event: base += weight * decay(days_old)
  multiplier = clip(base, 0.15, 2.5)

  Wider range than before (was 0.2–2.0):
  - Lower floor: WRONG_PERSON should nearly suppress a supervisor
  - Higher ceiling: ADMIT is rare and should strongly boost

Decay: 180-day half-life (outcomes >180 days old have ≤50% effect).
"""

import os
import math
import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

OUTCOME_WEIGHTS: dict[str, float] = {
    "ADMIT": 0.40,
    "INTERVIEW": 0.25,
    "POSITIVE_REPLY": 0.12,
    "OUT_OF_OFFICE": 0.02,
    "NO_REPLY": -0.03,
    "REJECT": -0.12,
    "NOT_RECRUITING": -0.20,
    "BOUNCE": -0.08,
    "WRONG_PERSON": -0.35,
}

# Multiplier clipping bounds
SUP_MIN = 0.15   # floor: near-suppress wrong-person supervisors
SUP_MAX = 2.50   # ceiling: strongly boost proven admit supervisors
INST_MIN = 0.50  # institutions change more slowly
INST_MAX = 1.50

DECAY_HALF_LIFE_DAYS = 180


def _decay(sent_at_str: str) -> float:
    """Exponential decay factor based on age of outcome."""
    try:
        sent_at = datetime.strptime(str(sent_at_str)[:10], "%Y-%m-%d")
    except Exception:
        return 1.0
    days_old = (datetime.utcnow() - sent_at).days
    return math.exp(-math.log(2) * days_old / DECAY_HALF_LIFE_DAYS)


class FeedbackProcessor:
    def __init__(self) -> None:
        self._supervisor_adjustments: dict[str, float] = {}
        self._institution_adjustments: dict[str, float] = {}

    def load_outcomes(self, csv_path: str) -> None:
        """Load and process outcomes CSV."""
        if not os.path.exists(csv_path):
            logger.warning("Outcomes CSV not found at %s — feedback disabled.", csv_path)
            return

        try:
            df = pd.read_csv(csv_path)
            required_cols = {
                "student_id", "supervisor_id", "institution",
                "area", "sent_at", "outcome",
            }
            missing = required_cols - set(df.columns)
            if missing:
                logger.error("Outcomes CSV missing columns: %s", missing)
                return

            df["outcome"] = df["outcome"].str.strip().str.upper()
            df = df[df["outcome"].isin(OUTCOME_WEIGHTS)]

            supervisor_scores: dict[str, list[float]] = {}
            institution_scores: dict[str, list[float]] = {}

            for _, row in df.iterrows():
                sup_id = str(row["supervisor_id"]).strip()
                institution = str(row["institution"]).strip()
                outcome = row["outcome"]
                weight = OUTCOME_WEIGHTS[outcome]
                d = _decay(row["sent_at"])
                adjusted = weight * d

                supervisor_scores.setdefault(sup_id, []).append(adjusted)
                institution_scores.setdefault(institution, []).append(adjusted)

            # Supervisor-level multipliers
            for sup_id, weights in supervisor_scores.items():
                raw = 1.0 + sum(weights)
                self._supervisor_adjustments[sup_id] = max(SUP_MIN, min(raw, SUP_MAX))

            # Institution-level multipliers (institution signal is weaker)
            for inst, weights in institution_scores.items():
                raw = 1.0 + sum(weights) * 0.25  # scaled down vs supervisor
                self._institution_adjustments[inst] = max(INST_MIN, min(raw, INST_MAX))

            logger.info(
                "Feedback loaded: %d supervisor adjustments, %d institution adjustments.",
                len(self._supervisor_adjustments), len(self._institution_adjustments),
            )
        except Exception as exc:
            logger.error("Failed to load outcomes CSV: %s", exc)

    def get_adjustment(self, supervisor_id: str, institution: str) -> float:
        """
        Composite multiplier for a supervisor.
        1.0 = no change. >1.0 = boost. <1.0 = penalise.
        Supervisor signal dominates (70%) over institution signal (30%).
        """
        sup_adj = self._supervisor_adjustments.get(supervisor_id, 1.0)
        inst_adj = self._institution_adjustments.get(institution, 1.0)
        return round(0.70 * sup_adj + 0.30 * inst_adj, 4)

    def get_stats(self) -> dict:
        return {
            "supervisors_with_feedback": len(self._supervisor_adjustments),
            "institutions_with_feedback": len(self._institution_adjustments),
            "top_boosted_supervisors": sorted(
                self._supervisor_adjustments.items(), key=lambda x: x[1], reverse=True
            )[:5],
            "top_penalized_supervisors": sorted(
                self._supervisor_adjustments.items(), key=lambda x: x[1]
            )[:5],
        }