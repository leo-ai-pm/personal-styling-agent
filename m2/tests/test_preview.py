import unittest
import json
from pathlib import Path
from copy import deepcopy
from m2.preview import preview_resources
from m2.core import ROOT,execute,new_state,size,Fault,Resources

class PreviewTests(unittest.TestCase):
    def test_four_budget_size_journeys_have_images_and_keep_then_replace_shoes(self):
        r=preview_resources();at=r.inventory['captured_at']
        for clothes,shoe in [('S','36'),('M','38')]:
            for budget in [50000,80000]:
                with self.subTest(clothes=clothes,budget=budget):
                    state=new_state(r.data['tenant_id'],False,at);history=[]
                    intents=[{'updates':{'budget_max_fen':budget,'scene':'scene:work_commute','usual_sizes':{s:size(s,v) for s,v in [('top',clothes),('bottom',clothes),('shoes',shoe)]}}},
                             {'operation':'modify_current','replace_slots':['top','bottom'],'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}],'preference_deltas':[{'attribute_token':'style:casual','direction':'increase'}]},
                             {'operation':'modify_current','replace_slots':['shoes'],'required_change_slots':['shoes'],'carry_forward_unmentioned':True,'preference_deltas':[{'attribute_token':'color:light','direction':'increase','slot':'shoes'}]}]
                    for i,intent in enumerate(intents):
                        out=execute(r,state,history,intent,str(i),str(i),at);self.assertEqual(out['decision']['status'],'recommendation');state=out['state'];history=out['history'];look=history[-1]
                        self.assertLessEqual(look['total_price_fen'],budget);r.check_image(r.looks[look['look_id']])
                    self.assertEqual(history[0]['items']['shoes'],history[1]['items']['shoes'])
                    self.assertNotEqual(history[1]['items']['shoes'],history[2]['items']['shoes'])
    def test_source_approval_and_frozen_images_not_promoted(self):
        source=ROOT/'data/m3_draft/look_assets.json';before=source.read_bytes();r=preview_resources()
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual(len(r.looks),13);self.assertEqual(len(r.products),12)
        self.assertTrue(all(not l['active'] for l in json.loads(before) if l['look_id'].startswith('M3')))
        self.assertTrue(r.clone(r.data).preview)
    def test_candidate_images_require_explicit_preview_mode(self):
        r=preview_resources()
        with self.assertRaises(Fault):Resources(r.data,display=True)
    def test_image_digest_and_path_tampering_fail_closed(self):
        r=preview_resources();look=deepcopy(next(l for l in r.looks.values() if l['look_id'].startswith('M3')))
        look['image_sha256']='0'*64
        with self.assertRaises(Fault):r.check_image(look)
        look['image_url']='/data/m3_draft/assets/../../manifest.json'
        with self.assertRaises(Fault):r.check_image(look)
