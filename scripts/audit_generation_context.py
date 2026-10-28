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
from roometry.vision.review_render import VIEW_IDS, ReviewView
from roometry.vision.review_request import build_review_request


def audit_review(analysis, proposal, constraints):
    if proposal is None or proposal.scene is None:
        return None
    # Text-only sizing: placeholders never upload or expose actual room photos.
    images = [b''] * len(analysis.observations.image_ids)
    views = [ReviewView(key, b'') for key in VIEW_IDS]
    sizes = {}
    for label, compact in (('legacy', False), ('compact', True)):
        payload = build_review_request(Settings(_env_file=None, review_compact_context=compact),
                                       analysis, proposal, constraints, images, views)
        parts = payload['input'][0]['content']
        sizes[label] = {
            'context_characters': len(parts[0]['text']),
            'schema_characters': len(json.dumps(payload['text'])),
            'instruction_characters': len(payload['instructions']),
            'evidence_label_characters': sum(len(p['text']) for p in parts[1:] if p['type'] == 'input_text'),
        }
        sizes[label]['total_text_characters'] = sum(sizes[label].values())
    context = json.loads(payload['input'][0]['content'][0]['text'])
    return {'sizes': sizes,
            'text_reduction_percent': round(100 * (1 - sizes['compact']['total_text_characters'] / sizes['legacy']['total_text_characters']), 1),
            'original_photo_count': len(images), 'diagnostic_view_count': len(views),
            'review_target_count': len(context['review_targets']),
            'relation_probe_count': sum(len(t['relation_probes']) for t in context['review_targets']),
            'material_probe_count': sum(len(t['material_probes']) for t in context['review_targets']),
            'output_token_allowance': payload['max_output_tokens'],
            'photo_or_target_selection_changed': False}


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
            'visual_review': audit_review(analysis, state.proposal, state.reconstruction_constraints),
            'text_reduction_percent': round(100 * (1 - sizes['compact']['total_text_characters'] / sizes['legacy']['total_text_characters']), 1),
            'analysis_and_visual_review_photo_count': len(analysis.observations.image_ids),
            'construction_photo_count': len(chosen), 'construction_photo_ids': chosen,
            'coverage_policy': 'Every recorded entity, relation, span and finish retains an attached source; repair citations are mandatory.',
            'models': {'construction': Settings(_env_file=None).model_route('scene_proposal').model,
                       'visual_review': Settings(_env_file=None).model_route('visual_review').model,
                       'optimization_proposal': Settings(_env_file=None).model_route('optimization_proposal').model,
                       'other': 'gpt-6-luna'},
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
