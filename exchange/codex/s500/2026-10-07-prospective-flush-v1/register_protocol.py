"""Freeze validated methods before any eligible future session; no acquisition."""
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import hashlib,json
from protocol_tools import digest,initial_ledger,write_once
ROOT=Path(__file__).parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    path=ROOT/'protocol.json';p=json.loads(path.read_text());assert p['status']=='draft_before_validation_and_registration'
    test=json.loads((ROOT/'pre-registration-validation.json').read_text());assert test['passed'] and test['failed_tests']==0
    audit=json.loads((ROOT/'independent-pre-registration-audit.json').read_text());assert audit['passed'] is True
    design=json.loads((ROOT/'independent-design-draft-review.json').read_text());assert design['structural_checks_passed'] is True
    assert design['protocol_sha256']==sha(path)
    for n,h in design['binding'].items():assert sha(ROOT/n)==h,n
    for n,h in audit['bound_sha256'].items():assert sha(ROOT/n)==h,n
    for n,h in test['bound_sha256'].items():assert sha(ROOT/n)==h,n
    now=datetime.now(timezone.utc);assert now.astimezone(ZoneInfo('America/New_York')).date().isoformat()<p['window'][0]
    write_once(ROOT/'protocol-draft-before-registration.json',p)
    files=['capture_prior20.py','protocol_tools.py','prospect_selection.py','fixed-panel.json','fixed-panel-source.json','calendar-sessions.json','calendar-source.json']+[f.relative_to(ROOT).as_posix() for f in sorted((ROOT/'vendor').glob('*.py'))]
    p.update(status='registered',registered_at=now.isoformat(),registered_at_et=now.astimezone(ZoneInfo('America/New_York')).isoformat(),bound_file_sha256={n:sha(ROOT/n) for n in files})
    path.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    write_once(ROOT/'protocol-registration.json',{'study':p['id'],'registered_at':p['registered_at'],'registered_at_et':p['registered_at_et'],'protocol_sha256':sha(path),'protocol_content_digest':digest(p),'draft_sha256':sha(ROOT/'protocol-draft-before-registration.json'),'tests_passed':test['tests_passed'],'validation_sha256':sha(ROOT/'pre-registration-validation.json'),'independent_pre_registration_audit_sha256':sha(ROOT/'independent-pre-registration-audit.json'),'independent_design_review_sha256':sha(ROOT/'independent-design-draft-review.json'),'eligible_dates':59,'already_seen_registration_day_excluded':True,'prospective_observations_collected':0,'registered_not_scheduled':True,'frozen_before_first_future_date':True})
    write_once(ROOT/'observation-ledger.json',initial_ledger(p,p['registered_at']))
    print(json.dumps({'registered_at_et':p['registered_at_et'],'protocol_sha256':sha(path),'planned_dates':59,'observed_future_dates':0,'new_scheduler':False}))
if __name__=='__main__':main()
