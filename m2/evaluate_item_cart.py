"""Independent live-model regression for single-item cart and dialogue follow-ups.

Every case runs in a new in-memory Service; deterministic fixture setup is explicitly
separate from scored live turns. No online HTTP endpoint or SQLite store is used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from .core import ROOT, Fault, validate_intent
from .model import CompatibleModel, PROMPT_VERSION, PROMPT, GUARD_VERSION, GUARD_PROMPT
from .preview import preview_resources
from .runtime import RequestLimits
from .service import Service

DATASET = ROOT / 'evals/item-cart-followup/cases_v3.json'
SOURCE_FILES = ['core.py', 'model.py', 'cart.py', 'service.py', 'consultation.py', 'dialogue.py', 'preview.py',
                'catalog.py', 'runtime.py', 'storage.py', 'config.py']

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_hashes():
    return {str(Path('m2') / name): digest(ROOT / 'm2' / name) for name in SOURCE_FILES}

def sizes(clothes, shoe):
    return {slot: {'system': 'cn_shoe' if slot == 'shoes' else 'clothing_alpha', 'value': shoe if slot == 'shoes' else clothes}
            for slot in ['top', 'bottom', 'shoes']}

class RecordingModel(CompatibleModel):
    def __init__(self):
        super().__init__()
        self.fixture_intent = None
        self.last_intent = None
        self.last_responses = []

    def parse(self, text, state, history, resources, dialog_context=None):
        self.last_intent = None
        self.last_responses = []
        if self.fixture_intent is not None:
            intent = validate_intent(deepcopy(self.fixture_intent), resources)
            self.fixture_intent = None
            meta = dict(mode='structured_fixture_setup', model=None, input_tokens=0, output_tokens=0, api_calls=0)
        else:
            intent, meta = super().parse(text, state, history, resources, dialog_context)
        self.last_intent = deepcopy(intent)
        return intent, meta

    def _request(self, payload, deadline, stage):
        data, duration = super()._request(payload, deadline, stage)
        choice = data.get('choices', [{}])[0]
        self.last_responses.append(dict(stage=stage, model=data.get('model'),
                                        content=choice.get('message', {}).get('content'),
                                        finish_reason=choice.get('finish_reason'),
                                        usage=deepcopy(data.get('usage', {})), duration_ms=duration))
        return data, duration

def request_body(view, utterance=None, action=None, view_history_index=None):
    body = dict(contract_version='0.1.0', request_id=str(uuid.uuid4()), turn_id=str(uuid.uuid4()),
                idempotency_key=str(uuid.uuid4()), expected_state_revision=view['state']['state_revision'],
                expected_conversation_revision=view['conversation_revision'])
    if action is not None:
        action = deepcopy(action)
        if 'history_index' in action:
            index = action.pop('history_index')
            action['target_look_version_id'] = view['history'][index]['look_version_id']
        body['action'] = action
    else:
        body['user_utterance'] = utterance
    if view_history_index is not None:
        body['view_context'] = {'look_version_id': view['history'][view_history_index]['look_version_id']}
    return body

def fixture_setup(service, model, sid, name):
    if name == 'none':
        return []
    clothes, shoe, budget = ('M', '38', 80000) if name == 'high_m' else ('M', '38', 50000) if name == 'low_m' else ('S', '36', 50000)
    initial = {'updates': {'budget_max_fen': budget, 'scene': 'scene:work_commute', 'usual_sizes': sizes(clothes, shoe)}}
    scripted = [(f'预算{budget/100:g}，上衣下装{clothes}，鞋{shoe}，日常通勤，帮我搭一套', initial)]
    if name == 'history_low_s':
        scripted.append(('整套重新搭配，休闲一些', {'operation': 'modify_current', 'replace_slots': ['top', 'bottom', 'shoes'],
                                                    'carry_forward_unmentioned': False,
                                                    'preference_deltas': [{'attribute_token': 'style:casual', 'direction': 'increase'}]}))
    elif name == 'old_bug_low_s':
        scripted.append(('鞋子还有别的吗？', {'operation': 'browse_catalog', 'query_slots': ['shoes']}))
        # Inject the documented old error to verify recovery from an already wrong result.
        scripted.append(('你帮我搭配', {'operation': 'modify_current', 'replace_slots': ['top', 'bottom', 'shoes'], 'carry_forward_unmentioned': False}))
    records = []
    for text, intent in scripted:
        before = service.view(sid); model.fixture_intent = intent
        result = service.turn(sid, request_body(before, text))
        assert result['metrics']['mode'] == 'structured_fixture_setup'
        records.append(dict(utterance=text, injected_intent=intent, result=result))
    return records

def resolve_sku(value, view):
    if not value.startswith('$'):
        return value
    if value.startswith('$current.'):
        slot = value.split('.')[-1]
        current = next(v for v in view['history'] if v['look_version_id'] == view['state']['current_look_version_id'])
        return current['items'][slot]
    raise ValueError('unsupported test binding: ' + value)

def mutate_resources(service, mutations, before):
    observed = []
    for mutation in mutations:
        sku_id = resolve_sku(mutation['sku'], before)
        field, value = mutation['field'], mutation['value']
        if field == 'stock_qty':
            service.r.stock[sku_id]['available_qty'] = value
        elif field == 'price_delta':
            service.r.skus[sku_id]['price_fen'] += value
        elif field == 'product_active':
            service.r.products[service.r.skus[sku_id]['product_id']]['active'] = value
        else:
            raise ValueError('unsupported test mutation: ' + field)
        observed.append(dict(mutation, resolved_sku_id=sku_id))
    return observed

def check_turn(expected, record, resources):
    checks = []
    def add(name, passed, actual=None, wanted=None):
        checks.append(dict(check=name, passed=bool(passed), actual=actual, expected=wanted))
    if record.get('error'):
        add('request_completed', False, record['error'], 'no model or application exception')
        return checks
    result, before, after = record['result'], record['before'], record['after']
    decision = result['decision']; parsed = record['parsed_intent'] or {}
    effective = decision['interpretation']; action = decision.get('action') or {}
    text = result['response']['text']
    for key, wanted in expected.items():
        if key == 'statuses':
            add(key, decision['status'] in wanted, decision['status'], wanted)
        elif key == 'operation':
            actual = effective['operation']; add(key, actual == wanted, actual, wanted)
        elif key in ['replace_slots', 'query_slots']:
            actual = effective.get(key, parsed.get(key, [])); add(key, sorted(actual) == sorted(wanted), actual, wanted)
        elif key == 'carry_forward_unmentioned':
            actual = effective.get(key); add(key, actual is wanted, actual, wanted)
        elif key == 'cart_delta':
            actual = len(after['cart']) - len(before['cart']); add(key, actual == wanted, actual, wanted)
        elif key == 'cart_sku':
            actual = result.get('cart_added_sku_ids', []); sid = resolve_sku(wanted, before)
            add(key, actual == [sid], actual, [sid])
        elif key in ['quantity', 'unit_price_fen']:
            actual = action.get(key); add(key, actual == wanted, actual, wanted)
        elif key == 'history_unchanged':
            add(key, (after['history'] == before['history']) is wanted, after['history'] == before['history'], wanted)
        elif key == 'profile_unchanged':
            add(key, (after['state']['profile'] == before['state']['profile']) is wanted, after['state']['profile'] == before['state']['profile'], wanted)
        elif key == 'event':
            types = [e['event_type'] for e in result['events']]; add(key, wanted in types, types, wanted)
        elif key == 'pending_cart':
            actual = bool((record['dialog_after'] or {}).get('pending_cart')); add(key, actual is wanted, actual, wanted)
        elif key == 'proposal_present':
            actual = bool((record['dialog_after'] or {}).get('proposal')); add(key, actual is wanted, actual, wanted)
        elif key == 'clarification_field':
            actual = decision.get('clarification', {}).get('field'); add(key, actual == wanted, actual, wanted)
        elif key == 'look_id':
            current = next((v for v in after['history'] if v['look_version_id'] == after['state']['current_look_version_id']), None)
            actual = current['look_id'] if current else None; add(key, actual == wanted, actual, wanted)
        elif key == 'same_slots':
            old = next(v for v in before['history'] if v['look_version_id'] == before['state']['current_look_version_id'])
            current = next(v for v in after['history'] if v['look_version_id'] == after['state']['current_look_version_id'])
            add(key, all(current['items'][slot] == old['items'][slot] for slot in wanted), current['items'], {slot: old['items'][slot] for slot in wanted})
        elif key == 'recommended_attributes':
            if decision['status'] == 'recommendation':
                look = decision['recommendation']['look_version']
                for slot, token in wanted:
                    add(key + ':' + slot + ':' + token, resources.hit(look, token, [slot]) > 0, look['items'][slot], token)
        elif key == 'history_unchanged_on_no_match':
            if decision['status'] == 'no_match':
                add(key, after['history'] == before['history'], after['history'] == before['history'], wanted)
        elif key == 'text_concepts':
            for group in wanted:
                add('text_concept:' + '|'.join(group), any(word in text for word in group), text, group)
        elif key == 'text_not_regex':
            add(key, re.search(wanted, text) is None, text, wanted)
        elif key == 'model_calls':
            add(key, record['actual_model_calls'] == wanted, record['actual_model_calls'], wanted)
        elif key == 'identical_replay':
            add(key, record['replay_equal'] is wanted, record['replay_equal'], wanted)
        else:
            raise ValueError('unknown expectation: ' + key)
    # Independent cart arithmetic, stock and no-unrequested-side-effect checks.
    if result.get('cart_added_sku_ids') and action.get('operation') == 'add_item_to_cart':
        ids = result['cart_added_sku_ids']
        add('single_sku_only', len(ids) == 1 and action.get('sku_ids') == ids, ids, 'one actual SKU')
        if len(ids) == 1 and ids[0] in resources.skus:
            sku = resources.skus[ids[0]]; quantity = action.get('quantity')
            add('current_price_and_quantity', type(quantity) is int and 1 <= quantity <= 10 and action.get('unit_price_fen') == sku['price_fen']
                and action.get('item_count') == quantity and action.get('cart_value_fen') == quantity * sku['price_fen'],
                {k: action.get(k) for k in ['quantity', 'unit_price_fen', 'item_count', 'cart_value_fen']}, 'current unit price × quantity')
            stock = resources.stock[ids[0]]
            add('sellable_at_commit', stock['sellable'] and stock['available_qty'] >= quantity and sku['sale_status'] == 'sellable'
                and resources.products[sku['product_id']]['active'], stock, 'sellable and enough stock')
        preserved = ['profile', 'context', 'constraints', 'rejected_sku_ids', 'rejected_attribute_tokens',
                     'current_look_version_id', 'look_version_ids']
        add('outfit_business_state_preserved_by_item_cart',
            all(before['state'].get(key) == after['state'].get(key) for key in preserved),
            [key for key in preserved if before['state'].get(key) != after['state'].get(key)], 'no changed outfit state fields')
    add('inventory_not_reserved_or_deducted', record['stock_before_turn'] == record['stock_after_turn'], None, 'no inventory mutation by simulated cart')
    add('returned_cart_matches_get', result.get('cart') == after['cart'], None, 'same cart as Service.view')
    return checks

def run_case(case):
    model = RecordingModel()
    service = Service(preview_resources(), model, remember_messages=True, limits=RequestLimits())
    created = service.create(); sid = created['state']['session_id']; records = []
    try:
        setup = fixture_setup(service, model, sid, case['setup'])
        previous_body = previous_result = None
        for index, turn in enumerate(case['turns'], 1):
            before = service.view(sid)
            mutations = mutate_resources(service, turn.get('mutations', []), before)
            stock_before = deepcopy(service.r.stock)
            body = deepcopy(previous_body) if turn.get('replay_previous') else request_body(before, turn.get('utterance'), turn.get('action'), turn.get('view_history_index'))
            started = time.monotonic(); calls_before = service.limits.daily_count
            record = dict(turn_no=index, utterance=turn.get('utterance'), action=turn.get('action'), replay_previous=turn.get('replay_previous', False),
                          expected=turn['expect'], before=before, mutations=mutations, stock_before_turn=stock_before)
            try:
                result = service.turn(sid, body)
                record.update(result=result, parsed_intent=deepcopy(model.last_intent) if not turn.get('action') else turn['action'],
                              replay_equal=result == previous_result if turn.get('replay_previous') else None)
                previous_body, previous_result = body, deepcopy(result)
            except Fault as error:
                record.update(error={'code': error.code, 'message': str(error)}, parsed_intent=deepcopy(model.last_intent))
            record.update(after=service.view(sid), dialog_after=deepcopy(service.sessions[sid].get('dialog_context')),
                          stock_after_turn=deepcopy(service.r.stock), actual_model_calls=service.limits.daily_count - calls_before,
                          model_responses=deepcopy(model.last_responses) if service.limits.daily_count > calls_before else [],
                          duration_ms=round((time.monotonic() - started) * 1000))
            record['checks'] = check_turn(turn['expect'], record, service.r)
            records.append(record)
            if record.get('error'):
                break  # Preserve the failed turn; dependent turns cannot be fairly scored.
        checks = [check for record in records for check in record['checks']]
        return dict(case_id=case['case_id'], tags=case['tags'], fixture_setup=setup, records=records,
                    planned_turns=len(case['turns']), unexecuted_turns_after_failure=len(case['turns']) - len(records),
                    passed=all(check['passed'] for check in checks), assertion_count=len(checks),
                    failed_assertions=[dict(turn_no=record['turn_no'], **check) for record in records for check in record['checks'] if not check['passed']])
    finally:
        service.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=DATASET)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--tag', help='Only cases containing this tag, e.g. main.')
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--workers', type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Refusing to overwrite an earlier run; choose a new --out path.')
    dataset = json.loads(args.dataset.read_text())
    cases = [case for case in dataset['cases'] if (not args.tag or args.tag in case['tags']) and (not args.case or case['case_id'] in args.case)]
    if not cases:
        parser.error('No cases selected.')
    started = time.monotonic(); hashes = source_hashes()
    pack_hashes = {str(path.relative_to(ROOT)): digest(path) for folder in ['data/m1_v2', 'data/m3_draft'] for path in sorted((ROOT / folder).rglob('*')) if path.is_file()}
    report = dict(report_type='independent live DeepSeek item-cart and follow-up semantics', dataset_version=dataset['dataset_version'],
                  dataset_sha256=digest(args.dataset), evaluator_sha256=digest(Path(__file__)), prompt_version=PROMPT_VERSION, prompt_sha256=hashlib.sha256(PROMPT.encode()).hexdigest(),
                  guard_version=GUARD_VERSION, guard_sha256=hashlib.sha256(GUARD_PROMPT.encode()).hexdigest(), source_sha256_at_start=hashes,
                  source_pack_sha256_at_start=pack_hashes, isolation='fresh in-memory Service per case; remember_messages=true; no SQLite/online HTTP/shared inventory',
                  started_at=datetime.now().astimezone().isoformat(), results=[])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    def save():
        report['results'].sort(key=lambda result: next(index for index, case in enumerate(cases) if case['case_id'] == result['case_id']))
        report['summary'] = dict(cases_completed=len(report['results']), cases_planned=len(cases), cases_passed=sum(r['passed'] for r in report['results']),
                                 evaluated_turns=sum(len(r.get('records', [])) for r in report['results']),
                                 planned_turns=sum(len(case['turns']) for case in cases),
                                 live_model_turns=sum(record['actual_model_calls'] > 0 for r in report['results'] for record in r.get('records', [])),
                                 assertions=sum(r.get('assertion_count', 0) for r in report['results']),
                                 assertions_failed=sum(len(r.get('failed_assertions', [])) for r in report['results']),
                                 actual_model_calls=sum(record['actual_model_calls'] for r in report['results'] for record in r.get('records', [])))
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, case): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = dict(case_id=case['case_id'], tags=case['tags'], passed=False, records=[], assertion_count=1,
                              failed_assertions=[{'check': 'evaluation_completed', 'passed': False, 'actual': type(error).__name__ + ': ' + str(error)}])
            report['results'].append(result); save()
            print(json.dumps(dict(case_id=result['case_id'], passed=result['passed'], failures=result['failed_assertions']), ensure_ascii=False), flush=True)
    report.update(completed_at=datetime.now().astimezone().isoformat(), elapsed_seconds=round(time.monotonic() - started, 2),
                  source_sha256_at_end=source_hashes(), source_code_unchanged_during_run=source_hashes() == hashes,
                  source_pack_unchanged=all((ROOT / name).is_file() and digest(ROOT / name) == expected for name, expected in pack_hashes.items()))
    report['passed'] = (len(report['results']) == len(cases) and all(r['passed'] for r in report['results'])
                        and report['source_pack_unchanged'] and report['source_code_unchanged_during_run'])
    save(); print(json.dumps({'passed': report['passed'], **report['summary']}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
