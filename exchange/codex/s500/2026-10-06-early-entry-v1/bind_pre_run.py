"""Bind final implementation, tests and all actual data before first wealth run."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json

ROOT=Path(__file__).resolve().parent
def sha(n):return hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
def read(n):return json.loads((ROOT/n).read_text())

def main():
    import sys
    assert len(sys.argv)==2,'Pass independent source audit filename'
    independent_source=sys.argv[1]
    assert not (ROOT/'pre-run-validation.json').exists() and not (ROOT/'isolated-results.json').exists()
    binding=read('independent-final-test-binding.json')
    assert binding['failures']==0 and binding['test_count']==42
    for n,s in binding['input_sha256'].items():assert sha(n)==s,n
    own=read('engine-conflict-version-note.json')
    assert own['final_engine_sha256']==sha('early_engine.py') and own['self_tests']==38
    assert own['self_tests_log_sha256']==sha('early-model-tests.log')
    source=read('source-verification.json')
    for n,s in source['output_sha256'].items():assert sha(n)==s,n
    assert source['all_required_request_scopes_verified'] is True
    assert source['required_symbol_sessions']==2584 and source['conflicting_symbol_sessions']==0
    files=['study-design.json','market-gates.json','gate-pre-run-validation.json','gate-summary.json',
      'holding-input-design.json','holding-input-manifest.json','holding-market.json','daily-market-private.json','corporate-events.json',
      'verified-symbol-sessions.json','input-conflicts.json','source-verification.json','cached-source-manifest.json',
      'ep_engine.py','ep_event_engine.py','market_gates.py','early_engine.py','early_gates.py','run_early_paths.py',
      'test_early_models.py','early-model-tests.log','independent_gate_tests.py','independent_engine_tests.py',
      'independent-final-tests.log','independent-final-test-binding.json','independent-actual-gate-audit.json',
      'engine-conflict-version-note.json','diagnostic-plan.json','diagnose_mechanisms.py',independent_source]
    record={'validated_at':datetime.now(timezone.utc).isoformat(),'tests_passed':80,'implementation_tests':38,'independent_tests':42,
      'failed_tests':0,'design_sha256':sha('study-design.json'),'bound_sha256':{n:sha(n) for n in files},
      'wealth_runs_before_validation':0,'input_conflict_count':0,'source_missing_scope_count':0,
      'source_input_incomplete_interpretation':'Only failure at time of use; no early suppression for a future missing request.',
      'revision_quarantine_interpretation':'Conservative planned-window data-quality unknown only, not actual execution failure; no conflicts observed here.'}
    (ROOT/'pre-run-validation.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'bound_files':len(files),'tests_passed':80,'validated_at':record['validated_at']}))

if __name__=='__main__':main()
