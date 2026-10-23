#!/usr/bin/env python3
"""Parity controls prevent unrelated context changes being attributed to crop detail."""
import copy
import unittest
from evaluate_crop_modes import compare
from temporal_interpretation import CROP_VERSION

class Checks(unittest.TestCase):
    def values(self):
        preview=dict(sampled_timestamps_ms=[100,200],frames=[dict(image='frame-A'),dict(image='frame-B')],provenance=dict(crop_evidence=dict(mode='preview',version=CROP_VERSION,no_upscale=False,decoded_timestamps_ms=[110,210],crop_sizes=[[20,20]]*2)))
        source=copy.deepcopy(preview);source['provenance']['crop_evidence'].update(mode='source',no_upscale=True,crop_sizes=[[40,40]]*2)
        return preview,source

    def test_parity_and_real_pixel_area(self):
        a,b=self.values();self.assertEqual(compare(a,b)['pixel_area_ratios'],[4,4])
        # A small native source may have fewer pixels than an already-upscaled preview.
        b['provenance']['crop_evidence']['crop_sizes']=[[10,10]]*2
        self.assertEqual(compare(a,b)['pixel_area_ratios'],[.25,.25])

    def test_changed_context_pts_mode_and_upscale_rejected(self):
        for mutate in (lambda v:v['frames'][0].update(image='different'),lambda v:v.update(sampled_timestamps_ms=[101,200]),lambda v:v['provenance']['crop_evidence'].update(decoded_timestamps_ms=[111,210]),lambda v:v['provenance']['crop_evidence'].update(mode='preview'),lambda v:v['provenance']['crop_evidence'].update(no_upscale=False)):
            a,b=self.values();mutate(b)
            with self.assertRaises(ValueError):compare(a,b)

if __name__=='__main__':unittest.main()
