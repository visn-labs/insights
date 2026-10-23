#!/usr/bin/env python3
"""Reproduce blueprint gaps on a disposable Visn instance (never a shared server).

Usage: python3 tools/audit_depin_implementation.py http://127.0.0.1:8230
The final probe deliberately poisons the disposable vector index with invalid input.
This is an implementation audit, not a video-quality or hardware benchmark.
"""
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import bitstream_gater


def main():
    base = sys.argv[1].rstrip('/')
    results = {}

    def call(route, payload=None):
        request = urllib.request.Request(
            base + '/api/v1/telemetry/' + route,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return {'http': response.status, 'body': json.load(response)}
        except (urllib.error.URLError, OSError, ValueError) as error:
            return {'error': str(error)}

    results['status'] = call('status')
    results['uncalibrated_liveness'] = call('liveness', {
        'accumulated_frames': 0, 'pce_score': -100,
        'enf_samples': [], 'grid_reference': [],
    })
    trace = [1, 2, 4, 3, 5]
    results['caller_fabricated_liveness'] = call('liveness', {
        'accumulated_frames': 250, 'pce_score': 300,
        'enf_samples': trace, 'grid_reference': trace,
    })
    results['new_low_confidence_face'] = call('redaction', {
        'detections': [{'bbox': [.1, .1, .1, .1], 'confidence': .5}],
    })
    results['first_face'] = call('redaction', {
        'detections': [{'bbox': [.1, .1, .1, .1], 'confidence': .95}],
    })
    moved = call('redaction', {
        'detections': [{'bbox': [.15, .1, .1, .1], 'confidence': .95}],
    })
    results['moving_face'] = moved
    box = moved['body'][0]['dilated_bbox']
    results['moving_face_right_edge_uncovered'] = .25 - (box[0] + box[2])
    results['unrelated_empty_camera_request'] = call('redaction', {'detections': []})

    macroblocks = ['intra4x4'] * 10 + ['skip'] * 90
    vectors = [dict(source=-1, block_w=16, block_h=16, src_x=38, src_y=52,
                    dst_x=50, dst_y=60, motion_x=12, motion_y=8,
                    category='foreground') for _ in range(8)]
    results['rust_active_gating'] = call('gating', {
        'macroblocks': macroblocks, 'motion_vectors': vectors, 'bitrate_kbps': 2500,
    })
    results['python_active_gating'] = bitstream_gater.evaluate_bitstream_gating(
        ['intra_4x4'] * 10 + ['skip'] * 90,
        bitstream_gater.classify_motion_vectors(vectors), 2500,
    )
    results['empty_rag'] = call('video-rag', {'query': 'Why did the truck stop?'})
    results['vector_sizing'] = call('vector-sizing')

    # Same-sign distractors are far in cosine space. True neighbours differ
    # in one negligible sign bit, so the Hamming shortlist excludes all ten.
    dimension = 384
    query = [1.] + [.001] * (dimension - 1)
    vectors_by_id = {}
    for index in range(40):
        vector = [.001] + [1.] * (dimension - 1)
        vector[1] += index * .001
        vectors_by_id[index] = vector
    for index in range(10):
        vector = query.copy()
        vector[index + 1] = -.001
        vectors_by_id[100 + index] = vector
    for identity, vector in vectors_by_id.items():
        response = call('quantized-insert', {'id': identity, 'vector': vector})
        assert response.get('http') == 200, response

    def cosine(vector):
        return sum(a*b for a, b in zip(query, vector)) / math.sqrt(
            sum(a*a for a in query) * sum(b*b for b in vector))

    exact = sorted(vectors_by_id, key=lambda i: cosine(vectors_by_id[i]), reverse=True)[:10]
    started = time.perf_counter()
    approximate = call('quantized-search', {'query': query, 'top_k': 10, 'oversample_factor': 2.5})
    elapsed_ms = (time.perf_counter() - started) * 1000
    selected = [item['vector_id'] for item in approximate['body']]
    results['constructed_vector_recall'] = {
        'corpus_size': 50, 'dimension': dimension, 'exact_top10': exact,
        'approximate_top10': selected, 'recall_at_10': len(set(exact) & set(selected)) / 10,
        'request_ms': elapsed_ms, 'scope': 'adversarial synthetic fixture, not a dataset estimate',
    }

    node = dict(camera_id='camera-a', contributor_id='owner-a', h3_resolution_14_cell=1,
                overlapping_cameras_in_cell=1, pce_score=300, enf_correlation=1,
                resolution_quality=.2, walkable_fov_ratio=.2, detection_stability=.2,
                motion_entropy=1, visual_entropy=1, enterprise_queries_in_cell=10,
                commercial_zoning_prior=1, active_uptime_days=30)
    results['below_quality_threshold_payout'] = call('poc-epoch', {
        'epoch_index': 1, 'reward_pool': 100, 'nodes': [node],
    })
    results['duplicate_camera_payout'] = call('poc-epoch', {
        'epoch_index': 1, 'reward_pool': 100, 'nodes': [node, node],
    })
    results['negative_reward_pool'] = call('poc-epoch', {
        'epoch_index': 1, 'reward_pool': -100, 'nodes': [node],
    })
    # Run last: the assertion panics while holding the index mutex.
    results['invalid_dimension_insert'] = call('quantized-insert', {'id': 999, 'vector': [1.]})
    results['valid_search_after_bad_insert'] = call('quantized-search', {'query': query})
    results['status_after_bad_insert'] = call('status')
    destination = Path('output/depin-audit-2026-10-21/adversarial-results.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, indent=2) + '\n')
    print(destination)
    print(json.dumps({
        'moving_face_uncovered_width': results['moving_face_right_edge_uncovered'],
        'recall_at_10': results['constructed_vector_recall']['recall_at_10'],
        'invalid_dimension': results['invalid_dimension_insert'],
        'subsequent_search': results['valid_search_after_bad_insert'],
    }, indent=2))


if __name__ == '__main__':
    main()
