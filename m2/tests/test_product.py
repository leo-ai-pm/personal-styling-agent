import json
import threading
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch
from m2.core import *
from m2.service import Service
from m2.model import CompatibleModel
from m2.evaluate import events_valid, independent_recommendation_check, pathget, judge, MISSING


class InjectedModel:
    """Explicit test seam, not an NLU parser."""
    configured=False
    def __init__(self,intent):self.intent=intent
    def parse(self,*args):return deepcopy(self.intent),{'mode':'structured_test_injection'}


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T08:00:00+00:00';self.r=demo_resources(self.at,save=False)
        self.s=new_state(self.r.data['tenant_id'],True,self.at);self.h=[];self.n=0;self.pending=None;self.dialog=None
    def run_intent(self,i):
        self.n+=1;o=execute(self.r,self.s,self.h,i,str(self.n),str(self.n),self.at,self.pending,self.dialog)
        self.pending=o.pop('pending_intent');self.dialog=o.pop('dialog_context');self.s=o['state'];self.h=o['history'];self.assertTrue(events_valid(o['events']));return o
    def first(self):return self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}})
    def clothes(self,scope='next_recommendation'):
        return {'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True,'keep_directives':[{'slot':'shoes','scope':scope}],'preference_deltas':[{'attribute_token':'style:casual','direction':'increase'}]}
    def test_core_journey_and_immutable_recovery(self):
        o=self.run_intent({'updates':{'budget_max_fen':80000}});self.assertEqual(o['decision']['clarification']['field'],'scene')
        o=self.run_intent({'updates':{'scene':'scene:work_commute'}});self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-101');original=deepcopy(self.h)
        self.run_intent(self.clothes());self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-112')
        self.assertEqual([c['status'] for c in self.s['constraints'] if c['kind']=='keep_sku'],['consumed'])
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True,'preference_deltas':[{'attribute_token':'color:light','direction':'increase'}]})
        self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-113');self.assertEqual(self.h[:1],original)
        self.run_intent({'operation':'restore_history','target_look_version_id':'lv-1'})
        self.assertEqual(self.h[-1]['restored_from_look_version_id'],'lv-1');self.assertEqual(self.h[-1]['version_no'],4);self.assertEqual(self.h[:1],original)
        o=self.run_intent({'operation':'add_bundle_to_cart','accepted':True});self.assertEqual(len(o['cart_added_sku_ids']),3);self.assertEqual(o['decision']['action']['cart_value_fen'],79700)
    def test_temporary_keep_consumed_on_no_match(self):
        self.first();i=self.clothes();i['updates']={'budget_max_fen':70000};o=self.run_intent(i)
        self.assertEqual(o['decision']['status'],'no_match');self.assertTrue(o['decision']['state_transition']['consumed_constraint_ids']);self.assertEqual(len(self.h),1)
    def test_keep_survives_clarification_and_continues_partial_operation(self):
        self.first();self.s['context'].pop('scene');i=self.clothes();o=self.run_intent(i)
        self.assertEqual(o['decision']['status'],'clarification');self.assertTrue(any(c['kind']=='keep_sku' and c['status']=='active' for c in self.s['constraints']))
        o=self.run_intent({'updates':{'scene':'scene:work_commute'}});self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-112');self.assertTrue(o['decision']['state_transition']['consumed_constraint_ids'])
    def test_session_keep_and_explicit_release(self):
        self.first();self.run_intent(self.clothes('session'))
        self.run_intent({'operation':'modify_current'})
        self.assertEqual(self.h[-1]['items']['shoes'],'M1V2-S101-38')
        self.run_intent({'operation':'restore_history','target_look_version_id':'lv-2'})
        o=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(self.h[-1]['items']['shoes'],'M1V2-S102-38');self.assertTrue(o['decision']['state_transition']['revoked_constraint_ids'])
    def test_only_top_uses_fourth_registered_image(self):
        self.first();before=deepcopy(self.h[0]['items'])
        o=self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-116');self.assertEqual(self.h[-1]['items']['bottom'],before['bottom']);self.assertEqual(self.h[-1]['items']['shoes'],before['shoes'])
    def test_no_replacement_must_not_return_same_look(self):
        self.first();o=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(o['decision']['status'],'no_match');self.assertIn('NO_REPLACEMENT_LOOK',o['decision']['no_match']['reason_codes']);self.assertEqual(len(self.h),1)
    def test_unmapped_sizes_honest_no_match(self):
        self.first();o=self.run_intent({'updates':{'usual_sizes':{'shoes':size('shoes','39')}}})
        self.assertEqual(o['decision']['status'],'no_match');self.assertIn('SIZE_CONFLICT',o['decision']['no_match']['reason_codes'])
    def test_relaxation_requires_explicit_acceptance(self):
        o=self.run_intent({'updates':{'budget_max_fen':70000,'scene':'scene:work_commute'}})
        self.assertEqual(o['decision']['status'],'no_match');self.assertEqual(self.s['context']['budget_max_fen'],70000)
        self.assertEqual(self.dialog['proposal']['updates']['budget_max_fen'],73700)
        o=self.run_intent({'relaxation_response':'accept'})
        self.assertEqual(o['decision']['status'],'recommendation');self.assertEqual(self.s['context']['budget_max_fen'],73700)
    def test_explicit_budget_overrides_suggested_amount(self):
        self.run_intent({'updates':{'budget_max_fen':70000,'scene':'scene:work_commute'}})
        self.run_intent({'relaxation_response':'accept','updates':{'budget_max_fen':90000}})
        self.assertEqual(self.s['context']['budget_max_fen'],90000)
    def test_declining_relaxation_does_not_change_budget(self):
        self.run_intent({'updates':{'budget_max_fen':70000,'scene':'scene:work_commute'}})
        o=self.run_intent({'relaxation_response':'decline'});self.assertEqual(self.s['context']['budget_max_fen'],70000);self.assertFalse(self.h)
    def test_budget_fen_boundary(self):
        o=self.run_intent({'updates':{'budget_max_fen':73700,'scene':'scene:work_commute'}});self.assertEqual(self.h[-1]['total_price_fen'],73700)
        o=self.run_intent({'updates':{'budget_max_fen':73699}});self.assertEqual(o['decision']['status'],'no_match');self.assertEqual(self.s['context']['budget_max_fen'],73699)
    def test_forbidden_sku_attribute_and_revoke(self):
        self.first();o=self.run_intent({'rejections':[{'kind':'forbid_sku','slot':'top','value':'M1V2-T101-M','action':'add'}]})
        self.assertEqual(self.h[-1]['items']['top'],'M1V2-T102-M');self.assertEqual(self.s['rejected_sku_ids'],['M1V2-T101-M'])
        o=self.run_intent({'rejections':[{'kind':'forbid_attribute','value':'color:blue','action':'add'}]});self.assertEqual(o['decision']['status'],'no_match')
        o=self.run_intent({'rejections':[{'kind':'forbid_sku','slot':'top','value':'M1V2-T101-M','action':'remove'}]});self.assertEqual(self.h[-1]['look_id'],'M1V2-LOOK-101');self.assertEqual(self.s['rejected_sku_ids'],[])
    def test_hard_scene_no_bypass(self):
        self.first();r=deepcopy(self.r.data);r['attribute_vocabulary']['tokens'].append('scene:wedding');self.r=Resources(r)
        o=self.run_intent({'updates':{'scene':'scene:wedding','scene_hard':True}});self.assertEqual(o['decision']['status'],'no_match');self.assertIn('SCENE_CONFLICT',o['decision']['no_match']['reason_codes'])
    def test_stock_failure_is_atomic_and_history_unchanged(self):
        self.first();before=deepcopy(self.h);sku=self.h[-1]['items']['bottom'];self.r.stock[sku]['available_qty']=0
        o=self.run_intent({'operation':'add_bundle_to_cart','accepted':True});self.assertEqual(o['decision']['action_failure']['code'],'BUNDLE_INVENTORY_CHANGED');self.assertEqual(o['cart_added_sku_ids'],[]);self.assertEqual(self.h,before)
        self.assertNotIn('bundle_added_to_cart',[e['event_type'] for e in o['events']]);self.assertNotIn('recommendation_accepted',[e['event_type'] for e in o['events']])
    def test_price_change_blocks_cart_even_with_budget_headroom(self):
        self.first();self.r.skus[self.h[-1]['items']['top']]['price_fen']+=1
        o=self.run_intent({'operation':'add_bundle_to_cart'});self.assertEqual(o['decision']['action_failure']['code'],'BUNDLE_PRICE_CHANGED');self.assertEqual(o['cart_added_sku_ids'],[])
    def test_history_missing_vs_unavailable(self):
        self.first();o=self.run_intent({'operation':'restore_history','target_look_version_id':'absent'});self.assertEqual(o['decision']['status'],'action_failed')
        self.r.stock[self.h[0]['items']['shoes']]['available_qty']=0;o=self.run_intent({'operation':'restore_history','target_look_version_id':'lv-1'});self.assertEqual(o['decision']['status'],'no_match');self.assertEqual(len(self.h),1)
    def test_history_view_does_not_mutate_state(self):
        self.first();self.run_intent(self.clothes());before=deepcopy(self.s)
        o=self.run_intent({'operation':'view_history','target_look_version_id':'lv-1'});self.assertEqual(self.s,before);self.assertEqual(o['decision']['action']['viewed_look_version_ids'],['lv-1'])
    def test_scope_and_ambiguous_ignore_updates(self):
        self.first();before=deepcopy(self.s)
        self.run_intent({'operation':'scope_redirect','scope_reason':'USER_PHOTO_TRY_ON','updates':{'budget_max_fen':0}});self.assertEqual(self.s,before)
        o=self.run_intent({'operation':'modify_current','ambiguous':True,'updates':{'budget_max_fen':0},'keep_directives':[{'slot':'shoes','scope':'session'}]})
        self.assertEqual(o['decision']['state_transition']['activated_constraint_ids'],[]);self.assertEqual(self.s['context']['budget_max_fen'],80000)
    def test_invalid_intent_and_stale_snapshot_do_not_modify_input(self):
        self.first();before=deepcopy(self.s)
        for intent in [{'updates':{'budget_max_fen':True}},{'updates':{'budget_max_fen':1.2}},{'updates':{'phone':'123'}},{'keep_directives':[{'slot':'bag','scope':'session'}]},{'operation':'pay'}]:
            with self.assertRaises(Fault):self.run_intent(intent)
            self.assertEqual(self.s,before)
        self.r.inventory['valid_until']=self.at
        with self.assertRaises(Fault) as e:self.run_intent(self.clothes())
        self.assertEqual(e.exception.code,'INVENTORY_SNAPSHOT_STALE');self.assertEqual(self.s,before)
    def test_asset_error_does_not_consume_or_append(self):
        self.first();before=deepcopy(self.s)
        with patch.object(self.r,'check_image',side_effect=Fault('LOOK_MAPPING_MISMATCH')):
            with self.assertRaises(Fault):self.run_intent(self.clothes())
        self.assertEqual(self.s,before);self.assertEqual(len(self.h),1)
    def test_catalog_validation_rejects_mutations(self):
        for mutate,code in [(lambda f:f['catalog']['skus'][0].update(price_fen=True),'MONEY_INVALID'),(lambda f:f['inventory_snapshot']['items'].pop(),'INVENTORY_ITEM_MISSING'),(lambda f:f['look_assets'][0]['items'].pop('shoes'),'LOOK_SLOT_INCOMPLETE'),(lambda f:f['look_assets'][0]['origin'].update(rights_confirmed=False),'PROVENANCE_MISSING_OR_UNCONFIRMED'),(lambda f:f['look_assets'][0].update(tenant_id='alien'),'TENANT_REFERENCE_MISMATCH')]:
            d=deepcopy(self.r.data);mutate(d)
            with self.assertRaises(Fault) as e:Resources(d)
            self.assertEqual(e.exception.code,code)
    def test_new_inventory_snapshot_does_not_change_frozen_file(self):
        p=ROOT/'data/m1_v2/inventory.json';before=p.read_bytes();r=demo_resources(self.at,False)
        self.assertNotEqual(r.inventory['inventory_snapshot_id'],self.r.inventory['inventory_snapshot_id']);self.assertEqual(p.read_bytes(),before)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T08:00:00+00:00';self.r=demo_resources(self.at,False)
        self.model=InjectedModel({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}})
        self.svc=Service(self.r,self.model,lambda:self.at);self.sid=self.svc.create(True)['state']['session_id']
    def body(self,key='request',rev=0,action=None):
        b=dict(contract_version=VERSION,request_id=key,idempotency_key=key,turn_id=key,expected_state_revision=rev)
        b.update({'action':action} if action else {'user_utterance':'测试输入仅供本次解析'})
        return b
    def test_duplicate_request_returns_same_result_without_events(self):
        b=self.body();a=self.svc.turn(self.sid,b);before=self.svc.view(self.sid);self.assertEqual(self.svc.turn(self.sid,b),a);self.assertEqual(self.svc.view(self.sid),before)
        b['user_utterance']='different'
        with self.assertRaises(Fault) as e:self.svc.turn(self.sid,b)
        self.assertEqual(e.exception.code,'IDEMPOTENCY_CONFLICT')
    def test_cart_retry_atomic_and_revision_unchanged(self):
        self.svc.turn(self.sid,self.body());b=self.body('cart',1,{'operation':'add_bundle_to_cart'});a=self.svc.turn(self.sid,b);self.svc.turn(self.sid,b)
        row=self.svc.view(self.sid);self.assertEqual(len(row['cart']),1);self.assertEqual(row['state']['state_revision'],1);self.assertEqual(len(a['cart_added_sku_ids']),3)
    def test_model_failure_preserves_state_events_and_does_not_store_utterance(self):
        before=self.svc.view(self.sid)
        with patch.object(self.model,'parse',side_effect=Fault('MODEL_UNAVAILABLE')):
            with self.assertRaises(Fault):self.svc.turn(self.sid,self.body())
        self.assertEqual(self.svc.view(self.sid),before)
        self.svc.turn(self.sid,self.body());self.assertNotIn('测试输入',json.dumps(self.svc.sessions,ensure_ascii=False));self.assertNotIn('user_utterance',json.dumps(self.svc.sessions))
    def test_concurrent_writes_one_wins(self):
        barrier=threading.Barrier(2);original=self.model.parse
        def parse(*args):barrier.wait(timeout=5);return original(*args)
        self.model.parse=parse;results=[]
        def call(key):
            try:results.append(self.svc.turn(self.sid,self.body(key))['decision']['status'])
            except Fault as e:results.append(e.code)
        threads=[threading.Thread(target=call,args=(k,)) for k in ['a','b']]
        for t in threads:t.start()
        for t in threads:t.join(7)
        self.assertCountEqual(results,['recommendation','STATE_REVISION_CONFLICT']);self.assertEqual(len(self.svc.view(self.sid)['history']),1)
    def test_delete_and_expiry_purge_all_linked_data(self):
        self.svc.turn(self.sid,self.body());self.svc.delete(self.sid);self.assertNotIn(self.sid,self.svc.sessions)
        self.sid=self.svc.create()['state']['session_id'];self.at='2026-09-07T08:00:00+00:00'
        with self.assertRaises(Fault):self.svc.view(self.sid)
        self.assertNotIn(self.sid,self.svc.sessions)
    def test_unconfigured_model_does_not_fake_response(self):
        m=CompatibleModel();m.key=m.base=m.name=''
        with self.assertRaises(Fault) as e:m.parse('预算800',{},[],self.r)
        self.assertEqual(e.exception.code,'MODEL_NOT_CONFIGURED')
    def test_cross_session_idempotency_isolated(self):
        self.svc.turn(self.sid,self.body());other=self.svc.create(True)['state']['session_id'];a=self.svc.turn(other,self.body());self.assertEqual(a['state']['session_id'],other)


class RunnerTests(unittest.TestCase):
    def test_paths_and_missing_do_not_pass_negation(self):
        self.assertEqual(pathget({'a':[{'payload':{'slot':'top','value':3}}]},'a[payload.slot=top].payload.value'),3)
        self.assertIs(pathget({},'a.b'),MISSING)
    def test_resource_and_budget_validator_is_independent(self):
        r=demo_resources('2026-09-05T08:00:00+00:00',False);s=new_state(r.data['tenant_id'],True,'2026-09-05T08:00:00+00:00')
        o=execute(r,s,[],{'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}},'t','r','2026-09-05T08:00:00+00:00');o['state_before']=s
        self.assertTrue(independent_recommendation_check([o],r));o['decision']['recommendation']['look_version']['total_price_fen']=1;self.assertFalse(independent_recommendation_check([o],r))

if __name__=='__main__':unittest.main()
