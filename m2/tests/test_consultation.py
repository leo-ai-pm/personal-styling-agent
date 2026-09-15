import unittest
from copy import deepcopy
from m2.core import demo_resources,new_state,execute,size,Fault
from m2.evaluate import events_valid


class ConsultationTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T12:00:00+00:00';self.r=demo_resources(self.at,False)
        self.s=new_state(self.r.data['tenant_id'],True,self.at);self.h=[];self.d=None;self.p=None;self.n=0

    def run_intent(self,intent):
        self.n+=1
        o=execute(self.r,self.s,self.h,intent,str(self.n),str(self.n),self.at,self.p,self.d)
        self.s=o['state'];self.h=o['history'];self.d=o['dialog_context'];self.p=o['pending_intent']
        self.assertTrue(events_valid(o['events']))
        return o

    def initial(self):
        return self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}})

    def ask(self,topic,reference='current',**kw):
        return self.run_intent({'operation':'consult','inquiry':dict(topic=topic,reference=reference,**kw)})

    def test_current_shoe_price_only(self):
        self.initial();before=deepcopy(self.s)
        o=self.ask('price',slot='shoes');data=o['decision']['action']['inquiry_result']
        self.assertEqual(data['resolved_sku_ids'],['M1V2-S101-38'])
        self.assertIn('269',o['response']['text']);self.assertNotIn('249',o['response']['text'])
        self.assertEqual(self.s,before)

    def test_single_purchase_explains_boundary_without_cart(self):
        self.initial();before=deepcopy(self.s)
        o=self.ask('purchase',slot='shoes')
        self.assertIn('单件',o['response']['text'])
        self.assertNotIn('暂不支持单件加购',o['response']['text'])
        self.assertNotIn('single_item_cart',o['decision']['action']['inquiry_result']['unsupported_features'])
        self.assertEqual(o['cart_added_sku_ids'],[]);self.assertEqual(self.s,before)

    def test_material_does_not_invent_leather_or_cotton(self):
        self.initial();o=self.ask('material',slot='shoes')
        self.assertIn('未提供',o['response']['text'])
        self.assertNotIn('真皮',o['response']['text']);self.assertNotIn('纯棉',o['response']['text'])

    def test_unavailable_current_item_is_not_replaced(self):
        self.initial();self.r.stock['M1V2-S101-38']['available_qty']=0
        o=self.ask('availability',slot='shoes')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-S101-38'])
        self.assertIn('无可售库存',o['response']['text'])

    def test_visible_history_is_distinct_from_current(self):
        self.initial();first=self.h[0]['look_version_id']
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes']})
        self.d={**(self.d or {}),'view_context':{'look_version_id':first}}
        before=deepcopy(self.s);o=self.ask('price','visible',slot='top')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-T101-M'])
        self.assertEqual(self.s,before)

    def test_catalog_item_can_be_followed_by_material(self):
        self.initial();self.run_intent({'operation':'browse_catalog','query_slots':['shoes']})
        self.ask('price','catalog',sku_id='M1V2-S102-38')
        o=self.ask('material','last_mentioned')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-S102-38'])

    def test_no_target_does_not_destroy_pending_outfit_question(self):
        self.run_intent({'updates':{'budget_max_fen':80000}});before=deepcopy(self.s);pending=deepcopy(self.p)
        o=self.ask('price',slot='shoes')
        self.assertEqual(o['decision']['status'],'clarification')
        self.assertEqual(self.s,before);self.assertEqual(self.p,pending)

    def test_acknowledgement_does_not_trigger_an_outfit(self):
        before=deepcopy(self.s);o=self.ask('acknowledgement','app')
        self.assertEqual(o['decision']['status'],'action_completed');self.assertEqual(self.s,before)

    def test_readonly_inquiry_cannot_raise_budget(self):
        self.initial();before=deepcopy(self.s)
        self.run_intent({'operation':'consult','inquiry':{'topic':'price','reference':'current','slot':'shoes'},'updates':{'budget_max_fen':90000},'relaxation_response':'accept'})
        self.assertEqual(self.s,before)

    def test_history_reference_must_exist(self):
        self.initial();before=deepcopy(self.s)
        o=self.ask('price','history',look_version_id='missing-target',slot='shoes')
        self.assertEqual(o['decision']['status'],'clarification');self.assertEqual(self.s,before)

    def test_cancel_preserves_budget_history_and_persistent_keep(self):
        self.initial();before_history=deepcopy(self.h)
        self.run_intent({'operation':'modify_current','keep_directives':[{'slot':'shoes','scope':'session'}],'ambiguous':True})
        o=self.run_intent({'operation':'cancel_pending'})
        self.assertEqual(o['decision']['status'],'action_completed');self.assertEqual(self.s['context']['budget_max_fen'],80000)
        self.assertEqual(self.h,before_history);self.assertIsNone(self.p);self.assertIsNone(self.s['pending_clarification'])

    def test_inquiry_invalid_id_or_topic_rejected(self):
        for q in [{'topic':'price','reference':'catalog','sku_id':'imaginary'},{'topic':'invent','reference':'app'}]:
            with self.assertRaises(Fault):self.run_intent({'operation':'consult','inquiry':q})

    def test_material_followup_switches_slot_in_same_focus_look(self):
        self.initial();first=self.h[0]['look_version_id']
        self.ask('material',slot='top')
        o=self.ask('material','last_mentioned',slot='shoes')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-S101-38'])
        self.assertEqual(o['decision']['action']['inquiry_result']['look_version_id'],first)
        self.assertIn('未提供',o['response']['text'])

    def test_missing_item_reference_never_defaults_to_full_look_or_first_product(self):
        o=self.ask('price');self.assertEqual(o['decision']['status'],'clarification')
        self.initial();o=self.ask('price');self.assertEqual(o['decision']['status'],'clarification')
        o=self.ask('price',whole_look=True)
        self.assertEqual(len(o['decision']['action']['inquiry_result']['resolved_sku_ids']),3)

    def test_explicit_sku_and_product_narrow_current_outfit(self):
        self.initial();sid='M1V2-S101-38';pid=self.r.skus[sid]['product_id']
        for field,value in [('sku_id',sid),('product_id',pid)]:
            o=self.ask('price',**{field:value})
            self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],[sid])
            self.assertNotIn('三件',o['response']['text'])

    def test_verbal_history_focus_survives_readonly_inquiry(self):
        self.initial();first=self.h[0]['look_version_id']
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes']})
        self.run_intent({'operation':'view_history','target_look_version_id':first})
        self.assertEqual(self.d['viewed_look_version_id'],first)
        o=self.ask('price','visible',slot='top')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-T101-M'])
        self.d['view_context']={'look_version_id':None}
        o=self.ask('price','visible',slot='top')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],[self.h[-1]['items']['top']])

    def test_unavailable_formal_style_is_not_claimed_as_fulfilled(self):
        o=self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'},'preference_deltas':[{'attribute_token':'dress_code:formal','direction':'add'}]})
        self.assertEqual(o['decision']['status'],'recommendation')
        self.assertIn('正式',o['response']['text']);self.assertIn('未满足',o['response']['text'])

    def test_whole_look_cannot_be_combined_with_specific_item(self):
        with self.assertRaises(Fault):self.ask('price',whole_look=True,slot='shoes')

    def test_whole_outfit_redesign_must_change_explicit_shoes(self):
        self.initial();shoes=self.h[-1]['items']['shoes']
        o=self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes'],'release_slots':['shoes'],'required_change_slots':['shoes'],'carry_forward_unmentioned':False})
        self.assertEqual(o['decision']['status'],'recommendation')
        self.assertNotEqual(self.h[-1]['items']['shoes'],shoes)

    def test_permission_to_change_is_not_a_requirement(self):
        self.initial();shoes=self.h[-1]['items']['shoes']
        o=self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes'],'release_slots':['shoes'],'carry_forward_unmentioned':False})
        self.assertEqual(o['decision']['status'],'recommendation')
        self.assertEqual(self.h[-1]['items']['shoes'],shoes)

    def test_catalog_ordinal_is_resolved_from_actual_list_and_bounds_checked(self):
        self.initial();self.run_intent({'operation':'browse_catalog','query_slots':['top']})
        expected=self.d['last_catalog_result'][1]['variants'][0]['sku_id']
        o=self.ask('price','catalog',catalog_position=2)
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],[expected])
        before=deepcopy(self.s);o=self.ask('price','catalog',catalog_position=9)
        self.assertEqual(o['decision']['status'],'clarification');self.assertIn('只有 2',o['response']['text'])
        self.assertEqual(self.s,before)

    def test_whole_bundle_purchase_question_never_adds_cart(self):
        self.initial();before=deepcopy(self.s)
        o=self.ask('purchase',whole_look=True)
        self.assertEqual(o['cart_added_sku_ids'],[]);self.assertEqual(self.s,before)
        self.assertIn('整套',o['response']['text'])
        self.assertIn('模拟购物车',o['response']['text'])
        self.assertEqual(o['decision']['action']['operation'],'consult')

    def test_catalog_variants_are_aggregated_in_text_but_preserved_as_facts(self):
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        pid=self.r.skus['M1V2-T101-M']['product_id']
        self.r.stock['M1V2-T101-XL']['available_qty']=1
        self.r.stock['M1V2-T101-XL']['sellable']=True
        o=self.ask('price','catalog',product_id=pid)
        self.assertEqual(len(o['decision']['action']['inquiry_result']['facts']),4)
        self.assertEqual(o['response']['text'].count('奶油白不对称无袖剪裁上衣'),1)
        self.assertIn('S / M / L / XL',o['response']['text'])
        o=self.ask('material','catalog',product_id=pid)
        self.assertEqual(o['response']['text'].count('商品资料暂未提供'),1)

    def test_unmapped_size_does_not_question_user_or_offer_useless_budget_raise(self):
        o=self.run_intent({'updates':{'budget_max_fen':50000,'scene':'scene:work_commute','usual_sizes':{slot:size(slot,value) for slot,value in [('top','S'),('bottom','S'),('shoes','36')]}}})
        self.assertEqual(o['decision']['status'],'no_match')
        self.assertIn('搭配图暂未覆盖',o['response']['text'])
        self.assertNotIn('是否正确',o['response']['text']);self.assertNotIn('我会在本次',o['response']['text'])
        self.assertIsNone(self.d['proposal'])

    def test_size_correction_targets_named_slot_and_acknowledges_new_value(self):
        self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute','usual_sizes':{slot:size(slot,value) for slot,value in [('top','S'),('bottom','S'),('shoes','36')]}}})
        o=self.run_intent({'confirmation_response':'correct','size_correction_slots':['shoes']})
        self.assertEqual(o['decision']['clarification']['field'],'shoes_size')
        self.assertIn('鞋',o['response']['text']);self.assertNotIn('哪一项',o['response']['text'])
        o=self.run_intent({'updates':{'usual_sizes':{'shoes':size('shoes','38')}}})
        self.assertIn('鞋码更新为 38',o['response']['text'])
        self.assertNotIn('尺码与预算保持不变',o['response']['text'])

    def test_initial_scene_is_explained_from_real_product_attributes(self):
        o=self.initial()
        self.assertIn('通勤',o['decision']['recommendation']['reason_text'])
        self.assertTrue(any(word in o['decision']['recommendation']['reason_text'] for word in ['阔腿','低跟','剪裁']))

    def test_old_item_focus_cannot_override_new_visible_history(self):
        self.initial();first=self.h[0]
        self.run_intent({'operation':'modify_current','replace_slots':['top','bottom','shoes'],'required_change_slots':['shoes']})
        latest=self.h[-1];self.ask('purchase',slot='shoes')
        self.run_intent({'operation':'view_history','target_look_version_id':first['look_version_id']})
        self.d['view_context']={'look_version_id':first['look_version_id']}
        o=self.ask('price','last_mentioned',slot='shoes')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],[first['items']['shoes']])
        self.d['view_context']={'look_version_id':None}
        o=self.ask('price','last_mentioned',slot='shoes')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],[latest['items']['shoes']])

    def test_catalog_focus_survives_unchanged_visible_current(self):
        self.initial();self.d={'view_context':{'look_version_id':None}}
        self.ask('price','catalog',sku_id='M1V2-S102-38')
        self.d['view_context']={'look_version_id':None}
        o=self.ask('material','last_mentioned')
        self.assertEqual(o['decision']['action']['inquiry_result']['resolved_sku_ids'],['M1V2-S102-38'])


if __name__=='__main__':unittest.main()
