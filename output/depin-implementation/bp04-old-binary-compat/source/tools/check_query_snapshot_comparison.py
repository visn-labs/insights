#!/usr/bin/env python3
"""Only unordered graph-edge enumeration may be normalized by the API checker."""
import copy
import unittest
from check_query_snapshot_workflow import comparable


class Checks(unittest.TestCase):
    def test_link_order_is_ignored_but_values_and_duplicate_edges_are_not(self):
        edge=dict(entity_id='a',**{'from':'one','to':'two'},relation='same_tracker_order',gap_ms=1)
        other=dict(edge,entity_id='b')
        a=dict(query_id='first',matches=[1,2],long_memory=dict(links=[edge,other]))
        b=copy.deepcopy(a);b['query_id']='second';b['long_memory']['links'].reverse()
        self.assertEqual(comparable(a),comparable(b))
        b['long_memory']['links'][0]['gap_ms']=2
        self.assertNotEqual(comparable(a),comparable(b))
        b=copy.deepcopy(a);b['long_memory']['links'].append(edge)
        self.assertNotEqual(comparable(a),comparable(b))

    def test_retrieval_order_scores_and_observation_order_remain_exact(self):
        a=dict(query_id='first',matches=[dict(score=.12345678901234568,id='a'),dict(score=.2,id='b')],long_memory=dict(links=[],observations=[1,2]))
        b=copy.deepcopy(a);b['matches'].reverse();self.assertNotEqual(comparable(a),comparable(b))
        b=copy.deepcopy(a);b['matches'][0]['score']+=1e-16;self.assertNotEqual(comparable(a),comparable(b))
        b=copy.deepcopy(a);b['long_memory']['observations'].reverse();self.assertNotEqual(comparable(a),comparable(b))


if __name__=='__main__':unittest.main()
