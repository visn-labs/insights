#!/usr/bin/env python3
"""Scoring/scope/input-isolation contracts; fixture labels are not human evidence."""
import copy
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from evaluate_reviewed_states import main,score,validate_response
from prepare_state_review import digest
from temporal_interpretation import PROMPT_VERSION

class Checks(unittest.TestCase):
    def response(self):
        return dict(interpretations=[dict(event_id='target',start_ms=0,end_ms=400,status='uncertain',answer='Uncertain',observations=[dict(frame_index=i,timestamp_ms=t,target_visibility='visible',description='Vehicle') for i,t in enumerate((100,300))],limitations=[],sampled_timestamps_ms=[100,300],frames=[dict(timestamp_ms=t,decoded_timestamp_ms=t,image='data:image/jpeg;base64,YQ==') for t in (100,300)],provenance=dict(model='fixture',snapshot='revision',prompt_version=PROMPT_VERSION,state_verification=dict(status='rejected')),cached=False)])

    def test_scene_exclusion_and_labels_never_reach_model(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('source','video','frame','crop'):(root/name).write_bytes(name.encode())
            frames=[dict(timestamp_ms=t,image='frame',crop='crop',image_sha256=digest(root/'frame'),crop_sha256=digest(root/'crop')) for t in (100,300)]
            cases=[dict(id=id,start_ms=0,end_ms=400,source_path=str(root/'source'),source_sha256=digest(root/'source'),video='video',video_sha256=digest(root/'video'),frames=frames) for id in ('target','scene')]
            packet=root/'packet.json';packet.write_text(json.dumps(dict(cases=cases)))
            review=dict(packet_sha256=digest(packet),review_method='human',reviewer='PRIVATE_HUMAN_REVIEWER',reviewed_utc='fixture',cases=[dict(id=c['id'],object='PRIVATE_TARGET_LABEL',notes='PRIVATE_NOTES',identity='different',reference_visible=True,frames=[dict(timestamp_ms=t,visibility='visible',state='far') for t in (100,300)]) for c in cases])
            annotations=root/'annotations.json';annotations.write_text(json.dumps(review))
            bodies=[dict(query='What happens to the vehicle?',events=[dict(event_id=c['id'],source_path=c['source_path'],start_ms=0,end_ms=400)]) for c in cases]
            inputs=root/'inputs.json';inputs.write_text(json.dumps(bodies))
            scopes=root/'scopes.json';scopes.write_text(json.dumps(dict(cases=dict(target='target',scene='scene'))))
            calls=[]
            def reply(request,timeout):
                body=json.loads(request.data);calls.append(body);self.assertEqual(body,bodies[0]);self.assertNotIn('PRIVATE_',request.data.decode())
                # Human review is frozen before this first request.
                self.assertEqual((root/'out/annotations.json').read_bytes(),annotations.read_bytes())
                return io.BytesIO(json.dumps(self.response()).encode())
            argv=['eval','--packet',str(packet),'--annotations',str(annotations),'--inputs',str(inputs),'--scopes',str(scopes),'--output',str(root/'out'),'--model','fixture','--revision','revision']
            with patch('sys.argv',argv),patch('urllib.request.urlopen',reply),contextlib.redirect_stdout(io.StringIO()):main()
            self.assertEqual(len(calls),1)
            summary=json.loads((root/'out/summary.json').read_text());self.assertEqual(summary['identity_policy_passes'],1);self.assertEqual(summary['excluded_scene_cases'],1)

    def test_forged_time_wrong_model_and_missing_gate_rejected(self):
        case=dict(id='target',frames=[dict(timestamp_ms=t) for t in (100,300)]);event=dict(start_ms=0,end_ms=400)
        validate_response(self.response(),case,event,'fixture','revision')
        for mutate in (lambda v:v['frames'][0].update(decoded_timestamp_ms=101),lambda v:v['observations'][0].update(timestamp_ms=101),lambda v:v['provenance'].update(model='wrong'),lambda v:v.update(status='supported')):
            bad=self.response();mutate(bad['interpretations'][0])
            with self.assertRaises(ValueError):validate_response(bad,case,event,'fixture','revision')

    def test_identity_policy_is_distinct_from_action_accuracy(self):
        label=dict(identity='different',reference_visible=True,frames=[])
        self.assertTrue(score(dict(status='uncertain'),label)['policy_passed'])
        self.assertFalse(score(dict(status='supported'),label)['policy_passed'])
        self.assertFalse(score(dict(status='uncertain'),label)['accuracy_claim'])
        label=dict(identity='same',reference_visible=True,frames=[dict(visibility='visible',state=s) for s in ('far','near')])
        value=dict(status='supported',claimed_transition='approaching',observations=[dict(frame_index=i) for i in (0,1)])
        self.assertTrue(score(value,label)['policy_passed'])
        label['frames'][1]['state']='far'
        self.assertFalse(score(value,label)['policy_passed'])

    def test_reviewed_crop_resolution_cannot_change_silently(self):
        import base64
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(32,24)).save(buffer,format='JPEG')
        encoded='data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()
        result=self.response()
        for frame in result['interpretations'][0]['frames']:frame['target_crop']=encoded
        case=dict(id='target',frames=[dict(timestamp_ms=t,crop_size=[32,24]) for t in (100,300)])
        validate_response(result,case,dict(start_ms=0,end_ms=400),'fixture','revision')
        case['frames'][0]['crop_size']=[16,12]
        with self.assertRaises(ValueError):validate_response(result,case,dict(start_ms=0,end_ms=400),'fixture','revision')

    def test_low_visibility_is_an_abstention_policy_not_positive_accuracy(self):
        for visibility in (['unclear','unclear'],['unclear','visible']):
            review=dict(identity='same',reference_visible=True,frames=[dict(visibility=v,state='far') for v in visibility])
            row=score(dict(status='uncertain'),review)
            self.assertEqual(row['category'],'insufficient_reviewed_target_visibility');self.assertTrue(row['policy_passed']);self.assertFalse(row['accuracy_claim'])
            self.assertFalse(score(dict(status='supported'),review)['policy_passed'])

if __name__=='__main__':unittest.main()
