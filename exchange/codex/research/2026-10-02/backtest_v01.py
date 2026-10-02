"""Isolated Codex-500 exploratory simulation. No broker access or production imports."""
import json,math,statistics,hashlib,unittest
from pathlib import Path
ROOT=Path(__file__).parent

def features(data):
 days=sorted(b['t'][:10] for b in data['SPY']);series={s:{b['t'][:10]:b for b in rows} for s,rows in data.items()};feat={}
 for i,day in enumerate(days):
  if i<126:continue
  out={}
  for s,bars in series.items():
   window=[bars.get(x) for x in days[i-126:i+1]]
   if any(x is None for x in window):continue
   closes=[x['c'] for x in window];trs=[max(window[j]['h']-window[j]['l'],abs(window[j]['h']-window[j-1]['c']),abs(window[j]['l']-window[j-1]['c'])) for j in range(1,len(window))]
   rets=[closes[j]/closes[j-1]-1 for j in range(len(closes)-20,len(closes))];vol=statistics.stdev(rets)*math.sqrt(252)
   out[s]={'c':closes[-1],'atr':statistics.mean(trs[-20:]),'sma20':statistics.mean(closes[-20:]),'sma60':statistics.mean(closes[-60:]),'sma120':statistics.mean(closes[-120:]),'high20':max(x['h'] for x in window[-21:-1]),'vol':max(vol,.05),**{'mom'+str(n):closes[-1]/closes[-1-n]-1 for n in [60,63,126]}}
  feat[day]=out
 return days,series,feat

