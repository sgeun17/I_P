from copy import deepcopy
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from diagnose_aggregation import compare


def fixture():
    # A is consistently near the top; B/C have isolated higher maxima.
    return {'success':True,'evidence_id':'SYN','index':{},'retrieval':{
        'candidates':[{'control_id':c} for c in ['B','C','A']],
        'chunk_results':[{'candidates':[{'control_id':c,'rank':i+1,'similarity_score':s}
                                       for i,(c,s) in enumerate(rows)]}
                         for rows in [[('B',.95),('A',.8),('C',.1)],
                                      [('C',.94),('A',.8),('B',.1)]]]}}


class AggregationDiagnosticTests(unittest.TestCase):
    def test_consistent_candidate_loss_is_visible_without_overwriting_runtime(self):
        result=fixture(); original=deepcopy(result)
        report=compare(result,['A'],2)
        self.assertEqual(report['rankings']['max_chunk_similarity']['top_k'],['B','C'])
        self.assertEqual(report['rankings']['mean_chunk_similarity']['top_k'][0],'A')
        self.assertEqual(report['chunk_top_k_but_not_document_top_k'][0]['control_id'],'A')
        self.assertEqual(result,original)

    def test_truncated_scores_and_invalid_expected_rejected(self):
        result=fixture();result['retrieval']['chunk_results'][0]['candidates'].pop()
        with self.assertRaises(ValueError):compare(result,['A'])
        with self.assertRaises(ValueError):compare(fixture(),['missing'])

    def test_no_expected_label_means_no_accuracy_claim(self):
        report=compare(fixture())
        self.assertTrue(all(v['required_recall_at_k'] is None for v in report['rankings'].values()))

    def test_averaging_can_drop_a_short_but_relevant_secondary_topic(self):
        # Six chunks on A and one on B: C is background similarity, not a gold topic.
        groups = [[('A', .9), ('C', .7), ('B', .1)]] * 6
        groups += [[('B', .95), ('C', .6), ('A', .1)]]
        result = {'success': True, 'evidence_id': 'imbalanced', 'index': {}, 'retrieval': {
            'candidates': [{'control_id': c} for c in ['B', 'A', 'C']],
            'chunk_results': [{'candidates': [
                {'control_id': c, 'rank': n + 1, 'similarity_score': score}
                for n, (c, score) in enumerate(rows)]} for rows in groups]}}
        rankings = compare(result, ['A', 'B'], top_k=2)['rankings']
        self.assertEqual(rankings['max_chunk_similarity']['required_recall_at_k'], 1.0)
        for method in ('mean_chunk_similarity', 'mean_top3_chunk_similarity'):
            self.assertEqual(rankings[method]['required_recall_at_k'], .5)
            self.assertNotIn('B', rankings[method]['top_k'])
