#!/usr/bin/env python3
"""Small checks for benchmark denominators, ranking gain, and negative cases."""
import unittest
from evaluate_retrieval_corpus import metrics,summarize
class Checks(unittest.TestCase):
    def test_ranked_recall_and_gain(self):
        result=metrics(['noise','a','b'],['a','b']);self.assertEqual(result['hit@1'],0);self.assertEqual(result['recall@3'],1);self.assertGreater(result['ndcg@3'],0);self.assertLess(result['ndcg@3'],1)
        self.assertEqual(metrics(['a'],['a'])['ndcg@1'],1)
    def test_negatives_duplicates_and_missing_positives(self):
        self.assertEqual(metrics([],[])['negative_empty_result'],1);self.assertEqual(metrics(['candidate'],[])['negative_empty_result'],0)
        self.assertEqual(metrics([],['a'])['recall@5'],0)
        with self.assertRaises(ValueError):metrics(['a','a'],['a'])
    def test_failures_remain_in_effective_accuracy_denominators(self):
        queries=[dict(id='a',relevant_event_ids=['one']),dict(id='b',relevant_event_ids=['two']),dict(id='n',relevant_event_ids=[])]
        rows=[dict(mode='lexical',query_id='a',metrics=metrics(['one'],['one']),latency_secs=1),dict(mode='lexical',query_id='b',error='timeout',latency_secs=10),dict(mode='lexical',query_id='n',error='timeout',latency_secs=10)]
        result=summarize(rows,queries)['lexical'];self.assertEqual(result['failed'],2);self.assertEqual(result['mean_metrics']['hit@1'],.5);self.assertEqual(result['negative_empty_result_rate'],0)
if __name__=='__main__':unittest.main()
