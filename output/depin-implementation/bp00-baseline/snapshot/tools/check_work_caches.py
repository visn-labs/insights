#!/usr/bin/env python3
"""Exact cache reuse, source/config invalidation, mutation isolation and memory bounds."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import hashlib
import os
import cv2
import numpy as np
from check_retrieval import write_video
from semantic_memory_server import Retrieval

class Models:
    version='{"model":"fixture-v1"}'
    reranker_snapshot='revision-1'
    queries=0
    reranks=0
    def embed(self,value):
        self.queries+=1
        return [.12345678901234568,.9876543210987654] if 'text' in value else [1.,0.]
    def rerank(self,q,frames):self.reranks+=1;return .12345678901234568

class Checks(unittest.TestCase):
    def setup_service(self,tmp):
        source=Path(tmp)/'source.mp4';write_video(source)
        service=Retrieval(SimpleNamespace(data_dir=tmp,frames=4,frame_size=64),Models())
        return service,dict(event_id='one',source_path=str(source),start_ms=0,end_ms=2000)

    def test_pixels_pts_and_fresh_images_match_disabled_decoder(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp);service.args.disable_work_caches=True
            expected,times=service.frames(event,with_timestamps=True)
            service.args.disable_work_caches=False
            first,pts=service.frames(event,with_timestamps=True);calls=service.work_stats['ffmpeg_calls']
            first[0].putpixel((0,0),(1,2,3))
            second,again=service.frames(event,with_timestamps=True)
            self.assertEqual(service.work_stats['ffmpeg_calls'],calls);self.assertEqual(pts,times);self.assertEqual(again,times)
            self.assertEqual([f.tobytes() for f in expected],[f.tobytes() for f in second]);service.db.close()

    def test_decode_source_bounds_size_and_scaling_invalidate(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp);service.frames(event);calls=service.work_stats['ffmpeg_calls']
            for options in (dict(size=32),dict(no_upscale=True)):
                service.frames(event,**options);self.assertGreater(service.work_stats['ffmpeg_calls'],calls);calls=service.work_stats['ffmpeg_calls']
            service.frames(dict(event,end_ms=1000));self.assertGreater(service.work_stats['ffmpeg_calls'],calls);calls=service.work_stats['ffmpeg_calls']
            path=Path(event['source_path']);path.unlink();writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),10,(64,64))
            for _ in range(20):writer.write(np.full((64,64,3),(0,240,0),dtype=np.uint8))
            writer.release();frames=service.frames(event);self.assertGreater(service.work_stats['ffmpeg_calls'],calls);self.assertGreater(frames[0].getpixel((0,0))[1],200);service.db.close()

    def test_decode_lru_enforces_payload_and_entry_limits(self):
        with TemporaryDirectory() as tmp,patch('semantic_memory_server.DECODE_CACHE_MAX_BYTES',512),patch('semantic_memory_server.DECODE_CACHE_MAX_ENTRIES',2):
            service,event=self.setup_service(tmp);service.frames(event)
            self.assertLessEqual(service.decode_cache_bytes,512);self.assertLessEqual(len(service.decode_cache),2)
            self.assertEqual(service.decode_cache_bytes,sum(len(v[0]) for v in service.decode_cache.values()))
            self.assertGreater(service.work_stats['decode_evictions'],0);service.db.close()

    def test_exact_vectors_scores_revision_query_and_source_keys(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp)
            vector=service.query_embedding('red');score=service.rerank_event('red',event)
            self.assertEqual(service.query_embedding('red'),vector);self.assertEqual(service.rerank_event('red',event),score)
            self.assertEqual(service.models.queries,1);self.assertEqual(service.models.reranks,1)
            self.assertEqual(vector,(.12345678901234568,.9876543210987654));self.assertEqual(score,.12345678901234568)
            service.models.version='{"model":"fixture-v2"}';service.query_embedding('red');service.rerank_event('red',event)
            self.assertEqual(service.models.queries,2);self.assertEqual(service.models.reranks,2)
            service.models.reranker_snapshot='revision-2';service.rerank_event('red',event)
            service.rerank_event('blue',event);service.rerank_event('red',dict(event,end_ms=1000))
            self.assertEqual(service.models.reranks,5)
            with self.assertRaises(ValueError):service.rerank_event('red',dict(event,source_path=__file__))
            service.db.close()

    def test_disabling_caches_recomputes_and_finite_failures_not_cached(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp);service.query_embedding('red');service.rerank_event('red',event)
            service.args.disable_work_caches=True;service.query_embedding('red');service.rerank_event('red',event)
            self.assertEqual(service.models.queries,2);self.assertEqual(service.models.reranks,2)
            service.args.disable_work_caches=False
            service.models.rerank=lambda *a:float('nan')
            for _ in range(2):
                with self.assertRaises(ValueError):service.rerank_event('new',event)
            service.db.close()

    def test_query_and_rerank_entry_bounds(self):
        with TemporaryDirectory() as tmp,patch('semantic_memory_server.QUERY_CACHE_MAX_ENTRIES',2),patch('semantic_memory_server.RERANK_CACHE_MAX_ENTRIES',2):
            service,event=self.setup_service(tmp)
            for query in ('a','b','c','d'):service.query_embedding(query);service.rerank_event(query,event)
            self.assertEqual(len(service.query_cache),2);self.assertEqual(len(service.rerank_cache),2);service.db.close()

    def test_source_registry_is_bounded_and_reuses_exact_hash(self):
        with TemporaryDirectory() as tmp,patch('semantic_memory_server.SOURCE_CACHE_MAX_ENTRIES',2):
            service,event=self.setup_service(tmp)
            with patch('semantic_memory_server.hashlib.file_digest',wraps=hashlib.file_digest) as compute:
                original=service.source(event)[1];self.assertEqual(service.source(event)[1],original)
                self.assertEqual(compute.call_count,1)
                for name in ('second','third'):
                    path=Path(tmp)/name;path.write_bytes(name.encode());service.source(dict(event,source_path=str(path)))
                self.assertEqual(len(service.hashes),2)
                self.assertNotIn(event['source_path'],service.hashes)
                self.assertEqual(service.source(event)[1],original);self.assertEqual(compute.call_count,4)
            service.db.close()

    def test_same_size_rewrite_with_restored_mtime_invalidates_fingerprint(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp);path=Path(event['source_path'])
            before=path.stat();original=service.source(event)[1]
            content=path.read_bytes();path.write_bytes(bytes([content[0]^1])+content[1:])
            os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
            self.assertEqual(path.stat().st_size,before.st_size)
            self.assertEqual(path.stat().st_mtime_ns,before.st_mtime_ns)
            self.assertNotEqual(service.source(event)[1],original);service.db.close()

    def test_changing_source_during_hash_is_rejected_without_cache_entry(self):
        with TemporaryDirectory() as tmp:
            service,event=self.setup_service(tmp);original=hashlib.file_digest
            def changing(handle,algorithm):
                result=original(handle,algorithm)
                with Path(event['source_path']).open('ab') as changed:changed.write(b'changed')
                return result
            with patch('semantic_memory_server.hashlib.file_digest',side_effect=changing):
                with self.assertRaisesRegex(ValueError,'changed while fingerprinting'):service.source(event)
            self.assertEqual(len(service.hashes),0);service.db.close()

if __name__=='__main__':unittest.main()
