"""Catalog inquiries read sellable variants without committing a new outfit or preferences."""
import unittest
from copy import deepcopy
from m2.core import demo_resources, new_state, execute, size
from m2.evaluate import events_valid


class CatalogQueryTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T12:00:00+00:00'
        self.r=demo_resources(self.at,False)
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        self.h=[];self.dialog=None;self.pending=None;self.turn=0

    def run_intent(self,i):
        self.turn+=1
        out=execute(self.r,self.s,self.h,i,str(self.turn),str(self.turn),self.at,self.pending,self.dialog)
        self.s=out['state'];self.h=out['history'];self.dialog=out['dialog_context'];self.pending=out['pending_intent']
        return out

    def setup_profile(self,clothes='S',shoe='36',budget=80000):
        self.run_intent({'updates':{'budget_max_fen':budget,'scene':'scene:work_commute','usual_sizes':{s:size(s,v) for s,v in [('top',clothes),('bottom',clothes),('shoes',shoe)]}}})

    def query(self,**kwargs):
        out=self.run_intent({'operation':'browse_catalog',**kwargs})
        self.assertEqual(out['decision']['status'],'action_completed')
        self.assertEqual(out['decision']['action']['operation'],'browse_catalog')
        self.assertTrue(events_valid(out['events']))
        return out,out['decision']['action']['catalog_result']

    def test_unmapped_size_still_lists_real_products_and_preserves_state(self):
        self.setup_profile();self.run_intent({'confirmation_response':'confirm'});before=deepcopy(self.s)
        out,data=self.query()
        self.assertEqual(data['total_product_count'],6)
        self.assertEqual(data['coverage']['matching_size_look_count'],0)
        self.assertEqual(self.s,before);self.assertEqual(self.h,[])
        for p in data['products']:
            for v in p['variants']:
                self.assertEqual(v['price_fen'],self.r.skus[v['sku_id']]['price_fen'])
                self.assertEqual(v['size'],before['profile']['usual_sizes'][p['slot']])
        self.assertNotIn('我会在本次选择完整搭配',out['response']['text'])
        self.assertEqual([e['event_type'] for e in out['events']],['turn_submitted','catalog_viewed'])

    def test_top_only_query_and_price_filter_are_local(self):
        self.setup_profile();before=deepcopy(self.s)
        _,data=self.query(query_slots=['top'],updates={'budget_max_fen':23000})
        self.assertEqual(len(data['products']),1)
        self.assertEqual(data['products'][0]['variants'][0]['sku_id'],'M1V2-T102-S')
        self.assertEqual(self.s,before)
        self.assertEqual(data['filters']['budget_basis'],'query_item_ceiling')

    def test_single_item_cap_does_not_become_whole_outfit_cap(self):
        self.setup_profile('M','38');before=deepcopy(self.s)
        _,data=self.query(query_slots=['top'],updates={'budget_max_fen':25000})
        self.assertEqual(len(data['coverage']['eligible_look_ids']),4)
        self.assertEqual(self.s,before)

    def test_stock_and_product_status_are_revalidated(self):
        self.setup_profile()
        self.r.stock['M1V2-T102-S']['available_qty']=0
        self.r.products[self.r.skus['M1V2-T101-S']['product_id']]['active']=False
        out,data=self.query(query_slots=['top'])
        self.assertEqual(data['products'],[])
        self.assertIn('暂无',out['response']['text'])

    def test_existing_rejections_are_respected(self):
        self.setup_profile()
        self.run_intent({'rejections':[{'kind':'forbid_sku','slot':'top','value':'M1V2-T102-S','action':'add'}]})
        _,data=self.query(query_slots=['top'])
        self.assertEqual([v['sku_id'] for p in data['products'] for v in p['variants']],['M1V2-T101-S'])

    def test_query_does_not_consume_keep_or_drop_pending_operation(self):
        self.setup_profile('M','38');self.s['context'].pop('scene')
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True,'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}]})
        before=deepcopy(self.s);pending=deepcopy(self.pending);history=deepcopy(self.h)
        self.query(query_slots=['top'])
        self.assertEqual(self.s,before);self.assertEqual(self.pending,pending);self.assertEqual(self.h,history)
        self.run_intent({'updates':{'scene':'scene:work_commute'}})
        self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-112')

    def test_browsing_cannot_accept_a_budget_proposal(self):
        self.setup_profile('M','38',50000);proposal=deepcopy(self.dialog['proposal'])
        self.query(relaxation_response='accept')
        self.assertEqual(self.s['context']['budget_max_fen'],50000)
        self.assertEqual(self.dialog['proposal'],proposal)
        self.run_intent({'relaxation_response':'accept'})
        self.assertEqual(self.s['context']['budget_max_fen'],73700)

    def test_browsing_without_profile_does_not_force_outfit_questions(self):
        before=deepcopy(self.s);out,data=self.query()
        self.assertEqual(data['total_product_count'],6)
        self.assertIsNone(data['coverage']['matching_size_look_count'])
        self.assertEqual(self.s,before)
        self.assertNotIn('clarification',out['decision'])
        self.assertTrue(any(len(p['variants'])>1 for p in data['products']))

    def test_look_query_reports_coverage_without_selecting_one(self):
        self.setup_profile();before=deepcopy(self.s)
        _,data=self.query(query_target='looks')
        self.assertEqual(sum(len(g['look_ids']) for g in data['coverage']['registered_groups']),4)
        self.assertEqual(data['coverage']['eligible_look_ids'],[])
        self.assertEqual(self.s,before);self.assertFalse(self.h)

    def test_attribute_and_explicit_size_filters_do_not_modify_profile(self):
        self.setup_profile();before=deepcopy(self.s)
        _,data=self.query(query_slots=['shoes'],query_attributes=['color:light'],updates={'usual_sizes':{'shoes':size('shoes','38')}})
        self.assertEqual([v['sku_id'] for p in data['products'] for v in p['variants']],['M1V2-S102-38'])
        self.assertEqual(self.s,before)


if __name__=='__main__':unittest.main()
