"""
CaseContext Factory -- Builds CaseContext from pipeline data.

Phase 5: Renamed from context_synthesizer.py.
Maps pipeline inputs to judgment-core-required fields with sensible defaults.
"""

from schemas.case_context import CaseContext
from typing import Any, Dict, List


def synthesize_case_context(
    product_name: str,
    change_description: str,
    attribute_results: List[Dict],
    pre_change_data: Dict[str, Any],
) -> CaseContext:
    """Build CaseContext from OLD pipeline data.

    Maps pipeline inputs to judgment-core-required fields:
      molecule_class   <- pre_change_data.get('molecule_class', 'mAb')
      change_type      <- pre_change_data.get('change_type', 'process_change')
      lifecycle_stage   <- pre_change_data.get('lifecycle_stage', 'commercial')
      flagged_attrs     <- attributes with concern in (major, critical)
      flagged_cats      <- unique categories of flagged attrs
      identified_gaps   <- from evidence_closure analysis
    """
    flagged = [a for a in attribute_results
               if a.get('concern_level', a.get('concern', 'none')) in ('major', 'critical')]
    return CaseContext(
        molecule_class=pre_change_data.get('molecule_class', 'mAb'),
        change_type=pre_change_data.get('change_type', 'process_change'),
        change_description=change_description or "Manufacturing change assessment",
        lifecycle_stage=pre_change_data.get('lifecycle_stage', 'commercial'),
        flagged_attribute_ids=[a.get('attribute_id', a.get('name', '')) for a in flagged],
        flagged_categories=list(set(a.get('category', '') for a in flagged)),
        identified_gaps=pre_change_data.get('identified_gaps', []),
        target_geography=pre_change_data.get('target_geography', 'global'),
    )
