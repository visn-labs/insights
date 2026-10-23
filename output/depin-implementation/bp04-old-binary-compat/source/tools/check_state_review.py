#!/usr/bin/env python3
"""Review-packet integrity contracts with synthetic fixture annotations only."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from prepare_state_review import check_annotations,digest

class Checks(unittest.TestCase):
    def fixture(self,root):
        for name in ('source','video','frame','crop'):(root/name).write_bytes(name.encode())
        frame=dict(timestamp_ms=100,image='frame',crop='crop',image_sha256=digest(root/'frame'),crop_sha256=digest(root/'crop'))
        case=dict(id='case',source_path=str(root/'source'),source_sha256=digest(root/'source'),video='video',video_sha256=digest(root/'video'),frames=[frame])
        packet=root/'packet.json';packet.write_text(json.dumps(dict(cases=[case])))
        review=dict(packet_sha256=digest(packet),review_method='human',reviewer='SYNTHETIC_TEST_FIXTURE',reviewed_utc='2026-10-16T00:00:00Z',cases=[dict(id='case',object='car',identity='unclear',reference_visible=True,frames=[dict(timestamp_ms=100,visibility='unclear',state='unclear')])])
        return packet,review

    def test_named_complete_review_required(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet,review=self.fixture(root);path=root/'labels.json'
            path.write_text(json.dumps(review));check_annotations(packet,path)
            for mutate in (lambda r:r.update(reviewer=''),lambda r:r.update(review_method='model'),lambda r:r.update(packet_sha256='changed'),lambda r:r['cases'][0]['frames'][0].update(state=''),lambda r:r['cases'][0]['frames'][0].update(timestamp_ms=101),lambda r:r['cases'][0].update(identity='')):
                bad=copy.deepcopy(review);mutate(bad);path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):check_annotations(packet,path)

    def test_v2_requires_explicit_annotation_scope(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet,review=self.fixture(root)
            data=json.loads(packet.read_text());data['version']='human-state-review-v2';packet.write_text(json.dumps(data));review['packet_sha256']=digest(packet)
            path=root/'labels.json';path.write_text(json.dumps(review))
            with self.assertRaises(ValueError):check_annotations(packet,path)
            review['cases'][0]['annotation_scope']='target';path.write_text(json.dumps(review));check_annotations(packet,path)
            review['cases'][0]['annotation_scope']='scene';path.write_text(json.dumps(review));check_annotations(packet,path)

    def test_all_evidence_files_are_immutable(self):
        for name in ('source','video','frame','crop'):
            with TemporaryDirectory() as tmp:
                root=Path(tmp);packet,review=self.fixture(root);path=root/'labels.json';path.write_text(json.dumps(review));(root/name).write_bytes(b'changed')
                with self.assertRaises(ValueError):check_annotations(packet,path)

if __name__=='__main__':unittest.main()
