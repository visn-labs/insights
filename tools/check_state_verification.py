#!/usr/bin/env python3
"""Policy/transport contracts, not model accuracy tests."""
import copy
import unittest
from PIL import Image
from state_verification import gate, transition

class Checks(unittest.TestCase):
    def value(self,family='door_openness',claim='opening'):
        return dict(status='supported',answer='PRIVATE_PROPOSED_ANSWER',claimed_transition=claim,target_specification=dict(object='door',action='PRIVATE_ACTION',reference='frame',comparison=family),observations=[dict(frame_index=i,description='PRIVATE_AUDIT') for i in (0,1)],limitations=[])

    def run_gate(self,value,response,crops=None):
        frames=[Image.new('RGB',(64,64),c) for c in ('red','blue')]
        def complete(system,content,budget):
            text=str(content)
            for private in ('PRIVATE_PROPOSED_ANSWER','PRIVATE_AUDIT','PRIVATE_ACTION'):
                self.assertNotIn(private,text)
            self.assertEqual(sum(x['type']=='image' for x in content),4 if crops else 2)
            return copy.deepcopy(response)
        return gate(value,frames,crops,complete)

    def test_transition_table_and_unchanged_semantic_states(self):
        self.assertEqual(transition('door_openness','closed','open'),'opening')
        self.assertEqual(transition('door_openness','open','ajar'),'closing')
        self.assertEqual(transition('support_relation','on_reference','off_reference'),'removing')
        self.assertEqual(transition('region_membership','outside','inside'),'arriving')
        self.assertEqual(transition('camera_distance','near','far'),'receding')
        self.assertIsNone(transition('support_relation','on_reference','on_reference'))
        self.assertIsNone(transition('door_openness','unclear','open'))
        with self.assertRaises(ValueError):transition('door_openness','invented','open')

    def test_blind_states_agree_and_render_checked_transition(self):
        value=self.value()
        result=self.run_gate(value,dict(identity='same',reference_visible=True,before='closed',after='open'))
        self.assertEqual(result['status'],'passed');self.assertEqual(result['transition'],'opening')
        self.assertNotIn('PRIVATE_PROPOSED_ANSWER',value['answer']);self.assertIn('closed → open',value['answer'])

    def test_contradiction_same_state_identity_and_reference_abstain(self):
        base=dict(identity='same',reference_visible=True,before='closed',after='open')
        for change in (dict(before='open',after='closed'),dict(before='open',after='open'),dict(identity='different'),dict(identity='unclear'),dict(reference_visible=False),dict(reference_visible='true'),dict(after='invalid')):
            value=self.value();report=self.run_gate(value,dict(base,**change))
            self.assertEqual(report['status'],'rejected');self.assertEqual(value['status'],'uncertain')

    def test_tiny_crops_and_geometry_conflicts_fail_closed(self):
        base=dict(identity='same',reference_visible=True,before='far',after='near')
        for edge,boxes in [(8,[[0,0,.2,.2],[0,0,.4,.4]]),(32,[[0,0,.4,.4],[0,0,.2,.2]]),(32,[[0,0,.2,.2],[0,0,.201,.201]])]:
            crops=[dict(image=Image.new('RGB',(edge,edge),'red'),bbox=box) for box in boxes]
            value=self.value('camera_distance','approaching');report=self.run_gate(value,base,crops)
            self.assertEqual(report['status'],'rejected')
        crops=[dict(image=Image.new('RGB',(32,32),'red'),bbox=box) for box in [[0,0,.2,.2],[0,0,.4,.4]]]
        value=self.value('camera_distance','approaching');report=self.run_gate(value,base,crops)
        self.assertEqual(report['status'],'passed');self.assertEqual(report['tracker_area_ratio'],4)

    def test_missing_proof_and_missing_reference_never_accept_cached_claim(self):
        value=self.value();value.pop('target_specification')
        self.assertEqual(self.run_gate(value,{})['status'],'rejected')
        value=self.value('region_membership','arriving');value['target_specification']['reference']=''
        self.assertEqual(self.run_gate(value,{})['status'],'rejected')
        value=self.value();value['status']='uncertain'
        self.assertEqual(self.run_gate(value,{})['status'],'not_applicable')

    def test_cached_generation_is_rechecked_with_fresh_blind_states(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from check_retrieval import write_video
        from semantic_memory_server import Retrieval
        from temporal_interpretation import SequenceInterpreter, validate_result
        class Worker(SequenceInterpreter):
            generated=0
            def load(self):self.snapshot='fixture'
            def generate(self,q,e,frames,times):
                self.generated+=1
                value=dict(status='supported',answer='Opening',claimed_transition='opening',target_specification=dict(object='door',action='opening',reference='frame',comparison='door_openness'),observations=[dict(frame_index=i,target_visibility='visible',description='Door') for i in (0,3)],limitations=[])
                return validate_result(value,times)
        with TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4';write_video(source)
            service=Retrieval(SimpleNamespace(data_dir=tmp,frames=4,frame_size=64,interpretation_frames=4,interpretation_frame_size=64,interpretation_model='fixture'))
            worker=Worker(service)
            replies=iter([dict(identity='same',reference_visible=True,before='closed',after='open'),dict(identity='same',reference_visible=True,before='open',after='closed')])
            worker.complete=lambda *args:next(replies)
            body=dict(query='Is the door opening?',events=[dict(event_id='fixture',source_path=str(source),start_ms=0,end_ms=2000)])
            first=worker.interpret(body)['interpretations'][0];second=worker.interpret(body)['interpretations'][0]
            self.assertEqual(first['status'],'supported');self.assertEqual(second['status'],'uncertain')
            self.assertTrue(second['cached']);self.assertEqual(worker.generated,1)
            self.assertEqual(second['provenance']['state_verification']['status'],'rejected')
            service.db.close()

    def test_transport_failures_are_not_fabricated_verification(self):
        def complete(*a):raise RuntimeError('wrong returned model')
        with self.assertRaises(RuntimeError):gate(self.value(),[Image.new('RGB',(64,64))]*2,None,complete)

if __name__=='__main__':unittest.main()
