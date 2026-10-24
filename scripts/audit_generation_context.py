"""Compare request sizes and evidence coverage of a saved run; no API/key/photos.

Example: python scripts/audit_generation_context.py --run artifacts/example/run.json
Characters are diagnostic sizes, not token counts or a promise of billed savings.
"""

import argparse
import json
from pathlib import Path

from roometry.agents.state import AgentState
from roometry.config import Settings
from roometry.vision.context_budget import select_photo_ids
from roometry.vision.evidence import apply_constraints
from roometry.vision.proposal_request import build_proposal_request
from roometry.vision.proposals import resolve_measurements


def audit(state):
    if state.analysis is None:
        raise ValueError('A saved analysis is required for the offline audit.')
    analysis = apply_constraints(state.analysis, state.reconstruction_constraints,
        wall_axes=state.proposal.wall_axes if state.proposal else None)
    confirmed = state.confirmed_dimensions.model_dump(exclude={'measurements_source'}, exclude_none=True) if state.confirmed_dimensions else {}
    measurements = resolve_measurements(analysis, confirmed, True)
    sizes = {}
    for label, compact in (('legacy', False), ('compact', True)):
        request = build_proposal_request(Settings(_env_file=None, scene_compact_context=compact), analysis, measurements, None, [])
        text = request['input'][0]['content'][0]['text']
        sizes[label] = {'context_characters': len(text), 'schema_characters': len(json.dumps(request['text'])),
                        'instruction_characters': len(request['instructions'])}
        sizes[label]['total_text_characters'] = sum(sizes[label].values())
    chosen = select_photo_ids(analysis.observations)
    return {'no_provider_calls': True, 'sizes': sizes,
            'text_reduction_percent': round(100 * (1 - sizes['compact']['total_text_characters'] / sizes['legacy']['total_text_characters']), 1),
            'analysis_and_visual_review_photo_count': len(analysis.observations.image_ids),
            'construction_photo_count': len(chosen), 'construction_photo_ids': chosen,
            'coverage_policy': 'Every recorded entity, relation, span and finish retains an attached source; repair citations are mandatory.',
            'models': {'construction': 'gpt-6.1-sol', 'other': 'gpt-6-luna'},
            'note': 'Character sizes are not tokens. Actual savings, output length and quality require the single final live verification.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = audit(AgentState.model_validate_json(args.run.read_text()))
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text)
