"""Rebuild MIT-licensed SVG fixtures from synthetic catalog data; no external images."""
from pathlib import Path
import hashlib
import html
import json

ROOT = Path(__file__).resolve().parents[1]
COLORS = {'color:blue':'#9ebdda','color:ivory':'#ece6d4','color:ecru':'#e8e4d6','color:chocolate':'#655146','color:wine':'#803c48','color:burgundy':'#803c48','color:black':'#41423d','color:white':'#f5f3e9'}
LABELS = {'top':'上衣','bottom':'下装','shoes':'鞋履'}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def illustration(look, catalog):
    skus={s['sku_id']:s for s in catalog['skus']}
    products={p['product_id']:p for p in catalog['products']}
    rows=[]
    for index,(slot,sku_id) in enumerate(look['items'].items()):
        sku=skus[sku_id];product=products[sku['product_id']]
        color=COLORS.get(sku['color_token'],'#b5bca5')
        icon={
            'top':'<path d="M45 20 15 50 38 74 53 62 53 135 137 135 137 62 152 74 175 50 145 20 115 10 Q95 35 75 10Z"/>',
            'bottom':'<path d="M48 10 142 10 151 147 110 147 95 65 80 147 39 147Z"/>',
            'shoes':'<path d="M28 55 72 48 90 73 142 81 Q170 85 167 112 L26 112 Q14 105 28 55Z"/><path d="M28 120 167 120" fill="none"/>'
        }[slot]
        y=170+index*235
        name=html.escape(product['name'].removeprefix('模拟·'))
        rows.append(f'''<g transform="translate(40,{y})"><rect width="640" height="210" rx="18" fill="#fff"/>
<g transform="translate(8,25)" fill="{color}" stroke="#68715d" stroke-width="2">{icon}</g>
<text x="205" y="45" font-size="15" fill="#798368">{LABELS[slot]} · {html.escape(sku['size']['value'])} 码</text>
<text x="205" y="88" font-size="18">{name}</text>
<text x="205" y="127" font-size="13" fill="#798368">{html.escape(sku_id)}</text>
<text x="205" y="174" font-size="25">¥{sku['price_fen']/100:g}</text></g>''')
    total=sum(skus[s]['price_fen'] for s in look['items'].values())/100
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1080" viewBox="0 0 720 1080" role="img" aria-label="三件模拟商品的组合示意">
<rect width="720" height="1080" fill="#eeefe7"/>
<g font-family="sans-serif" fill="#35402e"><text x="40" y="65" font-size="16" letter-spacing="3">PERSONAL STYLING AGENT</text>
<text x="40" y="118" font-size="32">商品组合示意</text>{''.join(rows)}
<text x="40" y="930" font-size="18">三件合计</text><text x="40" y="980" font-size="38">¥{total:g}</text>
<text x="40" y="1030" font-size="15" fill="#798368">开源示意素材 · 非商品实拍、非试穿效果</text></g></svg>\n'''


def main():
    base={}
    for pack in ['m1_v2','m3_draft']:
        directory=ROOT/'data'/pack
        catalog=json.loads((directory/'catalog.json').read_text())
        looks=json.loads((directory/'look_assets.json').read_text())
        jobs=[]
        for look in looks:
            if look['look_id'] in base:
                saved=base[look['look_id']]
                for key in ['image_url','image_sha256','model_asset_version','model_identity_id','pose_id','background_id','view','disclaimer_code','origin']:
                    look[key]=saved[key]
                continue
            filename=Path(look['image_url']).stem+'.svg'
            path=directory/'assets'/filename;path.parent.mkdir(exist_ok=True)
            content=illustration(look,catalog).encode('utf-8');path.write_bytes(content)
            look.update(image_url='/'+path.relative_to(ROOT).as_posix(),image_sha256=hashlib.sha256(content).hexdigest(),
                        model_asset_version='schematic_sample_v1',model_identity_id='schematic_layout',pose_id='three_product_cards',
                        background_id='sample_sage',view='product_layout',disclaimer_code='schematic_reference_not_fit',
                        origin=dict(origin_type='self_created',source_name='开源商品组合示意',source_ref='scripts/generate_sample_assets.py',is_simulated=True,rights_confirmed=True))
            if pack=='m1_v2':base[look['look_id']]=look.copy()
            else:
                look['review_status']='sample_fixture'
                jobs.append(dict(look_id=look['look_id'],generator='svg_code',image_sha256=look['image_sha256'],output_path=path.relative_to(ROOT).as_posix(),license='MIT'))
        write_json(directory/'look_assets.json',looks)
        write_json(directory/'provenance/asset_manifest.json',dict(asset_version=looks[0]['look_asset_version'],generator='svg_code',license='MIT',human_visual_acceptance=False,
            look_image_sha256={l['look_id']:l['image_sha256'] for l in looks if l['active']}))
        if jobs:write_json(directory/'provenance/image_jobs.json',jobs)
    print('Generated 13 deterministic SVG sample assets. No model or network calls.')


if __name__=='__main__':main()
