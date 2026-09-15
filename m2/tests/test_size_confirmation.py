"""Size verification is a confirmation of facts, not permission to relax constraints."""
import unittest
from copy import deepcopy
from m2.core import demo_resources, new_state, execute, size


class SizeConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.at='2026-09-05T10:00:00+00:00'
        self.r=demo_resources(self.at,False)
        self.s=new_state(self.r.data['tenant_id'],False,self.at)
        self.h=[];self.dialog=None;self.pending=None;self.turn=0

    def run_intent(self,i):
        self.turn+=1
        out=execute(self.r,self.s,self.h,i,str(self.turn),str(self.turn),self.at,self.pending,self.dialog)
        self.s=out['state'];self.h=out['history'];self.dialog=out['dialog_context'];self.pending=out['pending_intent']
        return out

    def ask_size(self):
        out=self.run_intent({'updates':{'budget_max_fen':80000,'scene':'scene:work_commute','usual_sizes':{'top':size('top','S'),'bottom':size('bottom','S')}}})
        self.assertEqual(out['decision']['clarification']['field'],'shoes_size')
        out=self.run_intent({'updates':{'usual_sizes':{'shoes':size('shoes','36')}}})
        self.assertEqual(out['decision']['status'],'no_match')

    def test_confirm_preserves_sizes_budget_and_constraints_without_reasking(self):
        self.ask_size();before=deepcopy(self.s)
        out=self.run_intent({'confirmation_response':'confirm'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.s,before)
        self.assertEqual(self.h,[])
        self.assertIn('搭配图',out['response']['text'])
        self.assertNotIn('是否',out['response']['text'])
        self.assertNotIn('更换哪些部分',out['response']['text'])
        self.assertFalse(any(e['event_type']=='constraint_changed' for e in out['events']))

    def test_affirmative_mislabel_is_routed_by_size_question(self):
        self.ask_size();before=deepcopy(self.s)
        out=self.run_intent({'relaxation_response':'accept'})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertEqual(self.s,before)
        self.assertNotIn('是否',out['response']['text'])

    def test_confirmed_values_are_not_rechecked_on_retry(self):
        self.ask_size();self.run_intent({'confirmation_response':'confirm'})
        out=self.run_intent({})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertNotIn('是否',out['response']['text'])
        self.assertEqual(self.s['profile']['usual_sizes']['shoes']['value'],'36')

    def test_wrong_sizes_without_replacement_asks_for_correction(self):
        self.ask_size();before=deepcopy(self.s['profile'])
        out=self.run_intent({'confirmation_response':'correct'})
        self.assertEqual(out['decision']['status'],'clarification')
        self.assertEqual(out['decision']['clarification']['field'],'usual_sizes')
        self.assertEqual(self.s['profile'],before)
        self.assertEqual(self.s['context']['budget_max_fen'],80000)

    def test_explicit_size_correction_can_recommend(self):
        self.ask_size();self.run_intent({'confirmation_response':'correct'})
        out=self.run_intent({'updates':{'usual_sizes':{s:size(s,v) for s,v in [('top','M'),('bottom','M'),('shoes','38')]}}})
        self.assertEqual(out['decision']['status'],'recommendation')
        self.assertLessEqual(self.h[-1]['total_price_fen'],80000)

    def test_changed_size_invalidates_old_confirmation(self):
        self.ask_size();self.run_intent({'confirmation_response':'confirm'})
        out=self.run_intent({'updates':{'usual_sizes':{'shoes':size('shoes','39')}}})
        self.assertEqual(out['decision']['status'],'no_match')
        self.assertIn('暂未覆盖',out['response']['text'])
        self.assertNotIn('是否正确',out['response']['text'])
        self.assertNotEqual(self.dialog.get('confirmed_sizes'),self.s['profile']['usual_sizes'])

    def test_size_confirmation_never_authorizes_budget_proposal(self):
        out=self.run_intent({'updates':{'budget_max_fen':70000,'scene':'scene:work_commute','usual_sizes':{s:size(s,v) for s,v in [('top','M'),('bottom','M'),('shoes','38')]}}})
        self.assertEqual(self.dialog['proposal']['updates']['budget_max_fen'],73700)
        out=self.run_intent({'confirmation_response':'confirm'})
        self.assertEqual(self.s['context']['budget_max_fen'],70000)
        self.assertFalse(self.h)


if __name__=='__main__':unittest.main()
