"""Read-only journal statistics. Stdlib; prints JSON; never opens a writable DB.
Run: python3 -B research/stats.py > research/stats-output.txt
Bootstrap is descriptive IID-trade bootstrap; correlated trades require day blocks.
"""
import collections, datetime, json, math, pathlib, random, sqlite3, statistics
ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED, B = 20261119, 10000

def mean(x): return statistics.mean(x) if x else None

def wilson(w,n):
    if not n: return None
    z=1.959963984540054; p=w/n; den=1+z*z/n
    center=(p+z*z/(2*n))/den; half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-half,center+half]

def inference(x):
    if len(x)<2: return {'n':len(x),'mean':mean(x),'ci95':None,'p_above_zero':None,'n80':None}
    rng=random.Random(SEED); n=len(x); mu=mean(x)
    boots=sorted(mean(rng.choices(x,k=n)) for _ in range(B))
    centered=[v-mu for v in x]
    p=(1+sum(mean(rng.choices(centered,k=n))>=mu for _ in range(B)))/(B+1)
    sd=statistics.stdev(x)
    return {'n':n,'mean':mu,'ci95':[boots[249],boots[9749]],'p_above_zero':p,
            'n80':math.ceil(((1.644854+.841621)*sd/mu)**2) if mu>0 and sd>0 else None}

def metrics(ts):
    ps=[t['pnl'] for t in ts if t['pnl'] is not None]; rs=[t['r_multiple'] for t in ts if t['r_multiple'] is not None]
    win=[p for p in ps if p>0]; loss=[p for p in ps if p<0]
    peak=eq=dd=run=longest=0
    for p in ps:
        eq+=p;peak=max(peak,eq);dd=max(dd,peak-eq);run=run+1 if p<0 else 0;longest=max(longest,run)
    return {'closed':len(ts),'too_few':len(ts)<20,'win_rate':len(win)/len(ps) if ps else None,
      'wilson95':wilson(len(win),len(ps)),'avg_win_dollars':mean(win),'avg_loss_dollars':mean(loss),
      'expectancy_dollars':inference(ps),'expectancy_R':inference(rs),
      'profit_factor':sum(win)/-sum(loss) if loss else None,'max_realized_drawdown_dollars':dd if ps else None,'longest_losing_run':longest if ps else None}

def read_account(f):
    db=sqlite3.connect(f.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
    def rows(t): return [dict(r) for r in db.execute('select * from '+t)] if t in tables else []
    trades,signals,outcomes=rows('trades'),rows('signals'),rows('outcomes');kv={r['key']:json.loads(r['value']) for r in rows('kv')};db.close()
    signals={s['id']:dict(s,payload=json.loads(s['payload'])) for s in signals}
    ts=sorted([t for t in trades if t['status']=='closed'],key=lambda t:(t['exit_at'] or '',t['id']))
    dates=sorted({r['date'] for r in trades+list(signals.values())+outcomes})
    splits=collections.defaultdict(list);net=[];unknown_cost=0
    for t in ts:
        s=signals.get(t['signal_id'],{});p=s.get('payload',{});edge=(p.get('odds') or {}).get('edge_points')
        labels={'side':t.get('side','long'),'actor':'bot' if t.get('note')=='auto' else 'manual',
          'signal_quality':'weak' if any('edge' in x.lower() for x in p.get('problems',[])) else 'unknown',
          'edge_band':'unknown' if edge is None else '<0' if edge<0 else '0-10' if edge<10 else '10+',
          'hour':(t.get('entry_at') or '')[11:13],'exit_reason':t.get('exit_reason'),'ticker_source':'unknown'}
        cmp=kv.get('watchlist_comparison:'+t['date'],{})
        members=[g for g in ('formula','claude') if t['symbol'] in {q['symbol'] for q in cmp.get(g) or []}]
        if members: labels['ticker_source']='+'.join(members)
        for k,v in labels.items(): splits[k+':'+str(v)].append(t)
        cost=(p.get('after_cost') or {}).get('per_share');risk=abs(t['entry_price']-t['stop'])
        if cost is not None and risk and t['r_multiple'] is not None: net.append(t['r_multiple']-cost/risk)
        else: unknown_cost+=1
    split={k:dict(metrics(v),under10=len(v)<10) for k,v in splits.items()}
    tests=sorted((v['expectancy_R']['p_above_zero'],k) for k,v in split.items() if v['expectancy_R']['p_above_zero'] is not None)
    prev=0
    for i,(p,k) in enumerate(tests):
        prev=max(prev,min(1,p*(len(tests)-i)));split[k]['holm_p']=prev
    calibration=[]
    for o in outcomes:
        p=signals.get(o['signal_id'],{}).get('payload',{}).get('odds') or {}
        if o['result'] in ('target','stop','flat') and p.get('p_target') is not None:
            calibration.append({'p_target':p['p_target'],'target':int(o['result']=='target'),'p_stop':p.get('p_stop'),'stop':int(o['result']=='stop')})
    taken={t['signal_id'] for t in trades};counter=[o for o in outcomes if o['signal_id'] not in taken and o['r'] is not None]
    comparisons={k:v for k,v in kv.items() if k.startswith(('watchlist_comparison:','watchlist_results:'))}
    return {'file':str(f.relative_to(ROOT)),'mode':'live' if f.name=='journal-live.db' else 'paper','dates':dates,
      'signal_statuses':dict(collections.Counter(s['status'] for s in signals.values())),'signals':len(signals),'open_trades':sum(t['status']=='open' for t in trades),
      'metrics':metrics(ts),'net_R_after_logged_cost':inference(net),'missing_cost_trades':unknown_cost,'splits':split,
      'outcomes':len(outcomes),'calibration_n':len(calibration),'brier_target':mean([(r['p_target']-r['target'])**2 for r in calibration]),
      'brier_stop':mean([(r['p_stop']-r['stop'])**2 for r in calibration if r['p_stop'] is not None]),
      'counterfactual_n':len(counter),'counterfactual_R':inference([r['r'] for r in counter]),'watchlist_records':comparisons,
      'signal_odds':[{'symbol':s['symbol'],'date':s['date'],'status':s['status'],'odds':s['payload'].get('odds')} for s in signals.values()]}

if __name__=='__main__':
    files=sorted((ROOT/'data').glob('journal*.db'))+sorted((ROOT/'data/accounts').glob('*/journal*.db'))
    print(json.dumps({'as_of_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seed':SEED,'bootstrap_resamples':B,
      'method':'Mode=ro snapshots; closed trades sorted by exit time. Null means not estimable. One-sided centered bootstrap; exploratory Holm over split-vs-zero tests. Power normal approximation, not a guarantee.',
      'accounts':[read_account(f) for f in files]},indent=2,allow_nan=False))
