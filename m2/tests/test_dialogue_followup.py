"""User-reported follow-up regressions; state and grounded explanations are checked."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import patch
from m2.core import execute, new_state, size, SLOTS, Fault
from m2.preview import preview_resources

AT='2026-09-05T12:00:00+00:00'


class DialogueFollowupTests(unittest.TestCase):
    def setUp(self):
        self.r=preview_resources(AT,save=False)
        self.s=new_state(self.r.data['tenant_id'],False,AT)
        self.h=[];self.d=None;self.p=None;self.n=0
        self.run_intent({'updates':{'budget_max_fen':50000,'scene':'scene:work_commute',
                         'usual_sizes':{slot:size(slot,value) for slot,value in zip(SLOTS,['S','S','36'])}}})

    def run_intent(self,i):
        self.n+=1
        out=execute(self.r,self.s,self.h,i,f'dialogue-t-{self.n}',f'dialogue-r-{self.n}',AT,self.p,self.d)
        self.s,self.h,self.d,self.p=out['state'],out['history'],out['dialog_context'],out['pending_intent']
        return out

    def test_top_only_no_match_explains_kept_parts_and_coverage(self):
        before=deepcopy(self.h)
        out=self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h,before)
        text=out['response']['text']
        self.assertIn('上衣',text)
        self.assertIn('下装',text)
        self.assertIn('鞋',text)
        self.assertRegex(text,'搭配图|图片')
        self.assertNotIn('换搭要求',text)

    def test_purchase_inquiry_explains_single_cart_support_without_execution(self):
        before=deepcopy(self.s)
        out=self.run_intent({'operation':'consult','inquiry':{'topic':'purchase','reference':'current','slot':'shoes'}})
        self.assertEqual(out['cart_added_sku_ids'],[])
        self.assertEqual(self.s,before)
        self.assertNotIn('不支持单件',out['response']['text'])
        self.assertIn('单件',out['response']['text'])
        self.assertIn('模拟',out['response']['text'])

    def test_exact_catalog_name_is_resolved_to_its_actual_product(self):
        out=self.run_intent({'operation':'consult','inquiry':{'topic':'price','reference':'catalog','product_name':'象牙白简洁薄底休闲鞋'}})
        facts=out['decision']['action']['inquiry_result']['facts']
        self.assertEqual({f['product_id'] for f in facts},{'M3-P-S202'})
        self.assertEqual({f['price_fen'] for f in facts},{16900})

    def test_exact_name_conflicting_with_similar_product_id_is_rejected(self):
        with self.assertRaises(Fault):
            self.run_intent({'operation':'consult','inquiry':{'topic':'price','reference':'catalog','product_name':'象牙白简洁薄底休闲鞋','product_id':'M1V2-P-S102'}})

    def test_required_light_shoes_never_change_to_dark_shoes(self):
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True})
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        first=deepcopy(self.h[-1])
        self.assertTrue(self.r.hit(first,'color:light',['shoes']))
        out=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True,
                            'required_attributes':[{'slot':'shoes','attribute_token':'color:light'}]})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h[-1],first)

    def test_expanded_offer_preserves_required_attribute(self):
        required=[{'slot':'shoes','attribute_token':'color:light'}]
        out=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True,'required_attributes':required})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(out['dialog_context']['proposal']['required_attributes'],required)
        out=self.run_intent({'operation':'modify_current','relaxation_response':'accept'})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertTrue(self.r.hit(self.h[-1],'color:light',['shoes']))

    def test_local_required_attribute_does_not_become_permanent(self):
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True})
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True,
                         'required_attributes':[{'slot':'shoes','attribute_token':'color:light'}]})
        out=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertFalse(self.r.hit(self.h[-1],'color:light',['shoes']))

    def test_explicit_soft_preference_is_still_soft(self):
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True})
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        out=self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True,
                            'preference_deltas':[{'slot':'shoes','attribute_token':'color:light','direction':'add'}]})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertIn('未满足',out['response']['text'])

    def test_no_modification_correction_continues_current_baseline(self):
        first=deepcopy(self.h[-1])
        self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        out=self.run_intent({'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h[-1],first)
        self.assertEqual(out['decision']['no_match']['scope_diagnostics']['base_look_version_id'],first['look_version_id'])

    def checked_parse(self,text,primary,check):
        from m2.model import CompatibleModel
        model=CompatibleModel();model.key='test-not-a-real-credential';model.base='https://example.invalid/v1';model.name='test-model'
        def response(value):
            return io.BytesIO(json.dumps({'model':'test-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
                                         'usage':{'prompt_tokens':11,'completion_tokens':7}}).encode())
        with patch('urllib.request.OpenerDirector.open',side_effect=[response(primary),response(check)]):
            intent,metrics=model.parse(text,self.s,self.h,self.r,self.d)
        return intent,metrics

    def test_checker_catches_selection_misparsed_as_repeat_browse(self):
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        intent,metrics=self.checked_parse('帮我挑一下',{'operation':'browse_catalog','query_slots':['shoes']},
                                          {'decision':'select_item','slots':['shoes'],'whole_look':False})
        self.assertEqual(intent['operation'],'modify_current')
        self.assertEqual(intent['scope_reference'],'recent_topic')
        self.assertEqual(intent['replace_slots'],['shoes'])
        self.assertEqual(metrics['api_calls'],2)

    def test_cancellation_does_not_require_purchase_or_outfit_mutation_permission(self):
        for pending in [False,True]:
            self.s=new_state(self.r.data['tenant_id'],False,AT);self.h=[];self.d=None;self.p=None
            if pending:
                self.run_intent({'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','product_name':'象牙白简洁薄底休闲鞋'}})
                self.assertIsNotNone(self.d['pending_cart'])
            before=deepcopy(self.s)
            intent,metrics=self.checked_parse('算了，先不买',{'operation':'cancel_pending'},
                                              {'decision':'clarify','slots':[],'whole_look':False})
            out=self.run_intent(intent)
            self.assertEqual(intent['operation'],'cancel_pending')
            self.assertEqual(metrics['api_calls'],1)
            self.assertIsNone((self.d or {}).get('pending_cart'))
            self.assertEqual(out['cart_added_sku_ids'],[])
            self.assertEqual(self.s['profile'],before['profile'])
            self.assertEqual(self.h,[])
            self.assertNotIn('更换哪些',out['response']['text'])

    def test_checker_resolves_a_positive_pending_cart_slot_even_if_primary_ambiguous(self):
        self.run_intent({'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'current'}})
        intent,_=self.checked_parse('我指鞋子',{'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'current'},'ambiguous':True},
                                     {'decision':'resolve_cart_target','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertEqual(out['cart_added_sku_ids'],['M3-S201-36'])

    def test_checker_reaffirmed_original_scope_declines_expansion(self):
        self.run_intent({'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        intent,_=self.checked_parse('我是说只换鞋子，别的不要换',{'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True},
                                     {'decision':'decline_scope','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertIsNone(out['dialog_context']['proposal'])
        self.assertNotIn('是否允许',out['response']['text'])

    def test_scope_checker_does_not_invent_a_declined_proposal_for_history_correction(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.d={'last_modification':{'base_look_version_id':'lv-1','result_look_version_id':'lv-2','replace_slots':SLOTS}}
        intent,_=self.checked_parse('我是说只换鞋子，别的不要换',
                                    {'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True},
                                    {'decision':'decline_scope','slots':['shoes'],'whole_look':False})
        self.assertEqual(intent['scope_reference'],'previous_request')
        self.assertIsNone(intent['relaxation_response'])
        out=self.run_intent(intent)
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(out['decision']['no_match']['scope_diagnostics']['base_look_version_id'],'lv-1')

    def test_single_cart_ignores_inapplicable_mutation_noise_but_preserves_target(self):
        before=deepcopy(self.s)
        primary={'operation':'add_item_to_cart','inquiry':{'topic':'purchase','reference':'catalog','product_name':'象牙白简洁薄底休闲鞋'},
                 'cart_size':{'system':'cn_shoe','value':'36'},'quantity':1,
                 'rejections':[{'kind':'forbid_sku','slot':'shoes','value':'M1V2-P-S102','action':'add'}],
                 'updates':{'budget_max_fen':90000}}
        intent,_=self.checked_parse('旧款不要，简洁款36码加一双',primary,{'decision':'execute','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertEqual(out['cart_added_sku_ids'],['M3-S202-36'])
        self.assertEqual(self.s,before)

    def test_independent_correction_check_recovers_missing_primary_reference(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.d={'last_modification':{'base_look_version_id':'lv-1','result_look_version_id':'lv-2','replace_slots':SLOTS}}
        intent,_=self.checked_parse('你把衣服也换了，我只是想动鞋',
                                    {'operation':'modify_current','replace_slots':['shoes'],'relaxation_response':'decline'},
                                    {'decision':'correct_previous_scope','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(out['decision']['no_match']['scope_diagnostics']['base_look_version_id'],'lv-1')

    def test_ordinary_current_change_does_not_reuse_previous_modification_base(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.d={'last_modification':{'base_look_version_id':'lv-1','result_look_version_id':'lv-2','replace_slots':SLOTS}}
        current=deepcopy(self.h[-1])
        intent,_=self.checked_parse('当前这套只换鞋',
                                    {'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True},
                                    {'decision':'execute','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertEqual(self.h[-1]['items']['top'],current['items']['top'])
        self.assertEqual(self.h[-1]['items']['bottom'],current['items']['bottom'])

    def test_checker_alone_cannot_create_a_historical_reference(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.d={'last_modification':{'base_look_version_id':'lv-1','result_look_version_id':'lv-2','replace_slots':SLOTS}}
        current=deepcopy(self.h[-1])
        intent,_=self.checked_parse('当前这套只换鞋',
                                    {'operation':'modify_current','replace_slots':['shoes'],'carry_forward_unmentioned':True},
                                    {'decision':'correct_previous_scope','slots':['shoes'],'whole_look':False})
        out=self.run_intent(intent)
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertIsNone(intent['scope_reference'])
        self.assertEqual(self.h[-1]['items']['top'],current['items']['top'])
        self.assertEqual(self.h[-1]['items']['bottom'],current['items']['bottom'])

    def test_unverified_decline_is_rejected_without_an_offer_or_explicit_correction(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.d={'last_modification':{'base_look_version_id':'lv-1','result_look_version_id':'lv-2','replace_slots':SLOTS}}
        before=deepcopy(self.s)
        with self.assertRaises(Fault):
            self.checked_parse('继续',{'operation':'modify_current','replace_slots':['shoes'],'relaxation_response':'decline'},
                               {'decision':'decline_scope','slots':['shoes'],'whole_look':False})
        self.assertEqual(self.s,before)

    def test_single_cart_still_rejects_invalid_or_conflicting_actual_target(self):
        for extra in [{'sku_id':'nonexistent-sku'},{'product_name':'象牙白简洁薄底休闲鞋','product_id':'M1V2-P-S102'}]:
            with self.assertRaises(Fault):
                self.checked_parse('简洁鞋36码加一双',{'operation':'add_item_to_cart','inquiry':dict(topic='purchase',reference='catalog',**extra)},
                                   {'decision':'execute','slots':['shoes'],'whole_look':False})

    def test_catalog_followup_does_not_replace_unmentioned_clothes(self):
        first=deepcopy(self.h[-1]);self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        out=self.run_intent({'operation':'modify_current','scope_reference':'recent_topic','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.assertEqual(out['decision']['interpretation']['replace_slots'],['shoes'])
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h[-1]['items'],first['items'])
        self.assertIn('鞋',out['response']['text'])

    def test_catalog_topic_switch_retires_only_the_unrelated_local_scope_offer(self):
        self.run_intent({'operation':'modify_current','replace_slots':['bottom'],'carry_forward_unmentioned':True})
        self.assertIsNotNone(self.d['proposal'])
        self.d.update(confirmed_sizes=deepcopy(self.s['profile']['usual_sizes']),declined_budget_max_fen=50000)
        before=deepcopy(self.s);history=deepcopy(self.h)
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        self.assertEqual(self.d['dialogue_topic']['slot'],'shoes')
        for key in ['proposal','no_match','retry_intent']:self.assertNotIn(key,self.d)
        self.assertEqual(self.d['confirmed_sizes'],before['profile']['usual_sizes'])
        self.assertEqual(self.d['declined_budget_max_fen'],50000)
        self.assertEqual(self.s,before)
        self.assertEqual(self.h,history)

    def test_catalog_same_topic_and_budget_offer_are_not_retired(self):
        self.run_intent({'operation':'modify_current','replace_slots':['bottom'],'carry_forward_unmentioned':True})
        proposal=deepcopy(self.d['proposal'])
        self.run_intent({'operation':'browse_catalog','query_slots':['bottom']})
        self.assertEqual(self.d['proposal'],proposal)
        self.d['no_match']['suggested_relaxation']['action']='ask_user_to_increase_budget'
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        self.assertEqual(self.d['proposal'],proposal)

    def test_comfort_copy_uses_actual_shoe_structure_only(self):
        from m2.consultation import preference_explanation
        look=self.h[-1];sid=look['items']['shoes'];sku=self.r.skus[sid];product=self.r.products[sku['product_id']]
        intent={'replace_slots':['shoes'],'preference_deltas':[{'slot':'shoes','attribute_token':'comfort:comfortable','direction':'add'}]}
        state=deepcopy(self.s);state['context'].pop('scene',None)
        for obj in [sku,product]:
            obj['attribute_tokens']=[t for t in obj['attribute_tokens'] if not t.startswith('heel:')]
            obj['attribute_tokens'].append('comfort:comfortable')
        text=preference_explanation(self.r,look,state,intent)
        self.assertNotIn('低跟',text);self.assertNotIn('平底',text)
        self.assertIn('舒适',text);self.assertIn('不能保证',text)
        sku['attribute_tokens'].append('heel:low')
        text=preference_explanation(self.r,look,state,intent)
        self.assertIn('低跟',text);self.assertNotIn('平底',text)

    def test_explicit_whole_look_overrides_browsed_category(self):
        first=deepcopy(self.h[-1]);self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        out=self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertNotEqual(self.h[-1]['items']['top'],first['items']['top'])
        self.assertNotEqual(self.h[-1]['items']['bottom'],first['items']['bottom'])

    def test_correction_uses_pre_error_clothes_not_wrong_current(self):
        first=deepcopy(self.h[-1])
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        wrong=deepcopy(self.h[-1]);history=deepcopy(self.h)
        self.assertNotEqual(wrong['items']['top'],first['items']['top'])
        out=self.run_intent({'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h,history)
        self.assertEqual(out['decision']['no_match']['scope_diagnostics']['base_look_version_id'],first['look_version_id'])
        self.assertIn('第 1 套',out['response']['text'])
        self.assertNotIn('三件合计',out['response']['text'])

    def test_successful_correction_uses_prior_base_but_current_parent_and_delta(self):
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        base=deepcopy(self.h[-1])
        self.run_intent({'operation':'modify_current','replace_slots':SLOTS,'carry_forward_unmentioned':False})
        current=deepcopy(self.h[-1]);history=deepcopy(self.h)
        out=self.run_intent({'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertEqual(self.h[:-1],history)
        new=self.h[-1]
        self.assertEqual(new['items']['top'],base['items']['top'])
        self.assertEqual(new['items']['bottom'],base['items']['bottom'])
        self.assertNotEqual(new['items']['shoes'],base['items']['shoes'])
        self.assertEqual(new['parent_look_version_id'],current['look_version_id'])
        self.assertEqual(new['delta_from_parent_fen'],new['total_price_fen']-current['total_price_fen'])
        self.assertEqual(len(self.h),len(history)+1)

    def test_uncertain_correction_requests_base_before_writing(self):
        before=deepcopy(self.s);history=deepcopy(self.h)
        out=self.run_intent({'operation':'modify_current','scope_reference':'previous_request','replace_slots':['shoes'],'carry_forward_unmentioned':True})
        self.assertEqual(out['decision']['status'],'clarification')
        self.assertEqual(self.h,history)
        self.assertIn('哪一套',out['response']['text'])
        self.assertEqual(self.s['current_look_version_id'],before['current_look_version_id'])

    def test_expanded_offer_is_not_executed_until_accepted(self):
        first=deepcopy(self.h[-1])
        out=self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        self.assertEqual(self.h[-1],first)
        alt=out['decision']['no_match']['scope_diagnostics']['verified_alternative']
        self.assertEqual(alt['replace_slots'],['top','bottom'])
        self.assertEqual(out['dialog_context']['proposal']['replace_slots'],['top','bottom'])
        out=self.run_intent({'operation':'modify_current','relaxation_response':'accept'})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertEqual(self.h[-1]['look_id'],alt['look_id'])
        self.assertEqual(self.h[-1]['items']['shoes'],first['items']['shoes'])

    def test_declined_scope_offer_is_not_repeated_or_executed(self):
        first=deepcopy(self.h[-1])
        self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        out=self.run_intent({'operation':'modify_current','relaxation_response':'decline'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.h[-1],first)
        self.assertIsNone(out['dialog_context']['proposal'])
        self.assertNotIn('是否允许',out['response']['text'])

    def test_declined_scope_stays_declined_on_generic_followup_but_new_budget_can_reoffer(self):
        self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True})
        self.run_intent({'operation':'modify_current','relaxation_response':'decline'})
        out=self.run_intent({'operation':'modify_current','scope_reference':'recent_topic'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertIsNone(out['dialog_context']['proposal'])
        self.assertNotIn('是否允许',out['response']['text'])
        out=self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True,'updates':{'budget_max_fen':60000}})
        self.assertIsNotNone(out['dialog_context']['proposal'])
        self.assertIn('是否允许',out['response']['text'])

    def test_recent_topic_offer_acceptance_really_expands_scope(self):
        self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        out=self.run_intent({'operation':'modify_current','scope_reference':'recent_topic'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertIsNone(out['dialog_context']['proposal']['scope_reference'])
        out=self.run_intent({'operation':'modify_current','relaxation_response':'accept'})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertEqual(self.h[-1]['items']['shoes'],'M3-S202-36')


if __name__=='__main__':unittest.main()
