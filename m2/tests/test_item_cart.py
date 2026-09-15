import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from m2.core import Fault, demo_resources, execute, new_state, size, validate_intent
from m2.service import Service
from m2.evaluate import events_valid


class ItemCartTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T12:00:00+00:00';self.r=demo_resources(self.at,False)
        self.s=new_state(self.r.data['tenant_id'],True,self.at);self.h=[];self.d=None;self.p=None;self.n=0

    def run_intent(self,intent):
        self.n+=1
        result=execute(self.r,self.s,self.h,intent,str(self.n),str(self.n),self.at,self.p,self.d)
        self.s=result['state'];self.h=result['history'];self.d=result['dialog_context'];self.p=result['pending_intent']
        self.assertTrue(events_valid(result['events']))
        return result

    def initial(self):return self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}})

    def add(self,reference='current',quantity=1,cart_size=None,accepted=False,**target):
        intent={'operation':'add_item_to_cart','inquiry':dict(topic='purchase',reference=reference,**target),'quantity':quantity,'accepted':accepted}
        if cart_size:intent['cart_size']=cart_size
        return self.run_intent(intent)

    def assert_unmodified(self,before,result):
        self.assertEqual(self.s,before[0]);self.assertEqual(self.h,before[1])
        self.assertEqual(result['decision']['state_transition']['consumed_constraint_ids'],[])

    def test_current_item_quantity_total_and_price_snapshot(self):
        self.initial();sid=self.h[-1]['items']['shoes'];before=deepcopy((self.s,self.h))
        self.assertEqual(self.h[-1]['item_prices_fen'][sid],self.r.skus[sid]['price_fen'])
        result=self.add(slot='shoes',quantity=2)
        action=result['decision']['action']
        self.assertEqual(action['sku_ids'],[sid]);self.assertEqual(action['quantity'],2)
        self.assertEqual(action['item_count'],2);self.assertEqual(action['cart_value_fen'],2*self.r.skus[sid]['price_fen'])
        self.assertEqual(action['unit_price_fen'],self.r.skus[sid]['price_fen'])
        self.assertEqual(action['target_look_version_id'],'lv-1');self.assert_unmodified(before,result)
        self.assertIn('item_add_succeeded',[event['event_type'] for event in result['events']])

    def test_visible_history_buys_that_version_without_restoring_it(self):
        self.initial();first=self.h[0]
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes'],'required_change_slots':['shoes']})
        self.d={'view_context':{'look_version_id':first['look_version_id']}}
        before=deepcopy((self.s,self.h));result=self.add('visible',slot='shoes')
        self.assertEqual(result['cart_added_sku_ids'],[first['items']['shoes']]);self.assert_unmodified(before,result)

    def test_history_explicit_sku_must_belong_to_the_named_look(self):
        self.initial();before=deepcopy((self.s,self.h))
        result=self.add('history',look_version_id='lv-1',slot='shoes',sku_id='M1V2-S102-38')
        self.assertEqual(result['decision']['status'],'clarification');self.assertEqual(result['cart_added_sku_ids'],[])
        self.assert_unmodified(before,result)

    def test_ambiguous_target_is_clarified_without_outfit_state_changes(self):
        self.initial();before=deepcopy((self.s,self.h))
        result=self.add()
        self.assertEqual(result['decision']['clarification']['field'],'cart_target')
        self.assertIn('pending_cart',self.d);self.assert_unmodified(before,result)
        result=self.add(slot='shoes')
        self.assertEqual(result['decision']['status'],'action_completed');self.assertNotIn('pending_cart',self.d)

    def test_catalog_missing_size_then_local_size_answer_needs_no_complete_look(self):
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        before=deepcopy((self.s,self.h));result=self.add('catalog',catalog_position=1,quantity=2)
        self.assertEqual(result['decision']['clarification']['field'],'cart_size')
        self.assert_unmodified(before,result)
        pending=self.d['pending_cart'];self.assertEqual(pending['quantity'],2)
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':pending['inquiry'],'quantity':2,'cart_size':size('shoes','36')})
        self.assertEqual(result['decision']['status'],'action_completed')
        self.assertEqual(result['decision']['action']['item_count'],2)
        self.assertEqual(self.r.skus[result['cart_added_sku_ids'][0]]['size'],size('shoes','36'))
        self.assert_unmodified(before,result)

    def test_explicit_cart_size_selects_same_product_and_does_not_change_profile(self):
        self.initial();before=deepcopy((self.s,self.h));pid=self.r.skus[self.h[-1]['items']['shoes']]['product_id']
        result=self.add(slot='shoes',cart_size=size('shoes','36'))
        self.assertEqual(result['decision']['status'],'action_completed')
        self.assertEqual(self.r.skus[result['cart_added_sku_ids'][0]]['product_id'],pid)
        self.assertEqual(self.r.skus[result['cart_added_sku_ids'][0]]['size'],size('shoes','36'))
        self.assert_unmodified(before,result)

    def test_catalog_ordinal_is_bound_to_actual_list(self):
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        selected=self.d['last_catalog_result'][1]['variants'][0]['sku_id']
        result=self.add('catalog',catalog_position=2)
        self.assertEqual(result['cart_added_sku_ids'],[selected])
        result=self.add('catalog',catalog_position=9)
        self.assertEqual(result['decision']['status'],'clarification');self.assertEqual(result['cart_added_sku_ids'],[])

    def test_sold_out_downlisted_and_insufficient_quantity_never_write_cart(self):
        for mode in ['stock','sku','product','quantity']:
            with self.subTest(mode=mode):
                self.setUp();self.initial();sid=self.h[-1]['items']['shoes'];before=deepcopy((self.s,self.h))
                if mode=='stock':self.r.stock[sid]['sellable']=False
                elif mode=='sku':self.r.skus[sid]['sale_status']='unavailable'
                elif mode=='product':self.r.products[self.r.skus[sid]['product_id']]['active']=False
                else:self.r.stock[sid]['available_qty']=1
                result=self.add(slot='shoes',quantity=2)
                self.assertEqual(result['decision']['status'],'action_failed');self.assertEqual(result['cart_added_sku_ids'],[])
                self.assertIn('item_add_failed',[e['event_type'] for e in result['events']]);self.assert_unmodified(before,result)

    def test_changed_history_price_requires_specific_quote_confirmation(self):
        self.initial();sid=self.h[-1]['items']['shoes'];self.r.skus[sid]['price_fen']+=100
        before=deepcopy((self.s,self.h));result=self.add(slot='shoes',accepted=True)
        self.assertEqual(result['decision']['clarification']['field'],'cart_price_confirmation')
        self.assertEqual(result['cart_added_sku_ids'],[]);self.assertIn('270',result['response']['text']);self.assert_unmodified(before,result)
        pending=deepcopy(self.d['pending_cart'])
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':pending['inquiry'],'accepted':True})
        self.assertEqual(result['decision']['status'],'action_completed');self.assertEqual(result['decision']['action']['unit_price_fen'],27000)
        self.assert_unmodified(before,result)

    def test_price_moves_again_or_quantity_changes_requires_new_confirmation(self):
        self.initial();sid=self.h[-1]['items']['shoes'];self.r.skus[sid]['price_fen']+=100
        self.add(slot='shoes');pending=self.d['pending_cart'];self.r.skus[sid]['price_fen']+=100
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':pending['inquiry'],'accepted':True})
        self.assertEqual(result['decision']['status'],'clarification');self.assertIn('271',result['response']['text'])
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':self.d['pending_cart']['inquiry'],'quantity':2,'accepted':True})
        self.assertEqual(result['decision']['status'],'clarification');self.assertEqual(result['cart_added_sku_ids'],[])

    def test_catalog_price_change_and_old_history_without_snapshot_are_confirmed(self):
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        sid=self.d['last_catalog_result'][0]['variants'][0]['sku_id'];self.r.skus[sid]['price_fen']+=100
        result=self.add('catalog',catalog_position=1)
        self.assertEqual(result['decision']['clarification']['field'],'cart_price_confirmation')
        self.setUp();self.initial();self.h[0].pop('item_prices_fen')
        result=self.add(slot='shoes')
        self.assertEqual(result['decision']['clarification']['field'],'cart_price_confirmation')

    def test_pending_cart_does_not_turn_consultation_or_cancel_into_addition(self):
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        result=self.add('catalog',product_id=self.r.skus['M1V2-S101-38']['product_id'])
        self.assertIn('pending_cart',self.d)
        result=self.run_intent({'operation':'consult','inquiry':{'topic':'purchase','reference':'catalog','sku_id':'M1V2-S101-38'}})
        self.assertEqual(result['cart_added_sku_ids'],[]);self.assertNotIn('pending_cart',self.d)
        self.add('catalog',product_id=self.r.skus['M1V2-S101-38']['product_id'])
        result=self.run_intent({'operation':'cancel_pending'})
        self.assertFalse((self.d or {}).get('pending_cart'));self.assertEqual(result['cart_added_sku_ids'],[])

    def test_item_add_ignores_outfit_updates_and_does_not_consume_pending_keep(self):
        self.initial();self.s['context'].pop('scene')
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}]})
        self.assertTrue(any(c['kind']=='keep_sku' and c['status']=='active' for c in self.s['constraints']))
        before=deepcopy((self.s,self.h));pending=deepcopy(self.p)
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'current','slot':'shoes'},'updates':{'budget_max_fen':1,'usual_sizes':{'shoes':size('shoes','36')}},'relaxation_response':'accept','required_attributes':[{'slot':'shoes','attribute_token':'color:light'}]})
        self.assertEqual(result['decision']['status'],'action_completed');self.assert_unmodified(before,result);self.assertEqual(self.p,pending)
        self.assertNotIn('required_attributes',result['decision']['interpretation'])

    def test_cancelling_ambiguous_item_does_not_cancel_existing_outfit_keep(self):
        self.initial();self.s['context'].pop('scene')
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}]})
        before=deepcopy((self.s,self.h));pending=deepcopy(self.p)
        self.add();result=self.run_intent({'operation':'cancel_pending'})
        self.assert_unmodified(before,result);self.assertEqual(self.p,pending)
        self.assertFalse((self.d or {}).get('pending_cart'))

    def test_success_does_not_reserve_or_deduct_inventory(self):
        self.initial();stock=deepcopy(self.r.stock)
        self.add(slot='shoes',quantity=2)
        self.assertEqual(self.r.stock,stock)

    def test_single_item_does_not_depend_on_any_available_images(self):
        self.initial();sid=self.h[0]['items']['shoes']
        for look in self.r.looks.values():look['active']=False
        self.r.display=True
        with patch.object(self.r,'check_image',side_effect=AssertionError('single item does not load a look image')):
            result=self.add('history',look_version_id='lv-1',sku_id=sid)
        self.assertEqual(result['cart_added_sku_ids'],[sid])

    def test_followup_refers_to_cart_size_variant_not_the_original_look_size(self):
        self.initial();result=self.add(slot='shoes',cart_size=size('shoes','36'))
        selected=result['cart_added_sku_ids'][0]
        result=self.add('last_mentioned',slot='shoes')
        self.assertEqual(result['cart_added_sku_ids'],[selected])

    def test_pending_size_request_keeps_product_when_list_changes(self):
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        expected=self.d['last_catalog_result'][0]['product_id']
        self.add('catalog',catalog_position=1);pending=deepcopy(self.d['pending_cart'])
        self.d['last_catalog_result'].reverse()
        result=self.run_intent({'operation':'add_item_to_cart','inquiry':pending['inquiry'],'cart_size':size('shoes','36')})
        self.assertEqual(self.r.skus[result['cart_added_sku_ids'][0]]['product_id'],expected)

    def test_new_browse_clears_pending_price_acceptance(self):
        self.initial();sid=self.h[0]['items']['shoes'];self.r.skus[sid]['price_fen']+=100
        self.add(slot='shoes');pending=deepcopy(self.d['pending_cart'])
        result=self.run_intent({'operation':'browse_catalog','query_slots':['top']})
        self.assertFalse((self.d or {}).get('pending_cart'));self.assertEqual(result['cart_added_sku_ids'],[])
        result=self.add('history',look_version_id='lv-1',sku_id=sid,accepted=True)
        self.assertEqual(result['decision']['status'],'clarification');self.assertEqual(result['cart_added_sku_ids'],[])

    def test_invalid_item_contract_never_executes(self):
        base={'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','sku_id':'M1V2-S101-38'}}
        for patch in [{'quantity':True},{'quantity':0},{'quantity':11},{'quantity':1.5},{'cart_size':size('top','M')},{'inquiry':{'topic':'price','reference':'current'}},{'inquiry':{'topic':'purchase','reference':'current','whole_look':True}}]:
            with self.subTest(patch=patch):
                with self.assertRaises(Fault):validate_intent({**base,**patch},self.r)

    def test_service_button_and_restart_idempotency(self):
        class NoModel:
            configured=True
            def parse(self,*args):raise AssertionError('button must not call a model')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sessions.sqlite3';svc=Service(self.r,NoModel(),lambda:self.at,db_path=path,remember_messages=True)
            sid=svc.create(owner_id='a')['state']['session_id']
            body={'contract_version':'0.1.0','request_id':'a','turn_id':'a','idempotency_key':'a','expected_state_revision':0,'action':{'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','sku_id':'M1V2-S101-38'},'quantity':2}}
            result=svc.turn(sid,body,owner_id='a');before=svc.view(sid,owner_id='a');svc.close()
            self.assertEqual(len(before['cart']),1);self.assertEqual(before['cart'][0]['item_count'],2)
            self.assertEqual(result['cart'],before['cart'])
            svc=Service(self.r,NoModel(),lambda:self.at,db_path=path,remember_messages=True)
            self.assertEqual(svc.turn(sid,body,owner_id='a'),result);self.assertEqual(svc.view(sid,owner_id='a'),before)
            with self.assertRaises(Fault):svc.turn(sid,body,owner_id='b')
            svc.close()

    def test_concurrent_item_additions_cannot_overwrite_or_double_commit(self):
        from m2.cart import add_item
        class NoModel:
            configured=True
            def parse(self,*args):raise AssertionError('no model for button')
        svc=Service(self.r,NoModel(),lambda:self.at);sid=svc.create()['state']['session_id']
        barrier=threading.Barrier(2);outcomes=[]
        def coordinated(*args):barrier.wait(timeout=3);return add_item(*args)
        def run(key):
            body={'contract_version':'0.1.0','request_id':key,'turn_id':key,'idempotency_key':key,'expected_state_revision':0,'expected_conversation_revision':0,'action':{'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','sku_id':'M1V2-S101-38'}}}
            try:outcomes.append(svc.turn(sid,body)['decision']['status'])
            except Fault as error:outcomes.append(error.code)
        with patch('m2.cart.add_item',side_effect=coordinated):
            threads=[threading.Thread(target=run,args=(str(i),)) for i in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(5)
        self.assertCountEqual(outcomes,['action_completed','STATE_REVISION_CONFLICT'])
        self.assertEqual(len(svc.view(sid)['cart']),1);self.assertEqual(svc.view(sid)['conversation_revision'],1)
        svc.close()


if __name__=='__main__':unittest.main()
