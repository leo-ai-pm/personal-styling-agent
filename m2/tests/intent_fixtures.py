"""Hand-authored semantic inputs for L2–L5 tests only. Never available to live parser.
These are not NLU predictions and cannot establish model accuracy.
"""
from copy import deepcopy
from m2.core import size


def pref(token,direction='add',slot=None):
    x={'attribute_token':token,'direction':direction}
    if slot:x['slot']=slot
    return x


def change(token=None,keep=None,slots=None):
    i={'operation':'modify_current','replace_slots':slots or ['top','bottom','shoes'],'carry_forward_unmentioned':slots is not None}
    if token:i['preference_deltas']=[pref(token,'increase')]
    if keep:i['keep_directives']=[{'slot':'shoes','scope':keep}]
    return i


def cases():
    budget={'updates':{'budget_max_fen':80000}}
    commute={'updates':{'scene':'scene:work_commute','budget_max_fen':80000}}
    casual=change('style:casual','next_recommendation',['top','bottom'])
    return {
     'P0-CLAR-001':[budget,{'updates':{'scene':'scene:work_commute'},'preference_deltas':[pref('style:comfortable')]}],
     'P0-CLAR-002':[commute,{'updates':{'usual_sizes':{s:size(s,'M') for s in ['top','bottom']}}}],
     'P0-CLAR-003':[{'updates':{'height_cm':158,'weight_kg':50,'usual_sizes':{'top':size('top','M'),'bottom':size('bottom','M'),'shoes':size('shoes','37')},'budget_max_fen':80000,'scene':'scene:work_commute'},'preference_deltas':[pref('style:comfortable')]}],
     'P0-CONSTRAINT-001':[commute],'P0-CONSTRAINT-002':[dict(budget,preference_deltas=[pref('style:formal')])],
     'P0-CONSTRAINT-003':[commute],'P0-CONSTRAINT-004':[{}],'P0-CONSTRAINT-005':[{}],
     'P0-CONSTRAINT-006':[budget],'P0-CONSTRAINT-007':[{}],
     'P0-CONSTRAINT-008':[dict(budget,rejections=[{'kind':'forbid_attribute','slot':'top','value':'color:red','action':'add'}])],
     'P0-KEEP-001':[casual],'P0-KEEP-002':[casual,change('style:feminine')],
     'P0-KEEP-003':[change('style:casual','session',['top','bottom']),change('style:feminine')],
     'P0-KEEP-004':[dict(change('color:light',slots=['shoes']),release_slots=['shoes'])],
     'P0-KEEP-005':[dict(change(keep='next_recommendation',slots=['top','bottom']),preference_deltas=[pref('style:feminine')])],
     'P0-KEEP-006':[{'operation':'modify_current','ambiguous':True}],
     'P0-REJECT-001':[dict(change(),rejections=[{'kind':'forbid_attribute','slot':'bottom','value':'color:black','action':'add'}])],
     'P0-REJECT-002':[dict(change('style:casual'),rejections=[{'kind':'forbid_sku','slot':'top','value':'SYN-TOP-WHITE-M','action':'add'}])],
     'P0-REJECT-003':[dict(change('style:feminine'),rejections=[{'kind':'forbid_attribute','slot':'top','value':'color:red','action':'remove'}])],
     'P0-HISTORY-001':[change('style:casual')],'P0-HISTORY-002':[{'operation':'view_history','target_look_version_id':'lv-1'}],
     'P0-HISTORY-003':[{'operation':'restore_history','target_look_version_id':'lv-1'}],
     'P0-DIALOG-001':[{'operation':'modify_current','ambiguous':True}],
     'P0-BOUNDARY-001':[{'operation':'scope_redirect','scope_reason':'USER_PHOTO_TRY_ON'}],
     'P0-BOUNDARY-002':[{'operation':'scope_redirect','scope_reason':'OUT_OF_SCOPE_DOMAIN'}],
     'P0-GROUNDING-001':[{}],'P0-GROUNDING-002':[{}],
     'P0-EVENT-001':[{'operation':'add_bundle_to_cart','accepted':True}],'P0-EVENT-002':[casual],
     'P0-CONSTRAINT-009':[commute],'P0-CONSTRAINT-010':[commute],
     'P0-EVENT-003':[{'operation':'add_bundle_to_cart'}],
     'P0-HISTORY-004':[{'operation':'restore_history','target_look_version_id':'lv-1'}]
    }
