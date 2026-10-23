#!/usr/bin/env python3
"""Assert repaired telemetry contracts against an isolated running instance.

This checks API correctness, not real-video privacy, model quality or settlement.
Usage: python3 tools/check_depin_contracts.py http://127.0.0.1:8231
"""
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request


def main():
    base = sys.argv[1].rstrip('/') + '/api/v1/telemetry/'
    observations = []
    run_id = str(time.time_ns())

    def request(route, payload=None, expected=200):
        req = urllib.request.Request(base + route, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, body = response.status, json.load(response)
        observations.append(dict(route=route, request=payload, status=status, response=body))
        assert status == expected, (route, status, body)
        return body

    status = request('status')
    assert status['capability_stage'] == 'prototype'
    assert not any(value for key, value in status.items() if key.endswith('_enabled'))
    for payload in [{}, dict(accumulated_frames=250, pce_score=300,
                            enf_samples=[1, 2, 4, 3, 5], grid_reference=[1, 2, 4, 3, 5]),
                    dict(accumulated_frames=250, pce_score=300,
                         enf_samples=[50.01, 50.02, 49.99, 49.98], grid_reference=[50.01, 50.02, 49.99, 49.98])]:
        body = request('liveness', payload)
        assert body['is_authentic'] is False and body['evaluation_only'] is True

    def scope(camera):
        return dict(workspace_id='contract-test', camera_id=camera, session_id=run_id)

    body = request('redaction', dict(**scope('a'), timestamp_ms=1,
                                    detections=[dict(bbox=[.1, .1, .1, .1], confidence=.5)]))
    assert body, 'New low-confidence face must receive a mask'
    body = request('redaction', dict(**scope('a'), timestamp_ms=2,
                                    detections=[dict(bbox=[.15, .1, .1, .1], confidence=.95)]))
    assert any(m['dilated_bbox'][0] <= .15 and sum(m['dilated_bbox'][::2]) >= .25 for m in body)
    assert request('redaction', dict(**scope('b'), timestamp_ms=1, detections=[])) == []
    request('redaction', dict(**scope('a'), timestamp_ms=2, detections=[]), expected=400)
    request('redaction', dict(**scope('a'), timestamp_ms=3,
                              detections=[dict(bbox=[.99, .1, .2, .1], confidence=.9)]), expected=400)
    request('reset-session', scope('a'))
    assert request('redaction', dict(**scope('a'), timestamp_ms=1, detections=[])) == []

    for i in range(1, 5):
        body = request('anomaly', dict(**scope('a'), timestamp_ms=i, motion_vector_norm=1,
                                      embedding_norm=1, raw_classifier_score=.9))
        assert not body['alert_dispatched'] and body['model_invoked'] is None
    body = request('anomaly', dict(**scope('b'), timestamp_ms=1, motion_vector_norm=1,
                                  embedding_norm=1, raw_classifier_score=.9))
    assert not body['alert_dispatched'], 'History must not leak from camera a'

    identity = int(run_id)
    request('quantized-insert', dict(id=identity, vector=[1]), expected=400)
    request('quantized-insert', dict(id=identity, vector=[1.] * 384))
    request('quantized-insert', dict(id=identity, vector=[1.] * 384), expected=400)
    for query in [[1], [1.] * 385, [0.] * 384]:
        request('quantized-search', dict(query=query), expected=400)
    request('quantized-search', dict(query=[1.] * 384, top_k=0), expected=400)
    assert request('quantized-search', dict(query=[1.] * 384))

    node = dict(camera_id='camera-a', contributor_id='owner-a', h3_resolution_14_cell=1,
                overlapping_cameras_in_cell=1, pce_score=300, enf_correlation=1,
                resolution_quality=.2, walkable_fov_ratio=.2, detection_stability=.2,
                motion_entropy=1, visual_entropy=1, enterprise_queries_in_cell=10,
                commercial_zoning_prior=1, active_uptime_days=30)
    body = request('poc-epoch', dict(epoch_index=1, reward_pool=100, nodes=[node]))
    assert body['node_scores'][0]['allocated_payout'] == 0
    assert body['evaluation_only'] and not body['settlement_authorized']
    request('poc-epoch', dict(epoch_index=1, reward_pool=-100, nodes=[node]), expected=400)
    request('poc-epoch', dict(epoch_index=1, reward_pool=100, nodes=[node, node]), expected=400)
    assert 'confirmed' not in request('video-rag', dict(query='why did the truck stop?'))['synthesis_summary'].lower()
    for camera in ['a', 'b']:
        request('reset-session', scope(camera))

    target = Path('output/depin-implementation/bp01-http-contracts.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(scope='prototype HTTP contracts only', requests=observations), indent=2) + '\n')
    print(f'Passed {len(observations)} HTTP requests and correctness assertions; {target}')


if __name__ == '__main__':
    main()
