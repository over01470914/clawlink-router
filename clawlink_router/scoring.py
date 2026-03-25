"""ClawLink Router - Strictness-weighted scoring engine."""

from __future__ import annotations

import logging
from typing import Any, Optional

from clawlink_router.models import ScoreResult, SelfAssessment

logger = logging.getLogger(__name__)


def _strictness_tier(strictness: int) -> str:
    """Map a 0-100 strictness value to a named tier."""
    if strictness <= 25:
        return "lenient"
    if strictness <= 50:
        return "moderate"
    if strictness <= 75:
        return "firm"
    return "strict"


class ScoringEngine:
    """Score a teaching round using strictness-weighted linear interpolation.

    The teacher_weight is ``strictness / 100``.  At strictness 0 the
    self-assessment dominates; at strictness 100 the teacher score dominates.

    Four tiers influence feedback tone:
        lenient  (0-25)  : gentle language, broad pass criteria
        moderate (26-50) : balanced
        firm     (51-75) : detailed rubric enforcement
        strict   (76-100): exacting, all rubric items enforced
    """

    def score(
        self,
        strictness: int,
        teacher_score: float,
        self_assessment: SelfAssessment,
        pass_threshold: int = 70,
        rubric: Optional[dict[str, Any]] = None,
        teacher_listened: bool = False,
        student_challenge_accepted: bool = False,
    ) -> ScoreResult:
        """Compute a weighted score and generate feedback.

        Parameters
        ----------
        strictness:
            0-100 slider value.
        teacher_score:
            Score assigned by the teaching agent (0-100).
        self_assessment:
            Student agent's self-reported metrics.
        pass_threshold:
            Minimum score to pass.
        rubric:
            Optional rubric with category weights.
        teacher_listened:
            Did the teacher incorporate student feedback?
        student_challenge_accepted:
            Did the student accept the teaching challenge?
        """
        strictness = max(0, min(100, strictness))
        teacher_weight = strictness / 100.0
        student_weight = 1.0 - teacher_weight

        student_score = self_assessment.confidence * 100.0

        # Rubric scoring
        rubric_details: dict[str, Any] = {}
        rubric_weighted_score: Optional[float] = None
        if rubric:
            total_weight = 0.0
            weighted_sum = 0.0
            for category, weight in rubric.items():
                cat_score = self_assessment.rubric_scores.get(category, 0.5) * 100.0
                rubric_details[category] = {
                    "weight": weight,
                    "student_score": cat_score,
                    "teacher_adjusted": cat_score * student_weight + teacher_score * teacher_weight,
                }
                weighted_sum += rubric_details[category]["teacher_adjusted"] * weight
                total_weight += weight
            if total_weight > 0:
                rubric_weighted_score = weighted_sum / total_weight

        # Final blended score
        if rubric_weighted_score is not None:
            base_score = rubric_weighted_score
        else:
            base_score = teacher_score * teacher_weight + student_score * student_weight

        # Bonus / penalty adjustments
        adjustment = 0.0
        if teacher_listened:
            adjustment += 2.0
        if student_challenge_accepted:
            adjustment += 1.5

        final_score = max(0.0, min(100.0, base_score + adjustment))
        passed = final_score >= pass_threshold
        tier = _strictness_tier(strictness)

        # Generate feedback
        feedback = self._generate_feedback(
            tier=tier,
            final_score=final_score,
            passed=passed,
            teacher_score=teacher_score,
            student_score=student_score,
            teacher_listened=teacher_listened,
            student_challenge_accepted=student_challenge_accepted,
        )

        result = ScoreResult(
            score=round(final_score, 2),
            passed=passed,
            feedback=feedback,
            rubric_details=rubric_details,
            teacher_listened=teacher_listened,
            student_challenge_accepted=student_challenge_accepted,
        )
        logger.info(
            "Scored session: %.2f (passed=%s, tier=%s)", final_score, passed, tier
        )
        return result

    @staticmethod
    def _generate_feedback(
        tier: str,
        final_score: float,
        passed: bool,
        teacher_score: float,
        student_score: float,
        teacher_listened: bool,
        student_challenge_accepted: bool,
    ) -> str:
        """Build human-readable feedback based on the strictness tier."""
        parts: list[str] = []

        if tier == "lenient":
            if passed:
                parts.append("Good effort overall. The concepts are on the right track.")
            else:
                parts.append(
                    "Some areas need attention, but the attempt shows understanding."
                )
        elif tier == "moderate":
            if passed:
                parts.append(
                    "Solid performance. Most criteria met with adequate depth."
                )
            else:
                parts.append(
                    "Below the passing threshold. Review the rubric areas for improvement."
                )
        elif tier == "firm":
            if passed:
                parts.append(
                    "Meets expectations across rubric categories with reasonable rigour."
                )
            else:
                parts.append(
                    "Does not meet the required standard. Specific rubric deficits noted."
                )
        else:  # strict
            if passed:
                parts.append(
                    "Passes under strict evaluation. All rubric items addressed."
                )
            else:
                parts.append(
                    "Fails strict evaluation. Multiple rubric areas are deficient."
                )

        gap = abs(teacher_score - student_score)
        if gap > 20:
            parts.append(
                f"Notable gap between teacher ({teacher_score:.0f}) and "
                f"self-assessment ({student_score:.0f}). Calibration recommended."
            )

        if teacher_listened:
            parts.append("Positive: the teacher incorporated student feedback.")
        if student_challenge_accepted:
            parts.append("Positive: the student engaged with the teaching challenge.")

        parts.append(f"Final score: {final_score:.1f}/100.")
        return " ".join(parts)
