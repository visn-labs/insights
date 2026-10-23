#!/usr/bin/env python3
"""Offline retrieval contract checks; fake vectors, actual cache and video decoding."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from semantic_memory_server import Retrieval, fuse


class Models:
    version = '{"model":"test-only-v1"}'
    def __init__(self):
        self.calls = 0
        self.fail_rerank = False
    def embed(self, value):
        self.calls += 1
        if 'text' in value:
            return [1., 0.]
        color = value['video'][0].getpixel((32, 32))
        return [1., 0.] if color[0] > color[2] else [0., 1.]
    def rerank(self, query, frames):
        if self.fail_rerank:
            raise RuntimeError('intentional reranker failure')
        return .9 if frames[0].getpixel((32, 32))[0] > 100 else .1


def write_video(path):
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
    for color in [(0, 0, 240), (240, 0, 0)]:
        for _ in range(10):
            writer.write(np.full((64, 64, 3), color, dtype=np.uint8))
    writer.release()


class RetrievalChecks(unittest.TestCase):
    def test_intervals_cache_hybrid_rerank_and_failure(self):
        with TemporaryDirectory() as tmp:
            args = SimpleNamespace(data_dir=tmp, frames=4, frame_size=64)
            source = Path(tmp)/'source.mp4';write_video(source)
            models = Models();service = Retrieval(args, models)
            red = dict(event_id='red', source_path=str(source), start_ms=0, end_ms=1000)
            blue = dict(event_id='blue', source_path=str(source), start_ms=1000, end_ms=2000)
            body = dict(query='red', events=[blue, red], lexical_ids=['blue'], limit=2, rerank=True)
            result = service.search(body)
            self.assertEqual(result['mode'], 'hybrid_qwen_reranker')
            self.assertEqual(result['items'][0]['event_id'], 'red')
            calls = models.calls
            service.db.close();service = Retrieval(args, models)
            self.assertIn('2 cached', service.search(body)['notes'][0])
            self.assertEqual(models.calls, calls+1, 'only the query is re-embedded after restart')
            models.version = '{"model":"test-only-v2"}'
            self.assertIn('0 cached', service.search(body)['notes'][0])
            models.fail_rerank = True
            body['events'].append(dict(red, event_id='missing', source_path=str(Path(tmp)/'missing.mp4')))
            result = service.search(body)
            self.assertEqual(result['mode'], 'hybrid_rrf')
            self.assertTrue(any('incomplete' in note for note in result['notes']))
            self.assertTrue(any('Reranking failed' in note for note in result['notes']))
            self.assertTrue(any(item['event_id']=='red' for item in result['items']))
            with self.assertRaises(ValueError):
                service.source(dict(red, source_path=__file__))
            with self.assertRaises(ValueError):
                service.search(dict(body, lexical_ids=['outside-filter']))
            service.db.close()

    def test_sparse_interval_holds_in_bounds_without_seeking_into_next_event(self):
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=8,frame_size=64),Models())
            frames=service.frames(dict(source_path=str(source),start_ms=0,end_ms=50))
            self.assertEqual(len(frames),8)
            self.assertTrue(all(frame.getpixel((32,32))[0]>200 for frame in frames))
            with self.assertRaises(ValueError):service.frames(dict(source_path=str(source),start_ms=50,end_ms=90))
            service.db.close()

    def test_fusion_preserves_semantic_only_candidates(self):
        result = fuse(['keyword'], ['visual', 'keyword'], 2)
        self.assertEqual([row[0] for row in result], ['keyword', 'visual'])
        self.assertEqual(fuse(['x', 'x'], [], 3), [('x', 1/61)])


if __name__ == '__main__':
    unittest.main()
