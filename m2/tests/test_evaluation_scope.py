import unittest
from copy import deepcopy
from m2.core import demo_resources,new_state,execute
from m2.evaluate import independent_recommendation_check


class CorrectedScopeEvaluationTests(unittest.TestCase):
    def test_corrected_baseline_is_ancestor_but_parent_and_price_use_actual_current(self):
        at='2026-09-05T12:00:00+00:00';r=demo_resources(at,False)
        s=new_state(r.data['tenant_id'],True,at);h=[];dialog=None
        intents=[{'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}},
                 {'operation':'modify_current','replace_slots':['top','bottom','shoes']},
                 {'operation':'modify_current','replace_slots':['top'],'scope_reference':'previous_request','carry_forward_unmentioned':True}]
        for index,intent in enumerate(intents):
            before=deepcopy(s)
            result=execute(r,s,h,intent,str(index),str(index),at,dialog_context=dialog)
            s=result['state'];h=result['history'];dialog=result['dialog_context']
        self.assertEqual(result['decision']['status'],'recommendation')
        result['state_before']=before
        self.assertTrue(independent_recommendation_check([result],r))
        corrupt=deepcopy(result)
        corrupt['decision']['interpretation']['comparison_base_look_version_id']='not-an-ancestor'
        self.assertFalse(independent_recommendation_check([corrupt],r))
        corrupt=deepcopy(result)
        corrupt['decision']['recommendation']['look_version']['parent_look_version_id']=h[0]['look_version_id']
        self.assertFalse(independent_recommendation_check([corrupt],r))
        corrupt=deepcopy(result)
        corrupt['decision']['recommendation']['look_version']['delta_from_parent_fen']=999
        self.assertFalse(independent_recommendation_check([corrupt],r))


if __name__=='__main__':unittest.main()
