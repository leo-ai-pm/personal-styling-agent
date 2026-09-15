"""Extended sample catalog; never mutates source data or claims photographic approval."""
from datetime import timedelta
import json
from .core import ROOT, Resources, VERSION, demo_resources, now, instant, require, uid


def preview_resources(at=None, save=False):
    at=at or now()
    base=demo_resources(at,False)  # Revalidate the base sample pack.
    folder=ROOT/'data/m3_draft'
    read=lambda name:json.loads((folder/(name+'.json')).read_text())
    manifest=read('manifest');looks=read('look_assets')
    require(manifest['asset_mode']=='schematic_sample','LOOK_MAPPING_MISMATCH')
    require(len(looks)==manifest['expected_counts']['looks'],'LOOK_MAPPING_MISMATCH')
    jobs={j['look_id']:j for j in read('provenance/image_jobs')}
    for look in looks:
        if look['look_id'] in base.looks:
            original=base.looks[look['look_id']]
            require(all(look[k]==original[k] for k in ['items','image_url','image_sha256','active']),'LOOK_MAPPING_MISMATCH')
        else:
            job=jobs.get(look['look_id'],{})
            require(look['active'] is False and look['review_status']=='sample_fixture','PROVENANCE_MISSING_OR_UNCONFIRMED')
            require(job.get('image_sha256')==look['image_sha256'] and job.get('generator')=='svg_code','LOOK_MAPPING_MISMATCH')
            # Availability is scoped to this explicitly marked preview resource, not source approval.
            look['active']=True
    inventory=read('inventory')
    inventory.update(inventory_snapshot_id=uid('inv-candidate-sim'),captured_at=at,valid_until=(instant(at)+timedelta(hours=12)).isoformat())
    binding=dict(manifest['resource_binding'],inventory_snapshot_id=inventory['inventory_snapshot_id'])
    data=dict(contract_version=VERSION,tenant_id=manifest['tenant_id'],resource_binding=binding,
              catalog=read('catalog'),inventory_snapshot=inventory,look_assets=looks,
              attribute_vocabulary=read('attribute_vocabulary'),styling_rules=read('styling_rules'),
              asset_mode='schematic_sample',visual_acceptance='not_applicable_to_schematic')
    resources=Resources(data,display=True,preview=True);resources.fresh(at)
    return resources
