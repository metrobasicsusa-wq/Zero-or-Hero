from pathlib import Path
import json,hashlib
from datetime import datetime,timezone
R=Path(__file__).resolve().parent
sha=lambda n:hashlib.sha256((R/n).read_bytes()).hexdigest()
binding=json.loads((R/'independent-test-binding.json').read_text())
assert binding['failed_tests']==0 and binding['total_tests_passed']==62
for n,h in binding['sha256'].items():assert sha(n)==h,n
for n in ['study-design.json','source-manifest.json','selected-contracts.json']:
 d=json.loads((R/n).read_text())
 for field in ['input_sha256','output_sha256','source_sha256']:
  for f,h in d.get(field,{}).items():assert sha(f)==h,(n,f)
names=['study-design.json','sample-universe.json','current-underlying-references.json','session-coverage-plan.json','supplemental-underlying-references.json','supplemental-reference-plan.json','supplemental-reference-manifest.json','pre-price-correction.json','acquisition-plan.json','market-request-plan.json','source-manifest.json','contract-list-manifest.json','contract-list-coverage.json','selected-contracts.json','historical-option-events.json','current-indicative-quotes.json','option-market-manifest.json','collect_option_inputs.py','option_feasibility.py','run_feasibility.py','independent-test-binding.json','independent_option_tests.py','test_option_feasibility.py','options-source-rules.json','options-doc-manifest.json','bind_analysis.py']
out={'registered_at':datetime.now(timezone.utc).isoformat(),'tests_passed':62,'failed_tests':0,'actual_budget_math_started':False,'bound_sha256':{n:sha(n) for n in names},'scope':'Bound after acquisition and before any actual-sample budget arithmetic. Capability probes and collection saw prices but did not optimize selection or run this analysis. Pure tests are synthetic. Historical receipt timestamp deliberately unknown; calendar expiry comparison separately labeled.'}
p=R/'pre-analysis-validation.json';assert not p.exists();p.write_text(json.dumps(out,indent=2)+'\n');print('Bound',len(names),'files before arithmetic')
