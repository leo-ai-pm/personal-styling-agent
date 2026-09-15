"""Isolated scripted transport for browser QA; never used by m2.server.
This tests UI + real business layer; it does NOT test natural-language understanding.
"""
from copy import deepcopy
from m2.core import demo_resources,Fault
from m2.service import Service
from m2.server import make_server

INTENTS={
 '预算800，帮我搭一套':{'updates':{'budget_max_fen':80000}},
 '平时上班穿，舒服一点':{'updates':{'scene':'scene:work_commute'},'preference_deltas':[{'attribute_token':'prefer:comfortable','direction':'add'}]},
 '鞋子挺好，衣服换得更休闲一点':{'operation':'modify_current','replace_slots':['top','bottom'],'carry_forward_unmentioned':True,'keep_directives':[{'slot':'shoes','scope':'next_recommendation'}],'preference_deltas':[{'attribute_token':'style:casual','direction':'increase'}]},
 '鞋也换掉吧，换双浅色的':{'operation':'modify_current','replace_slots':['shoes'],'release_slots':['shoes'],'carry_forward_unmentioned':True,'preference_deltas':[{'attribute_token':'color:light','direction':'increase','slot':'shoes'}]},
 '我鞋码是39':{'updates':{'usual_sizes':{'shoes':{'system':'cn_shoe','value':'39'}}}},
 '我鞋码是38':{'updates':{'usual_sizes':{'shoes':{'system':'cn_shoe','value':'38'}}}},
 '只换上衣，其他别动':{'operation':'modify_current','replace_slots':['top'],'carry_forward_unmentioned':True},
 '模拟模型超时':None,
}

class ScriptedBrowserModel:
    configured=True
    mode='structured_test_injection'
    def parse(self,text,*args):
        if text not in INTENTS:raise Fault('TEST_INPUT_NOT_REGISTERED','仅限浏览器 QA 的登记测试输入。')
        if INTENTS[text] is None:raise Fault('MODEL_UNAVAILABLE','测试注入：模型超时，当前方案保持不变。')
        return deepcopy(INTENTS[text]),{'mode':self.mode,'model':None,'cost':0,'input_tokens':0,'output_tokens':0}

if __name__=='__main__':
    print('TEST ONLY: scripted intents on http://127.0.0.1:8766',flush=True)
    make_server(Service(demo_resources(),ScriptedBrowserModel()),8766).serve_forever()
