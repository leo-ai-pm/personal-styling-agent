"""Auditable semantic journeys and virtual business checks; no model-as-judge scoring."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .core import ROOT, SLOTS, Resources, Fault, demo_resources, execute, new_state, now, size, json_hash
from .evaluate import DATASET_SHA, events_valid, independent_recommendation_check, run_case
from .model import CompatibleModel, PROMPT, PROMPT_VERSION

CASES_PATH = ROOT / 'm2/tests/semantic_cases.json'
AT = '2026-09-05T12:00:00+00:00'


def load_cases(path=None):
    return json.loads((path or CASES_PATH).read_text())['cases']


class Journey:
    """Pure-core harness with bounded real conversation context, isolated per case."""
    def __init__(self, setup='blank', resources=None, profile=None):
        self.r = resources or demo_resources(AT, save=False)
        self.state = new_state(self.r.data['tenant_id'], False, AT)
        self.history = []
        self.pending = None
        self.dialog = None
        self.recent_messages = []
        self.outputs = []
        self.n = 0
        if setup != 'blank':
            clothing, shoe = ('S', '36') if setup in ['no_match_s', 'size_check'] else ('M', '38')
            budget = 50000 if setup == 'budget500' else 80000
            if profile:
                clothing, shoe, budget = profile['clothing'], profile['shoe'], profile['budget_max_fen']
            self.apply({'updates': {'budget_max_fen': budget, 'scene': 'scene:work_commute',
                        'usual_sizes': {s: size(s, v) for s, v in zip(SLOTS, [clothing, clothing, shoe])}}})
            if setup == 'no_match_s':
                self.apply({'confirmation_response': 'confirm'})
            if setup in ['current2', 'current3']:
                self.apply({'operation': 'modify_current', 'replace_slots': ['top', 'bottom'],
                            'carry_forward_unmentioned': True,
                            'keep_directives': [{'slot': 'shoes', 'scope': 'next_recommendation'}],
                            'preference_deltas': [{'attribute_token': 'style:casual', 'direction': 'increase'}]})
            if setup == 'current3':
                self.apply({'operation': 'modify_current', 'replace_slots': ['shoes'],
                            'carry_forward_unmentioned': True,
                            'preference_deltas': [{'attribute_token': 'color:light', 'direction': 'increase'}]})
        self.outputs = []

    def context(self):
        context = deepcopy(self.dialog or {})
        context['recent_messages'] = deepcopy(self.recent_messages[-16:])
        return context

    def apply(self, intent, text=None):
        self.n += 1
        before, history_before = deepcopy(self.state), deepcopy(self.history)
        out = execute(self.r, self.state, self.history, intent, 'sem-t-' + str(self.n),
                      'sem-r-' + str(self.n), AT, self.pending, self.context())
        self.pending = out.get('pending_intent')
        self.dialog = out.get('dialog_context')
        self.state, self.history = out['state'], out['history']
        out['state_before'] = before
        out['history_before'] = history_before
        out['intent'] = deepcopy(intent)
        if text is not None:
            self.recent_messages.extend([{'role': 'user', 'content': text},
                                         {'role': 'assistant', 'content': out['response']['text']}])
            self.outputs.append(out)
        return out


def current_version(state, history):
    return next((v for v in history if v['look_version_id'] == state['current_look_version_id']), None)


def expected_skus(reference, before, history, previous_outputs):
    if reference is None:
        return None
    parts = reference.split('.')
    if parts[0] == 'current':
        v = current_version(before, history)
        return list(v['items'].values()) if v and parts[1] == 'all' else ([v['items'][parts[1]]] if v else [])
    if parts[0] == 'sku':
        return [parts[1]]
    if parts[0] == 'history':
        v = next((v for v in history if v['version_no'] == int(parts[1])), None)
        return [v['items'][parts[2]]] if v else []
    if parts[0] == 'catalog':
        data = next((o['decision']['action']['catalog_result'] for o in reversed(previous_outputs)
                     if o.get('decision', {}).get('action', {}).get('catalog_result')), None)
        if data and len(data['products']) >= int(parts[1]):
            return [v['sku_id'] for v in data['products'][int(parts[1])-1]['variants']]
        return []
    raise ValueError('Unsupported reference: ' + reference)


def check_turn(expect, out, r, previous_outputs=None):
    """Checks source truth and state directly; expectations never come from model output."""
    previous_outputs = previous_outputs or []
    checks = []
    def check(name, passed, actual=None, expected=None):
        checks.append({'check': name, 'passed': bool(passed), 'actual': actual, 'expected': expected})
    decision, state = out['decision'], out['state']
    before, history_before = out['state_before'], out['history_before']
    text = out['response']['text']
    operation = decision.get('interpretation', {}).get('operation')
    check('events_contract', events_valid(out['events']))
    check('recommendation_source_truth', independent_recommendation_check([out], r))
    check('history_immutable', out['history'][:len(history_before)] == history_before)
    if 'operation' in expect:
        check('operation', operation in expect['operation'], operation, expect['operation'])
    if 'status' in expect:
        check('status', decision['status'] in expect['status'], decision['status'], expect['status'])
    if expect.get('readonly'):
        check('readonly_state', state == before)
        check('readonly_history', out['history'] == history_before)
        check('readonly_no_cart', not out.get('cart_added_sku_ids'))
    if expect.get('no_new_look'):
        check('no_new_look', state['current_look_version_id'] == before['current_look_version_id'] and out['history'] == history_before)
    if expect.get('no_cart'):
        check('no_cart', not out.get('cart_added_sku_ids') and not any(e['event_type'] == 'bundle_added_to_cart' for e in out['events']))
    if 'budget' in expect:
        check('budget', state['context'].get('budget_max_fen') == expect['budget'], state['context'].get('budget_max_fen'), expect['budget'])
    for slot, value in expect.get('sizes', {}).items():
        check('size_' + slot, state['profile']['usual_sizes'].get(slot) == size(slot, value), state['profile']['usual_sizes'].get(slot), size(slot, value))
    if expect.get('no_size_reconfirm'):
        check('no_size_reconfirm', not re.search('核对.{0,20}尺码|尺码.{0,12}(?:正确[？?]|是否正确)', text), text)
    old_current = current_version(before, history_before)
    new_current = current_version(state, out['history'])
    for slot in expect.get('keep_slots', []):
        check('kept_' + slot, bool(old_current and new_current and old_current['items'][slot] == new_current['items'][slot]))
    for slot in expect.get('changed_slots', []):
        check('changed_' + slot, bool(old_current and new_current and old_current['items'][slot] != new_current['items'][slot]))
    inquiry = decision.get('action', {}).get('inquiry_result', {})
    if inquiry:
        facts = inquiry.get('facts', [])
        facts_valid = True
        for fact in facts:
            sku = r.skus.get(fact.get('sku_id'))
            if not sku:
                facts_valid = False
                continue
            product, stock = r.products[sku['product_id']], r.stock[sku['sku_id']]
            sellable = bool(product['active'] and sku['sale_status'] == 'sellable' and stock['sellable'] and stock['available_qty'] > 0)
            facts_valid = facts_valid and fact.get('product_id') == sku['product_id'] and fact.get('price_fen') == sku['price_fen']
            facts_valid = facts_valid and fact.get('size') == sku['size'] and fact.get('available_qty') == stock['available_qty']
            facts_valid = facts_valid and fact.get('sellable') == sellable and fact.get('material') == product.get('material')
        check('inquiry_source_truth', facts_valid and {f.get('sku_id') for f in facts} == set(inquiry.get('resolved_sku_ids', [])))
    if 'topic' in expect:
        actual_topic = inquiry.get('topic')
        # A compound price question may be answered through purchase/details if the
        # independent item, actual-price, source-truth and read-only checks still pass.
        compatible_price = expect['topic'] == 'price' and actual_topic in ['purchase', 'details'] and expect.get('resolved') is not None
        check('inquiry_topic', actual_topic == expect['topic'] or compatible_price, actual_topic, expect['topic'])
    if expect.get('resolved') is not None:
        wanted = expected_skus(expect['resolved'], before, history_before, previous_outputs)
        got = inquiry.get('resolved_sku_ids', [])
        # A product-level inquiry may resolve all size variants of the displayed product.
        same_product = bool(wanted and got and set(got) <= set(wanted))
        if str(expect['resolved']).endswith('.all'):
            same_product = bool(wanted and set(got) == set(wanted))
        if str(expect['resolved']).startswith('catalog.') and wanted and got and all(s in r.skus for s in got):
            slot = r.skus[wanted[0]]['slot']
            if slot not in before['profile']['usual_sizes']:
                # A catalog row is a product, not a promise that every variant is in stock.
                same_product = {r.skus[s]['product_id'] for s in got} == {r.skus[s]['product_id'] for s in wanted}
        check('resolved_item', same_product, got, wanted)
        if expect.get('topic') == 'price' and wanted:
            prices = {sum(r.skus[s]['price_fen'] for s in wanted)} if expect.get('price_total') else {r.skus[s]['price_fen'] for s in wanted}
            check('answer_has_actual_price', any(re.search(r'(?<![\d.])' + re.escape(f'{p/100:g}') + r'(?![\d.])', text) for p in prices), text, sorted(prices))
    if expect.get('material_grounded'):
        check('missing_material_disclosed', bool(re.search('(?:未|没有|暂无|暂未|缺少).{0,15}(?:提供|登记|材质|成分|面料|资料|信息)|不能.{0,10}(?:确认|判断)', text)), text)
        check('no_invented_material_percentage', not re.search(r'\d+(?:\.\d+)?\s*[%％]', text), text)
    catalog = decision.get('action', {}).get('catalog_result', {})
    products = catalog.get('products', [])
    variants = [v for p in products for v in p.get('variants', [])]
    if catalog:
        valid = all(v.get('sku_id') in r.skus and v.get('price_fen') == r.skus[v['sku_id']]['price_fen']
                    and v.get('size') == r.skus[v['sku_id']]['size']
                    and r.stock[v['sku_id']]['available_qty'] > 0 and r.stock[v['sku_id']]['sellable']
                    and r.products[r.skus[v['sku_id']]['product_id']]['active'] for v in variants)
        check('catalog_source_truth', valid)
    if 'catalog_slots' in expect:
        check('catalog_slots', bool(products) and all(p['slot'] in expect['catalog_slots'] for p in products), [p['slot'] for p in products], expect['catalog_slots'])
    if 'catalog_product_count' in expect:
        check('catalog_count', catalog.get('total_product_count') == expect['catalog_product_count'], catalog.get('total_product_count'), expect['catalog_product_count'])
    if 'catalog_price_max' in expect:
        check('query_price_ceiling', bool(variants) and all(v['price_fen'] <= expect['catalog_price_max'] for v in variants))
    if 'catalog_size' in expect:
        check('query_size', bool(variants) and all(v['size']['value'] == expect['catalog_size'] for v in variants))
    if 'catalog_attribute' in expect:
        check('query_attribute', bool(variants) and all(r.hit({'items': {r.skus[v['sku_id']]['slot']: v['sku_id']}}, expect['catalog_attribute'], [r.skus[v['sku_id']]['slot']]) for v in variants))
    if 'catalog_target' in expect:
        check('catalog_target', catalog.get('target') == expect['catalog_target'], catalog.get('target'), expect['catalog_target'])
    if 'eligible_looks_count' in expect:
        ids = catalog.get('coverage', {}).get('eligible_look_ids')
        check('eligible_looks_count', isinstance(ids, list) and len(ids) == expect['eligible_looks_count'], ids, expect['eligible_looks_count'])
    if expect.get('no_pii_events'):
        check('no_pii_in_events', '13800138000' not in json.dumps(out['events']))
    if expect.get('no_fit_guarantee'):
        affirmative_text = re.sub(r'(?:不能|无法|不作|不做|不)[^。；\n]{0,15}(?:保证(?:你穿|合脚|合身)|一定(?:合脚|合身|适合你))', '', text)
        check('no_fit_guarantee', not re.search('保证(?:你穿|合脚|合身)|一定(?:合脚|合身|适合你)', affirmative_text), text)
    return checks


def run_semantic_case(case, model, resource_mode='accepted'):
    resources = None
    if resource_mode == 'candidate':
        from .preview import preview_resources
        resources = preview_resources(AT, save=False)
    journey = Journey(case['setup'], resources, case.get('profile'))
    results, all_checks = [], []
    for index, turn in enumerate(case['turns']):
        started = time.monotonic()
        before, history_before = deepcopy(journey.state), deepcopy(journey.history)
        try:
            if 'view_context' in turn:
                reference = turn['view_context']
                target = next((v['look_version_id'] for v in journey.history if v['version_no'] == int(reference.split('.')[1])), None) if reference else None
                journey.dialog = {**deepcopy(journey.dialog or {}), 'view_context': {'look_version_id': target}}
            intent, meta = model.parse(turn['text'], journey.state, journey.history, journey.r, journey.context())
            previous = list(journey.outputs)
            out = journey.apply(intent, turn['text'])
            checks = check_turn(turn['expect'], out, journey.r, previous)
            record = {'turn': index + 1, 'user_text': turn['text'], 'expected': turn['expect'],
                      'intent': intent, 'metrics': meta, 'output': out, 'checks': checks,
                      'status': 'passed' if all(c['passed'] for c in checks) else 'failed'}
            all_checks.extend(checks)
        except Fault as error:
            record = {'turn': index + 1, 'user_text': turn['text'], 'expected': turn['expect'],
                      'status': 'technical_error', 'error': {'code': error.code, 'message': error.message},
                      'state_before': before, 'history_before': history_before}
        except Exception as error:
            record = {'turn': index + 1, 'user_text': turn['text'], 'expected': turn['expect'],
                      'status': 'technical_error', 'error': {'code': type(error).__name__, 'message': str(error)},
                      'state_before': before, 'history_before': history_before}
        record['duration_ms'] = round((time.monotonic()-started)*1000)
        results.append(record)
    return {'case_id': case['case_id'], 'tags': case['tags'], 'setup': case['setup'], 'resource_mode': resource_mode,
            'status': 'passed' if all(t['status'] == 'passed' for t in results) else 'failed',
            'turns': results, 'checks_passed': sum(c['passed'] for c in all_checks),
            'checks_failed': sum(not c['passed'] for c in all_checks),
            'technical_errors': sum(t['status'] == 'technical_error' for t in results)}


def virtual_matrix():
    """144 isolated combinations; virtual S images are never saved or displayed."""
    base = demo_resources(AT, save=False).data
    results = []
    for clothing, shoe in [('S', '36'), ('M', '38')]:
        for budget in [49900, 50000, 73699, 73700, 79700, 80000]:
            for stock_mode in ['available', 'shoe_unavailable', 'all_unavailable', 'top_inactive']:
                for image_mode in ['accepted_only', 'virtual_extra_size', 'no_images']:
                    data = deepcopy(base)
                    if image_mode == 'virtual_extra_size':
                        virtual = []
                        for look in data['look_assets']:
                            l = deepcopy(look)
                            l['look_id'] = 'VIRTUAL-' + l['look_id']
                            l['image_url'] = '/virtual-not-for-display/' + l['look_id'] + '.png'
                            l['items'] = {s: sku.rsplit('-', 1)[0] + ('-36' if s == 'shoes' else '-S') for s, sku in l['items'].items()}
                            virtual.append(l)
                        data['look_assets'] += virtual
                    if image_mode == 'no_images':
                        data['look_assets'] = []
                    for item in data['inventory_snapshot']['items']:
                        if stock_mode == 'all_unavailable' or (stock_mode == 'shoe_unavailable' and '-S101-' in item['sku_id']):
                            item['available_qty'] = 0
                    if stock_mode == 'top_inactive':
                        next(p for p in data['catalog']['products'] if p['product_id'] == 'M1V2-P-T101')['active'] = False
                    r = Resources(data, display=False)
                    s = new_state(r.data['tenant_id'], False, AT)
                    intent = {'updates': {'budget_max_fen': budget, 'scene': 'scene:work_commute',
                              'usual_sizes': {slot: size(slot, v) for slot, v in zip(SLOTS, [clothing, clothing, shoe])}}}
                    # Independent brute-force availability oracle, no call to solve().
                    expected = []
                    for look in r.looks.values():
                        if not look['active']:
                            continue
                        ids = list(look['items'].values())
                        if sum(r.skus[x]['price_fen'] for x in ids) > budget:
                            continue
                        if any(r.skus[look['items'][slot]]['size'] != intent['updates']['usual_sizes'][slot] for slot in SLOTS):
                            continue
                        if any(not r.products[r.skus[x]['product_id']]['active'] or r.skus[x]['sale_status'] != 'sellable'
                               or not r.stock[x]['sellable'] or r.stock[x]['available_qty'] < 1 for x in ids):
                            continue
                        expected.append(look['look_id'])
                    out = execute(r, s, [], intent, 'virtual-t', 'virtual-r', AT)
                    actual = out['decision']['status']
                    valid = actual == ('recommendation' if expected else 'no_match') and independent_recommendation_check([out], r)
                    valid = valid and out['state']['context']['budget_max_fen'] == budget and out['state']['profile']['usual_sizes'] == intent['updates']['usual_sizes']
                    results.append({'case_id': f'VIRTUAL-{len(results)+1:03d}', 'clothing_size': clothing, 'shoe_size': shoe,
                                    'budget_max_fen': budget, 'stock': stock_mode, 'images': image_mode,
                                    'expected_eligible_look_ids': expected, 'actual_status': actual,
                                    'passed': bool(valid), 'is_simulated': True, 'is_displayable': False})
    return results


def run_v2_case(case, mode, model):
    result = run_case(case, mode, model)
    if case['case_id'] == 'P0-CONSTRAINT-007':
        event = next((e for o in result['outputs'] for e in o['events'] if e['event_type'] == 'no_match'), None)
        strict = bool(event and event['payload'].get('blocking_constraint_ids') == [])
        assertion = next(a for a in result['assertions'] if a['assertion_id'] == 'P0-CONSTRAINT-007-A08')
        assertion['strict_empty_list_check'] = strict
        if not strict:
            assertion['status'] = 'failed'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', choices=['semantic', 'virtual', 'frozen', 'v2'], default='semantic')
    parser.add_argument('--mode', choices=['live', 'structured'], default='live')
    parser.add_argument('--output', required=True, help='New report path; existing reports are never overwritten.')
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--cases-file', help='Separate semantic extension dataset; the main 71 cases stay frozen.')
    parser.add_argument('--resources', choices=['accepted', 'candidate'], default='accepted')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    output = ROOT / args.output
    if output.exists():
        parser.error('Refusing to overwrite an existing report; use a new round path.')
    if args.suite == 'semantic' and args.mode != 'live':
        parser.error('Semantic scores require live model input; use virtual for structured checks.')
    output.parent.mkdir(parents=True, exist_ok=True)
    model = CompatibleModel()
    report = {'created_at': now(), 'suite': args.suite, 'mode': 'structured' if args.suite == 'virtual' else args.mode,
              'prompt_version': PROMPT_VERSION, 'prompt_sha256': json_hash(PROMPT),
              'code_sha256': json_hash({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'m2').glob('*.py')}),
              'model': model.name if args.mode == 'live' and args.suite != 'virtual' else None,
              'notice': 'No automatic retry or model judge. All outcomes retained; structured setup is not NLU evidence.',
              'status': 'running', 'cases': []}
    if args.suite == 'semantic':
        case_path = ROOT / args.cases_file if args.cases_file else CASES_PATH
        cases = load_cases(case_path)
        report['dataset_sha256'] = hashlib.sha256(case_path.read_bytes()).hexdigest()
        report['dataset_path'] = str(case_path.relative_to(ROOT))
        report['resource_mode'] = args.resources
        runner = lambda c: run_semantic_case(c, model, args.resources)
    elif args.suite in ['frozen', 'v2']:
        original = (ROOT/'evals/p0_eval_cases.jsonl').read_bytes()
        assert hashlib.sha256(original).hexdigest() == DATASET_SHA, 'Original frozen dataset changed'
        path = ROOT / ('evals/p0_eval_cases_v2.jsonl' if args.suite == 'v2' else 'evals/p0_eval_cases.jsonl')
        cases = [json.loads(line) for line in path.read_text().splitlines()]
        report['dataset_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        runner = lambda c: run_v2_case(c, args.mode, model) if args.suite == 'v2' else run_case(c, args.mode, model)
    else:
        cases = virtual_matrix()
        runner = lambda c: c
    if args.case:
        cases = [c for c in cases if c['case_id'] in args.case]
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        parser.error('No matching cases')
    report['expected_case_count'] = len(cases)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    save()
    workers = max(1, min(args.workers, 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(runner, c): c for c in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                result = future.result()
            except Exception as error:
                result = {'case_id': case['case_id'], 'status': 'technical_error', 'error_type': type(error).__name__, 'technical_errors': 1}
            report['cases'].append(result)
            report['cases'].sort(key=lambda c: c['case_id'])
            save()
            print(json.dumps({'case_id': case['case_id'], 'completed': len(report['cases']), 'total': len(cases),
                              'status': result.get('status', 'passed' if result.get('passed') else None),
                              'failures': result.get('checks_failed'), 'technical_errors': result.get('technical_errors')}, ensure_ascii=False), flush=True)
    if args.suite == 'semantic':
        report['counts'] = dict(Counter(t['status'] for c in report['cases'] for t in c.get('turns', [])))
        report['cases_passed'] = sum(c.get('status') == 'passed' for c in report['cases'])
        report['cases_failed'] = len(cases)-report['cases_passed']
        report['technical_errors'] = sum(c.get('technical_errors', 0) for c in report['cases'])
    elif args.suite == 'virtual':
        report['counts'] = {'passed': sum(c['passed'] for c in cases), 'failed': sum(not c['passed'] for c in cases)}
    else:
        report['counts'] = dict(Counter(a['status'] for c in report['cases'] for a in c.get('assertions', [])))
        report['technical_errors'] = sum(len(c.get('errors', [])) + c.get('technical_errors', 0) for c in report['cases'])
    if args.mode == 'live' and args.suite != 'virtual':
        records = [t for c in report['cases'] for t in c.get('turns', c.get('outputs', []))]
        metrics = [t.get('metrics', {}) for t in records]
        durations = sorted(t.get('duration_ms', t.get('metrics', {}).get('total_duration_ms', 0)) for t in records)
        report['user_turns_evaluated'] = len(records)
        report['model_calls_attempted'] = sum(m.get('api_calls', 1) for m in metrics)
        report['usage'] = {key: sum(m[key] for m in metrics if type(m.get(key)) is int) for key in ['input_tokens', 'output_tokens']}
        report['usage']['responses_without_usage'] = sum(not all(type(m.get(k)) is int for k in ['input_tokens', 'output_tokens']) for m in metrics)
        report['usage']['cost'] = None
        report['usage']['cost_note'] = 'Provider did not supply billed cost; token counts are actual reported usage.'
        report['performance_ms'] = {'p50': durations[(len(durations)-1)//2], 'p95': durations[math.ceil(len(durations)*.95)-1], 'max': max(durations)} if durations else None
    report['status'] = 'completed'
    report['completed_at'] = now()
    save()
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False, indent=2))
    if report.get('cases_failed') or report['counts'].get('failed') or report.get('technical_errors'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