def run(days,series,feat,name,momentum=63,slots=1,cost=.001,start=None,end=None,restart=False):
 chosen=[i for i,d in enumerate(days) if i and days[i-1] in feat and (start is None or d>=start) and (end is None or d<=end)]
 cash=500.;vault=0.;injected=500.;pos={};peak=500.;pending=False;halt=False;restarts=0;cycles=0;curve=[];trades=[];missing=0;hits={};first_day=days[chosen[0]] if chosen else None
 def sell(s,raw,day,reason):
  nonlocal cash
  p=pos.pop(s);value=p['qty']*raw*(1-cost);cash+=value;trades.append({'day':day,'side':'sell','symbol':s,'qty':p['qty'],'raw_price':raw,'net_amount':value,'pnl':value-p['spent'],'reason':reason})
 for i in chosen:
  day=days[i];prior=days[i-1];f=feat[prior];market=f.get('SPY',{});risk_on=market.get('c',0)>market.get('sma120',float('inf'))
  # Risk breach is observed at close, liquidation happens at next executable open.
  if pending:
   for s in list(pos):
    if day in series[s]:sell(s,series[s][day]['o'],day,'drawdown_exit')
    else:missing+=1
   if pos:
    curve.append({'day':day,'equity':cash+sum(p['qty']*p['last'] for p in pos.values()),'vault':vault,'injected':injected});continue
   pending=False;cycles+=1
   if restart:
    vault+=cash;cash=500.;injected+=500.;peak=500.;restarts+=1
   else:halt=True
  ranks=[]
  if name=='spy':ranks=['SPY']
  elif risk_on:
   for s,x in f.items():
    if s=='SPY' or x['c']<10 or x['c']<=x['sma60']:continue
    if name=='baseline':ok=x['mom60']>0;score=x['mom60']/x['vol']
    else:ok=x['mom'+str(momentum)]>0 and x['c']>x['high20'];score=x['mom'+str(momentum)]/x['vol']
    if ok:ranks.append((score,s))
   ranks=[s for _,s in sorted(ranks,reverse=True)[:slots]]
  exited=set()
  for s in list(pos):
   b=series[s].get(day);x=f.get(s)
   if not b or not x:missing+=1;continue
   p=pos[s]
   # Stop set using only previous completed bars; gap executes at worse open.
   if name!='spy' and b['o']<=p['stop']:
    sell(s,b['o'],day,'gap_stop');exited.add(s);continue
   should=(name=='baseline' and s not in ranks) or (name=='breakout' and (not risk_on or x['c']<x['sma20']))
   if should:sell(s,b['o'],day,'prior_close_exit');exited.add(s)
  if not halt:
   equity_open=cash+sum(p['qty']*(series[s].get(day,{}).get('o',p['last'])) for s,p in pos.items())
   for s in ranks:
    if len(pos)>=slots:break
    if s in pos or s in exited or day not in series[s]:continue
    b=series[s][day];x=f[s];px=b['o'];stop=x['c']-2*x['atr'] if name=='breakout' else px*.95
    if name=='breakout' and px<=stop:continue
    allowance=min(cash,equity_open*.95/slots)
    qty=allowance/(px*(1+cost)) # Fractional research accounting, NOT an executable order claim.
    if name=='breakout':
     risk_per=max(px*(1+cost)-stop*(1-cost),.01)
     existing=sum(max(0,(series[k].get(day,{}).get('o',p['last'])-p['stop']))*p['qty'] for k,p in pos.items())
     qty=min(qty,equity_open*.05/risk_per,max(0,equity_open*.08-existing)/risk_per)
    if qty<=1e-9:continue
    spent=qty*px*(1+cost);cash-=spent;pos[s]={'qty':qty,'spent':spent,'stop':stop,'highclose':x['c'],'last':px};trades.append({'day':day,'side':'buy','symbol':s,'qty':qty,'raw_price':px,'net_amount':spent,'reason':'prior_close_signal'})
  for s in list(pos):
   b=series[s].get(day)
   if not b:missing+=1;continue
   p=pos[s]
   if name!='spy' and b['l']<=p['stop']:
    sell(s,min(b['o'],p['stop']),day,'intraday_stop');continue
   p['last']=b['c'];p['highclose']=max(p['highclose'],b['c'])
   # New trailing value becomes active only next day.
   if name=='breakout':p['stop']=max(p['stop'],p['highclose']-3*feat.get(day,{}).get(s,f[s])['atr'])
  equity=cash+sum(p['qty']*p['last'] for p in pos.values());peak=max(peak,equity)
  for target in [1000,2000,10000]:
   if equity>=target and target not in hits:hits[target]=day
  if not halt and equity<=peak*.60:pending=True
  assert cash>=-1e-7
  curve.append({'day':day,'equity':equity,'vault':vault,'injected':injected})
 if not curve:return None
 # Portfolio accounting includes residual cash from failed runs, and all new contributions.
 wealth=curve[-1]['equity']+vault;profit=wealth-injected
 dd=0.;pk=500.
 if not restart:
  for x in curve:pk=max(pk,x['equity']);dd=max(dd,1-x['equity']/pk)
 closed=[t for t in trades if t['side']=='sell'];contrib=[]
 for x in curve:
  if not contrib or x['injected']!=contrib[-1]['total_injected']:contrib.append({'day':x['day'],'total_injected':x['injected']})
 return {'name':name,'momentum':momentum if name=='breakout' else None,'slots':slots,'cost_bps_each_side':cost*10000,'start':first_day,'end':curve[-1]['day'],'sessions':len(curve),'initial':500,'final_active_equity':curve[-1]['equity'],'retired_cash':vault,'final_total_wealth':wealth,'total_injected':injected,'net_profit':profit,'return_on_total_injected':profit/injected,'max_close_drawdown':dd if not restart else None,'restart_count':restarts,'failed_cycles_liquidated':cycles,'halted':halt,'pending_risk_liquidation':pending,'target_dates':hits,'buy_count':sum(t['side']=='buy' for t in trades),'closed_trades':len(closed),'win_rate':sum(t['pnl']>0 for t in closed)/len(closed) if closed else None,'missing_held_bars':missing,'contributions':contrib,'trades':trades,'curve':curve}

