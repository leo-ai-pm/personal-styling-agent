"""Regression for the user's 500-yuan budget refusal, through the real solver."""
import unittest
from m2.core import demo_resources, new_state, execute, size


class BudgetDeclineTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T08:00:00+00:00'
        self.r=demo_resources(self.at,False)
        self.state=new_state(self.r.data['tenant_id'],True,self.at)
        self.history=[];self.dialog=None;self.pending=None;self.turn=0

    def run_intent(self,intent):
        self.turn+=1
        out=execute(self.r,self.state,self.history,intent,str(self.turn),str(self.turn),self.at,self.pending,self.dialog)
        self.state=out['state'];self.history=out['history'];self.dialog=out['dialog_context'];self.pending=out['pending_intent']
        return out

    def test_budget_refusal_retries_at_original_budget_instead_of_asking_slots(self):
        self.run_intent({'updates':{'budget_max_fen':50000,'scene':'scene:work_commute'}})
        out=self.run_intent({'relaxation_response':'decline'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.state['context']['budget_max_fen'],50000)
        self.assertFalse(self.history)
        self.assertNotIn('更换哪些部分',out['response']['text'])
        self.assertNotIn('是否',out['response']['text'])
        self.assertIsNone(self.dialog['proposal'])

    def test_decline_with_no_executable_proposal_and_unmapped_sizes(self):
        out=self.run_intent({'updates':{'budget_max_fen':50000,'scene':'scene:work_commute','usual_sizes':{s:size(s,v) for s,v in [('top','S'),('bottom','S'),('shoes','36')]}}})
        self.assertIsNone(self.dialog['proposal'])
        out=self.run_intent({'relaxation_response':'decline'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.state['context']['budget_max_fen'],50000)
        self.assertEqual(self.state['profile']['usual_sizes']['shoes']['value'],'36')
        self.assertNotIn('更换哪些部分',out['response']['text'])

    def test_refusal_runs_solver_again_and_can_find_an_affordable_look(self):
        self.r.stock['M1V2-T102-M']['available_qty']=0
        self.run_intent({'updates':{'budget_max_fen':76000,'scene':'scene:work_commute'}})
        self.r.stock['M1V2-T102-M']['available_qty']=1
        out=self.run_intent({'relaxation_response':'decline'})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertLessEqual(self.history[-1]['total_price_fen'],76000)
        self.assertEqual(self.state['context']['budget_max_fen'],76000)

    def test_same_budget_is_not_reoffered_and_explicit_new_budget_wins(self):
        self.run_intent({'updates':{'budget_max_fen':50000,'scene':'scene:work_commute'}})
        self.run_intent({'relaxation_response':'decline'})
        out=self.run_intent({})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertIsNone(self.dialog['proposal'])
        self.assertNotIn('是否',out['response']['text'])
        out=self.run_intent({'updates':{'budget_max_fen':80000}})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertEqual(self.state['context']['budget_max_fen'],80000)

    def test_retry_does_not_reactivate_consumed_keep(self):
        self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute'}})
        out=self.run_intent({'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True,'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}],'updates':{'budget_max_fen':50000}})
        keep_ids=[c['constraint_id'] for c in self.state['constraints'] if c['kind']=='keep_sku']
        self.assertTrue(out['decision']['state_transition']['consumed_constraint_ids'])
        out=self.run_intent({'relaxation_response':'decline'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(out['decision']['interpretation']['replace_slots'],['top'])
        self.assertEqual([c['constraint_id'] for c in self.state['constraints'] if c['kind']=='keep_sku'],keep_ids)
        self.assertFalse(out['decision']['state_transition']['activated_constraint_ids'])


if __name__=='__main__':unittest.main()
