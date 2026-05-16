"""
Action Recommender Module (S-4 Action Layer).

Transforms comparability analysis results into concrete, prioritized
action recommendations -- the decision layer that tells practitioners
what to do next.

5-level taxonomy: PROCEED, SUPPLEMENT, INVESTIGATE, MONITOR, DEFER.
"""

from modules.action_recommender.engine import (
    recommend_attribute_action,
    recommend_overall_actions,
    ActionRecommendation,
    OverallActionSummary,
    ACTION_LEVELS,
)
