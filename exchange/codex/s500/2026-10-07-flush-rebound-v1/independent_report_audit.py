"""Bind final research prose to independently audited figures and scope limits."""
import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
read=lambda name:json.loads((ROOT/name).read_text())
sha=lambda name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
def percent(x):return f'{x*100:+.4f}%'
def main():
    report=(ROOT/'REPORT.zh-CN.md').read_text();summary=read('statistics/summary.json');primary=read('statistics/primary-inference.json')
    variants=[r for r in summary['variants'] if r['group']=='stock'];assert len(variants)==36
    table=[line for line in report.splitlines() if re.match(r'\| [23]% \|',line)]
    assert len(table)==36
    for line,result in zip(table,variants):
        fields=[x.strip() for x in line.strip('|').split('|')];v=result['variant'];a=result['all'];b=result['matched_excess_date_cluster_bootstrap']
        expected=[str(round(float(v['drop_threshold'])*100))+'%',v['family'],v['exit_horizon'],str(v['cost_bps_per_side']),str(a['counts']['complete_returns']),percent(a['event_weighted']['returns']['mean']),percent(a['date_weighted']['returns']['mean']),percent(b['point_estimate']),'['+percent(b['lower'])+', '+percent(b['upper'])+']']
        assert fields==expected,(fields,expected)
    slices={**primary['months'],**primary['periods']};count=0
    for line in report.splitlines():
        if not re.match(r'\| (2026-\d\d|H1|H2_to_cutoff) \|',line):continue
        fields=[x.strip() for x in line.strip('|').split('|')];name=fields[0];r=slices[name]
        expected=[name,str(r['counts']['complete_returns']),str(r['counts']['complete_matched_excess']),percent(r['event_weighted']['returns']['mean']),percent(r['date_weighted']['returns']['mean']),percent(r['date_weighted']['matched_excesses']['mean'])]
        assert fields==expected,(fields,expected);count+=1
    assert count==12
    decision=read('stage-decision.json');peer=read('peer-exchange.json')
    assert decision['primary']==primary
    assert decision['decision']=='do_not_promote_this_rule_to_paper_execution'
    assert decision['orders_sent']==0 and decision['new_scheduler'] is False and decision['purchases'] is False
    assert decision['next_research']['status']=='proposed_not_registered_or_run'
    for name,digest in decision['bound_evidence'].items():assert sha(name)==digest
    for item in decision['cost_sensitivity']:
        selected=[r for r in variants if r['variant']['cost_bps_per_side']==item['cost_bps_per_side']]
        assert len(selected)==item['structural_variants']==12
        assert item['positive_event_mean_count']==sum(r['all']['event_weighted']['returns']['mean']>0 for r in selected)
        assert item['positive_date_mean_count']==sum(r['all']['date_weighted']['returns']['mean']>0 for r in selected)
        assert item['matched_excess_intervals_excluding_zero']==sum(not(r['matched_excess_date_cluster_bootstrap']['lower']<=0<=r['matched_excess_date_cluster_bootstrap']['upper']) for r in selected)
    assert peer['primary_net_event_mean']==primary['all']['event_weighted']['returns']['mean']
    assert peer['primary_date_matched_excess']==primary['matched_excess_date_cluster_bootstrap']
    assert peer['all_variants_retained']==36 and peer['no_actual_fill_claim'] is True
    required=['不是尚未结束的全年','不是独立交易数','不能直接相减','没有多重比较校正','不是因果效应','尚未登记或运行','没有模拟账户资金','缺口仍在','未新增定时器']
    assert all(t in report for t in required)
    assert '10:02:09 EDT' in report
    reproduction=(ROOT/'REPRODUCE.md').read_text()
    for t in ['does **not** contain all licensed raw market inputs','standard library','Creation timestamps differ','No row is an actual FILL','before outcome calculations']:
        assert t in reproduction,t
    assert read('independent-concentration-audit.json')['passed']
    result={'audit_version':'independent_final_report_review_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'numeric_checks':{'all36parameter_table_rows':36,'monthly_and_halfyear_rows':12,'primary_decision_matches_full_audited_primary':True,'all_cost_sensitivity_counts_recomputed':True,'peer_message_numerics_match':True},
        'scope_review':['Broad daily sample and fullcutoff dates distinguished from fullmarket orcompletedyear','Negative absolute modelednet and zero-crossing pairedexcessCI both emphasized; denominators andweights distinct','Unknown andno-event states retained without zeroimputation','Currentdirectory, missingdata, costs, fillability, observationalmatching, retrospectivehypothesis andmultiplicity limitations explicit','Concentration clearly postresult descriptive; fixed8storage symbols retained andPclassification unknown','Nextcandidate explicitly not registered/run; no impliedactivepapertrading orAIautowake','Public reproduction limits and privateinputrequirements explicit','User-facing freeze time shown in New York EDT'],
        'bound_documents':{name:sha(name) for name in ['REPORT.zh-CN.md','stage-decision.json','peer-exchange.json','REPRODUCE.md','primary-concentration.json','independent-concentration-audit.json']},
        'auditor_code_sha256':sha('independent_report_audit.py'),
        'publication_status':'Documents approved as localdescriptive research; remotecommit/publicationreadbacknotclaimed.'}
    (ROOT/'independent-report-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
