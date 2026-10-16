#!/usr/bin/env python3
"""Evaluation harness trust-boundary checks; model answers are fixtures here."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from evaluate_temporal_corpus import main


class Checks(unittest.TestCase):
    def run_case(self, tmp, *, revision='revision', forged=False, changed=False, frame_size=384):
        root=Path(tmp);source=root/'source.mkv';source.write_bytes(b'frozen source')
        labels=root/'labels.json';output=root/'results'
        labels.write_text(json.dumps(dict(cases=[dict(id='case',source_path=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),start_ms=0,end_ms=800,query='Does the truck approach?',expected='PRIVATE_EXPECTED_ANSWER',expected_target_visibility=['PRIVATE_VISIBILITY']*8)])))
        if changed: source.write_bytes(b'changed source')
        times=list(range(50,800,100))
        value=dict(event_id='case',start_ms=0,end_ms=800,provenance=dict(model='fixture-model',snapshot='/cache/'+revision,frames=8,frame_size=frame_size,prompt_version='independent-target-audit-v6'),sampled_timestamps_ms=times,
            frames=[dict(timestamp_ms=t,image='data:image/jpeg;base64,YQ==') for t in times],observations=[dict(frame_index=i,timestamp_ms=times[i],target_visibility='visible',description='Truck visible') for i in (0,7)],status='supported',answer='Approaches',limitations=[],cached=False)
        if forged: value['observations'][0]['timestamp_ms']=51
        def response(request,timeout):
            payload=json.loads(request.data)
            self.assertEqual(set(payload),{'query','events'})
            self.assertEqual(set(payload['events'][0]),{'event_id','source_path','start_ms','end_ms','metadata'})
            self.assertNotIn('PRIVATE_',request.data.decode())
            return io.BytesIO(json.dumps(dict(interpretations=[value])).encode())
        argv=['eval','--labels',str(labels),'--output',str(output),'--model','fixture-model','--revision','revision']
        with patch('sys.argv',argv),patch('urllib.request.urlopen',response),contextlib.redirect_stdout(io.StringIO()): main()
        return output

    def test_labels_stay_local_and_completed_manifest(self):
        with TemporaryDirectory() as tmp:
            out=self.run_case(tmp)
            self.assertEqual(json.loads((out/'run-manifest.json').read_text())['completed'],1)
            self.assertTrue(json.loads((out/'results.json').read_text())[0]['completed'])

    def test_wrong_model_does_not_count_as_completed(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit): self.run_case(tmp,revision='wrong')
            self.assertFalse(json.loads((Path(tmp)/'results/results.json').read_text())[0]['completed'])

    def test_forged_timestamps_are_rejected_before_normalization(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit): self.run_case(tmp,forged=True)

    def test_wrong_sampling_does_not_count_as_completed(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit): self.run_case(tmp,frame_size=256)

    def test_changed_sources_stop_before_inference(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): self.run_case(tmp,changed=True)
            self.assertFalse((Path(tmp)/'results').exists())


if __name__=='__main__': unittest.main()
