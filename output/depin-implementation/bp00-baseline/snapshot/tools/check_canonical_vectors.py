#!/usr/bin/env python3
"""Fresh/cache/restart precision, legacy isolation and invalid-unit recovery."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from semantic_memory_server import Retrieval, normalize, unit_vector


class Models:
    version = '{"model":"canonical-fixture-v1"}'
    def __init__(self):
        self.calls = 0
        self.vector = normalize([.1, .2, .3])
    def embed(self, value):
        self.calls += 1
        return self.vector.copy()


class Checks(unittest.TestCase):
    def service(self, root, policy='canonical-unit-v2'):
        source=Path(root)/'source.bin';source.touch(exist_ok=True)
        service=Retrieval(SimpleNamespace(data_dir=root, frames=2, frame_size=64, vector_policy=policy),Models())
        service.frames=lambda *a,**k:[]
        return service,dict(event_id='one',source_path=str(source),start_ms=0,end_ms=1000)

    def test_reproduces_legacy_drift_and_canonical_is_exact_after_restart(self):
        with TemporaryDirectory() as root:
            legacy,event=self.service(root,'legacy-renormalize-v1')
            first,_=legacy.embedding(event);second,hit=legacy.embedding(event)
            self.assertTrue(hit);self.assertNotEqual(first,second);legacy.db.close()
            before=hashlib.sha256((Path(root)/'semantic-v1.sqlite3').read_bytes()).hexdigest()
            canonical,event=self.service(root)
            fresh,hit=canonical.embedding(event);self.assertFalse(hit)
            cached,hit=canonical.embedding(event);self.assertTrue(hit);self.assertEqual(fresh,cached)
            self.assertEqual(canonical.models.calls,1);canonical.db.close()
            canonical,event=self.service(root);restarted,hit=canonical.embedding(event)
            self.assertTrue(hit);self.assertEqual(fresh,restarted);self.assertEqual(canonical.models.calls,0)
            # Near-tied dot products keep exact values and deterministic tie order.
            q=normalize([.100000000000001,.2,.3])
            self.assertEqual(sum(a*b for a,b in zip(q,fresh)),sum(a*b for a,b in zip(q,restarted)))
            self.assertEqual(before,hashlib.sha256((Path(root)/'semantic-v1.sqlite3').read_bytes()).hexdigest())
            canonical.db.close()

    def test_invalid_unit_vectors_rejected_without_rounding_valid_values(self):
        vector=normalize([.1,.2,.3]);self.assertIs(unit_vector(vector),vector)
        for invalid in ([],[True,0.],['1'],[float('nan')],[float('inf')],[0.,0.],[2.,0.]):
            with self.assertRaises(ValueError):unit_vector(invalid)

    def test_corrupt_canonical_row_regenerates_but_never_renormalizes(self):
        with TemporaryDirectory() as root:
            service,event=self.service(root);vector,_=service.embedding(event)
            service.db.execute('UPDATE vectors SET vector=?',(json.dumps([2.,0.]),));service.db.commit()
            again,hit=service.embedding(event);self.assertFalse(hit);self.assertEqual(vector,again)
            self.assertEqual(service.models.calls,2)
            service.models.vector=[2.,0.];service.models.version='{"model":"invalid-v2"}'
            with self.assertRaises(ValueError):service.embedding(event)
            self.assertEqual(service.db.execute('SELECT count(*) FROM vectors').fetchone()[0],1)
            service.db.close()

    def test_near_tied_search_ranks_and_scores_survive_restart(self):
        with TemporaryDirectory() as root:
            def configured():
                service,event=self.service(root)
                service.frames=lambda e: [e['event_id']]
                service.models.embed=lambda v: normalize([.1 + (1e-14 if v.get('video')==['b'] else 0),.2,.3])
                return service,event
            service,event=configured()
            body=dict(query='near tie',events=[event,dict(event,event_id='b',end_ms=2000)],lexical_ids=[],limit=2,rerank=False)
            first=service.search(body)['items'];self.assertEqual(first,service.search(body)['items']);service.db.close()
            service,_=configured();self.assertEqual(first,service.search(body)['items']);service.db.close()


if __name__=='__main__':unittest.main()
