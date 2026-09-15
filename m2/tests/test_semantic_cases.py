"""Coverage integrity, independent checker counterexamples, and virtual business matrix."""
from copy import deepcopy
import hashlib
import json
import unittest
from m2.core import ROOT
from m2.evaluate import DATASET_SHA
from m2.semantic_eval import Journey, check_turn, expected_skus, load_cases, virtual_matrix


class SemanticAcceptanceTests(unittest.TestCase):
    def test_registered_cases_are_independent_and_cover_semantic_families(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 60)
        self.assertEqual(len(cases), len({c['case_id'] for c in cases}))
        self.assertEqual(len(cases), len({json.dumps([c['setup'], c['turns']], ensure_ascii=False) for c in cases}))
        families = {tag for c in cases for tag in c['tags']}
        self.assertTrue({'current_reference', 'catalog_reference', 'history_reference', 'ellipsis',
                         'budget_decline', 'size_confirmation', 'size_correction', 'modify_vs_query',
                         'purchase_inquiry', 'missing_material', 'injection', 'privacy', 'multi_turn'} <= families)
        self.assertTrue(all(t['text'].strip() and t['expect'] for c in cases for t in c['turns']))

    def test_v2_changes_only_the_five_documented_assertions(self):
        source = (ROOT/'evals/p0_eval_cases.jsonl').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), DATASET_SHA)
        original = [json.loads(l) for l in source.splitlines()]
        revised = [json.loads(l) for l in (ROOT/'evals/p0_eval_cases_v2.jsonl').read_text().splitlines()]
        self.assertEqual(len(original), len(revised))
        changed = []
        for before, after in zip(original, revised):
            self.assertEqual({k: v for k, v in before.items() if k != 'assertions'},
                             {k: v for k, v in after.items() if k != 'assertions'})
            self.assertEqual(len(before['assertions']), len(after['assertions']))
            changed.extend(a['assertion_id'] for a, b in zip(before['assertions'], after['assertions']) if a != b)
        self.assertEqual(set(changed), {'P0-CONSTRAINT-007-A08', 'P0-KEEP-002-A22', 'P0-KEEP-002-A23',
                                        'P0-KEEP-003-A21', 'P0-HISTORY-002-A06'})

    def test_virtual_matrix_uses_independent_oracle_and_preserves_assets(self):
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/m1_v2/assets').glob('*')}
        results = virtual_matrix()
        self.assertEqual(len(results), 144)
        self.assertEqual([c for c in results if not c['passed']], [])
        self.assertTrue(all(c['is_simulated'] and not c['is_displayable'] for c in results))
        self.assertTrue(any(c['actual_status'] == 'recommendation' and c['clothing_size'] == 'S' for c in results))
        self.assertTrue(any(c['actual_status'] == 'no_match' and c['stock'] == 'all_unavailable' for c in results))
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data/m1_v2/assets').glob('*')}
        self.assertEqual(before, after)

    def test_checker_rejects_wrong_catalog_price_and_state_mutation(self):
        j = Journey('current')
        out = j.apply({'operation': 'browse_catalog', 'query_slots': ['shoes']})
        expect = {'operation': ['browse_catalog'], 'status': ['action_completed'], 'readonly': True, 'catalog_slots': ['shoes']}
        self.assertTrue(all(c['passed'] for c in check_turn(expect, out, j.r)))
        mutated = deepcopy(out)
        mutated['decision']['action']['catalog_result']['products'][0]['variants'][0]['price_fen'] = 1
        mutated['state']['context']['budget_max_fen'] = 999999
        failures = {c['check'] for c in check_turn(expect, mutated, j.r) if not c['passed']}
        self.assertTrue({'catalog_source_truth', 'readonly_state'} <= failures)

    def test_checker_rejects_wrong_item_and_invented_price(self):
        j = Journey('current')
        out = j.apply({'operation': 'browse_catalog', 'query_slots': ['shoes']})
        out['decision']['action']['inquiry_result'] = {'topic': 'price', 'resolved_sku_ids': ['M1V2-S102-38']}
        out['response']['text'] = '这双鞋只要1元。'
        failures = {c['check'] for c in check_turn({'topic': 'price', 'resolved': 'current.shoes'}, out, j.r) if not c['passed']}
        self.assertTrue({'resolved_item', 'answer_has_actual_price'} <= failures)

    def test_checker_rejects_invented_material(self):
        j = Journey('current')
        out = j.apply({'operation': 'browse_catalog'})
        out['response']['text'] = '鞋面是100%真皮。'
        failures = {c['check'] for c in check_turn({'material_grounded': True}, out, j.r) if not c['passed']}
        self.assertTrue({'missing_material_disclosed', 'no_invented_material_percentage'} <= failures)

    def test_reference_oracle_uses_actual_history_and_list_order(self):
        j = Journey('current3')
        self.assertEqual(expected_skus('current.shoes', j.state, j.history, []), ['M1V2-S102-38'])
        self.assertEqual(expected_skus('history.1.shoes', j.state, j.history, []), ['M1V2-S101-38'])
        out = j.apply({'operation': 'browse_catalog', 'query_slots': ['shoes']})
        self.assertEqual(expected_skus('catalog.2', j.state, j.history, [out]), ['M1V2-S101-38'])

    def test_fit_checker_understands_negation_but_rejects_later_guarantee(self):
        j = Journey('current')
        out = j.apply({'operation': 'browse_catalog'})
        out['response']['text'] = '没有品牌尺码表，不能据此保证合身。'
        self.assertTrue(all(c['passed'] for c in check_turn({'no_fit_guarantee': True}, out, j.r)))
        out['response']['text'] += '但是你穿一定合脚。'
        self.assertFalse(all(c['passed'] for c in check_turn({'no_fit_guarantee': True}, out, j.r)))

    def test_inquiry_fact_checker_rejects_wrong_price_and_fabricated_material(self):
        j = Journey('current')
        out = j.apply({'operation': 'consult', 'inquiry': {'topic': 'price', 'reference': 'current', 'slot': 'shoes'}})
        self.assertTrue(all(c['passed'] for c in check_turn({'readonly': True}, out, j.r)))
        mutated = deepcopy(out)
        mutated['decision']['action']['inquiry_result']['facts'][0]['price_fen'] = 100
        mutated['decision']['action']['inquiry_result']['facts'][0]['material'] = '纯棉'
        self.assertIn('inquiry_source_truth', [c['check'] for c in check_turn({}, mutated, j.r) if not c['passed']])

    def test_compound_purchase_price_question_is_checked_by_answer_not_topic_name(self):
        j = Journey('current')
        out = j.apply({'operation': 'consult', 'inquiry': {'topic': 'purchase', 'reference': 'current', 'slot': 'bottom'}})
        expect = {'topic': 'price', 'resolved': 'current.bottom', 'readonly': True}
        self.assertTrue(all(c['passed'] for c in check_turn(expect, out, j.r)))
        out['response']['text'] = '当前只支持整套加购。'
        self.assertIn('answer_has_actual_price', [c['check'] for c in check_turn(expect, out, j.r) if not c['passed']])


if __name__ == '__main__':
    unittest.main()