class Checks(unittest.TestCase):
 def test_gap_and_restart_count_all_funding(self):
  days=['2024-01-01','2024-01-02','2024-01-03','2024-01-04']
  series={'SPY':{d:{'o':v,'h':v,'l':v,'c':v} for d,v in zip(days,[20,20,10,8])}}
  feat={d:{'SPY':{'c':20,'sma120':10}} for d in days}
  a=run(days,series,feat,'spy',slots=1,cost=0,restart=True)
  self.assertEqual(a['restart_count'],1)
  self.assertEqual(a['total_injected'],1000)
  self.assertAlmostEqual(a['retired_cash'],215)
  self.assertAlmostEqual(a['final_total_wealth'],715)
  self.assertAlmostEqual(a['net_profit'],-285)
 def test_stop_fills_at_gap_open_not_stop(self):
  days=['2024-01-01','2024-01-02','2024-01-03']
  series={'SPY':{d:{'o':20,'h':21,'l':19,'c':20} for d in days},'X':{d:{'o':v,'h':v,'l':v,'c':v} for d,v in zip(days,[20,20,10])}}
  feat={d:{'SPY':{'c':20,'sma120':10},'X':{'c':20,'sma60':10,'mom60':1,'vol':1}} for d in days}
  a=run(days,series,feat,'baseline',slots=1,cost=0)
  sells=[t for t in a['trades'] if t['side']=='sell']
  self.assertEqual(sells[0]['raw_price'],10)
  self.assertEqual(sells[0]['reason'],'gap_stop')
 def test_features_exclude_future(self):
  d=json.loads((ROOT/'daily-input-20261002.json').read_text());days,_,f=features(d);cut=days[400];reduced={s:[b for b in bars if b['t'][:10]<=cut] for s,bars in d.items()};_,_,g=features(reduced);self.assertEqual(f[cut],g[cut])
 def test_future_prices_do_not_change_past_trades(self):
  d=json.loads((ROOT/'daily-input-20261002.json').read_text());days,s,f=features(d);cut=days[400];a=run(days,s,f,'breakout',start=days[200],end=cut);b=run(days[:401],s,f,'breakout',start=days[200]);self.assertEqual(a,b)
 def test_cash_flow_identity(self):
  d=json.loads((ROOT/'daily-input-20261002.json').read_text());days,s,f=features(d)
  for restart in [False,True]:
   a=run(days,s,f,'breakout',restart=restart);self.assertAlmostEqual(a['final_total_wealth']-a['total_injected'],a['net_profit']);self.assertEqual(a['total_injected'],500*(1+a['restart_count']))

if __name__=='__main__':
 import sys
 if '--test' in sys.argv:unittest.main(argv=[sys.argv[0]],verbosity=2)
 else:
  d=json.loads((ROOT/'daily-input-20261002.json').read_text());days,s,f=features(d);configs=[('breakout',m,n) for m in [63,126] for n in [1,2]]+[('baseline',60,5),('spy',60,1)];results=[];rolling=[]
  for name,m,n in configs:
   for cost in [.001,.0025]:
    for label,st,en in [('full',None,'2026-10-01'),('2025','2025-01-01','2025-12-31'),('2026','2026-01-01','2026-10-01')]:
     for reset in [False,True]:
      x=run(days,s,f,name,m,n,cost,st,en,reset);x.update(period=label,restart_enabled=reset);results.append(x)
   eligible=[i for i in range(127,len(days)-125,63)]
   for i in eligible:
    x=run(days,s,f,name,m,n,.001,days[i],days[i+125],False);rolling.append({k:v for k,v in x.items() if k not in ['trades','curve','contributions']})
  out={'generated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'input_hash':hashlib.sha256((ROOT/'daily-input-20261002.json').read_bytes()).hexdigest(),'data_start':days[0],'data_end':days[-1],'symbols':len(d)-1,'source':'archived Alpaca IEX split-adjusted daily bars; current fixed 50 names','results':results,'rolling_126_sessions':rolling}
  (ROOT/'backtest-20261002-results.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
  for x in results:
   if x['period']=='full' and x['cost_bps_each_side']==10:print(json.dumps({k:v for k,v in x.items() if k not in ['trades','curve','contributions']},ensure_ascii=False))
