"""Fetch public official option documentation, without any broker auth headers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json
import urllib.request,urllib.error

ROOT=Path(__file__).resolve().parent
SOURCES={
 'alpaca_llms':'https://docs.alpaca.markets/us/llms.txt',
 'historical_options':'https://docs.alpaca.markets/us/docs/historical-option-data.md',
 'options_trading':'https://docs.alpaca.markets/us/docs/options-trading.md',
 'option_contracts':'https://docs.alpaca.markets/us/reference/get-options-contracts.md',
 'option_single_contract':'https://docs.alpaca.markets/us/reference/get-option-contract-symbol_or_id.md',
 'option_bars':'https://docs.alpaca.markets/us/reference/optionbars.md',
 'option_trades':'https://docs.alpaca.markets/us/reference/optiontrades.md',
 'option_latest_quotes':'https://docs.alpaca.markets/us/reference/optionlatestquotes.md',
 'option_conditions':'https://docs.alpaca.markets/us/reference/optionmetaconditions.md',
 'option_dne':'https://docs.alpaca.markets/us/reference/optiondonotexercise.md',
 'regulatory_fees':'https://docs.alpaca.markets/us/docs/regulatory-fees.md',
 'real_time_options':'https://docs.alpaca.markets/us/docs/real-time-option-data.md',
 'opra_document_library':'https://www.opraplan.com/document-library',
 'alpaca_commissions':'https://alpaca.markets/support/commission-clearing-fees',
 'opra_output_spec':'https://cdn.opraplan.com/documents/OPRA_Pillar_Output_Specification.pdf',
 'alpaca_fee_schedule':'https://files.alpaca.markets/disclosures/library/BrokFeeSched.pdf',
 'option_commissions':'https://alpaca.markets/support/what-are-the-commission-fees-per-option-contract',
 'option_fees':'https://alpaca.markets/support/what-are-the-fees-associated-with-options-trading',
 'long_option_expiration':'https://alpaca.markets/support/what-happens-when-my-long-call-or-put-option-position-expires',
 'expiration_liquidation':'https://alpaca.markets/support/why-was-my-long-option-position-liquidated-or-short-option-position-closed-out-by-alpaca-on-expiration',
 'about_market_data':'https://docs.alpaca.markets/us/docs/about-market-data-api.md',
 'opra_electronic_agreement':'https://cdn.opraplan.com/documents/OPRA_Electronic_Subscriber_Agreement.pdf',
}

def fetch(item):
 key,url=item
 record={'source_id':key,'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),
  'authorization_headers_sent':False,'tls_verification':True,'request_method':'GET'}
 try:
  request=urllib.request.Request(url,headers={'User-Agent':'Public option-data documentation research (Python urllib)'})
  with urllib.request.urlopen(request,timeout=40)as response:
   body=response.read();record.update(http_status=response.status,final_url=response.url,content_type=response.headers.get('Content-Type'))
 except urllib.error.HTTPError as error:
  body=error.read();record.update(http_status=error.code,error_type='HTTPError')
 except Exception as error:
  body=b'';record.update(http_status=None,error_type=type(error).__name__)
 record.update(body_sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))
 (ROOT/'raw-docs'/(key+'.raw')).write_bytes(body)
 (ROOT/'raw-docs'/(key+'.meta.json')).write_text(json.dumps(record,indent=2)+'\n')
 return record

if __name__=='__main__':
 (ROOT/'raw-docs').mkdir(parents=True,exist_ok=True)
 with ThreadPoolExecutor(max_workers=3)as pool:records=list(pool.map(fetch,SOURCES.items()))
 (ROOT/'options-doc-manifest.json').write_text(json.dumps({'sources':records},indent=2)+'\n')
 print(json.dumps([{'source_id':r['source_id'],'status':r['http_status'],'bytes':r['bytes']}for r in records]))
