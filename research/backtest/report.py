"""Portable, dependency-free tables and SVG charts; no network resources."""
import html
import json
import math
import statistics

from backtest import HOME, OUT, bootstrap, drawdowns, metrics, write_csv


def fmt(value):
    if value is None:
        return 'N/A'
    if isinstance(value, float):
        return '%.4f' % value
    return str(value)


def table(rows):
    if not rows:
        return ''
    keys = list(rows[0])
    head = '<thead><tr>' + ''.join('<th>'+html.escape(k)+'</th>' for k in keys) + '</tr></thead>'
    body = '<tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(fmt(row.get(k)))+'</td>' for k in keys)+'</tr>' for row in rows)+'</tbody>'
    return '<div class="scroll"><table>'+head+body+'</table></div>'


def markdown_table(rows):
    keys = list(rows[0])
    safe = lambda x: fmt(x).replace('|', '/')
    return '\n'.join(['| '+' | '.join(keys)+' |', '| '+' | '.join('---' for _ in keys)+' |'] +
                     ['| '+' | '.join(safe(row.get(k)) for k in keys)+' |' for row in rows])


def chart(title, series, labels, ylabel, bars=False):
    width, height = 920, 330
    left, right, top, bottom = 76, 20, 40, 62
    colors = ['#3087e5', '#e79824', '#a66deb']
    values = [v for _, values in series for v in values]
    lo, hi = min(values or [0]), max(values or [1])
    if bars:
        lo, hi = min(0,lo), max(0,hi)
    span = hi-lo or max(abs(hi)*.1, 1)
    lo, hi = lo-.08*span, hi+.08*span
    pw, ph = width-left-right, height-top-bottom
    x = lambda i: left+pw*i/max(1,len(labels)-1)
    y = lambda value: top+ph*(hi-value)/(hi-lo)
    parts = ['<section><h3>'+html.escape(title)+'</h3>',
             '<svg role="img" aria-label="'+html.escape(title)+'" viewBox="0 0 %d %d"><title>%s</title>' % (width,height,html.escape(title))]
    for i in range(5):
        value = lo+(hi-lo)*i/4
        parts.append('<line class="grid" x1="%g" x2="%g" y1="%g" y2="%g"/><text x="%g" y="%g" text-anchor="end">%.2f</text>' % (left,width-right,y(value),y(value),left-8,y(value)+4,value))
    ticks = sorted(set(round(i*(len(labels)-1)/4) for i in range(5))) if labels else []
    for i in ticks:
        parts.append('<text x="%g" y="%g" text-anchor="middle">%s</text>' % (x(i),height-35,html.escape(labels[i])))
    parts.append('<text transform="translate(15 %g) rotate(-90)" text-anchor="middle">%s</text>' % (top+ph/2,html.escape(ylabel)))
    parts.append('<text x="%g" y="%g" text-anchor="middle">%s</text>' % (left+pw/2,height-8,'R bin midpoint' if 'histogram' in title.lower() else 'Session date (New York)'))
    for j,(name, values) in enumerate(series):
        color = colors[j%len(colors)]
        if bars:
            bw = min(35,pw/max(1,len(values))*.65)
            for i,v in enumerate(values):
                parts.append('<rect x="%g" y="%g" width="%g" height="%g" fill="%s"><title>%s: %.4f</title></rect>' % (x(i)-bw/2,min(y(0),y(v)),bw,max(.5,abs(y(v)-y(0))),color,html.escape(labels[i]),v))
        else:
            points = ' '.join('%.2f,%.2f' % (x(i),y(v)) for i,v in enumerate(values))
            parts.append('<polyline fill="none" stroke="%s" stroke-width="2.5" points="%s"/>' % (color,points))
        parts.append('<text x="%g" y="22" style="fill:%s">%s</text>' % (left+j*280,color,html.escape(name)))
    parts.append('</svg></section>')
    return ''.join(parts)


def equity_chart(title, run, spy):
    labels = ['Start']+[d['date'][5:] for d in run['daily']]
    return chart(title, [('Strategy',[run['initial']]+[d['equity'] for d in run['daily']]),
                         ('SPY',[spy['initial']]+[d['equity'] for d in spy['daily']])],labels,'Account value ($)')


def publish(result):
    seal = result['seal']
    winner = result['runs'][result['winner_label']]
    spy = result['spy']['400']
    base_label = 'base | $400 | costs 1x | locked'
    base = result['runs'][base_label]
    trend = result['runs']['trend | $400 | costs 1x | locked']
    wm,bm,sm,tm = metrics(winner,spy),metrics(base,spy),metrics(spy,spy,True),metrics(trend,spy)
    ci = result['inference']
    corrected = result['validation_inference']['bonferroni_p_30']
    lead = wm['return_pct']-sm['return_pct']
    summary = [
        'Evaluation: %s–%s (%d sessions); locked test: %s–%s (%d sessions).' %
        (seal['train'][0],seal['locked'][-1],len(seal['train'])+len(seal['validation'])+len(seal['locked']),seal['locked'][0],seal['locked'][-1],len(seal['locked'])),
        'Data: Yahoo five-minute OHLCV; %d daily-history symbols, %d intraday symbols; formula picks only, no Claude or headlines.' % (result['universe_size'],result['intraday_symbols']),
        'Locked $400 live-like base: %.2f%%; SPY: %.2f%%.' % (bm['return_pct'],sm['return_pct']),
        'Headline — validation winner %s: %.2f%% on locked data, %+.2f percentage points versus SPY, after base costs.' % (seal['winner']['name'],wm['return_pct'],lead),
        ('The selected strategy did not beat SPY on the locked test.' if lead <= 0 else 'The selected strategy beat SPY in this sample; this is not reliable evidence of an edge.'),
        'Predeclared trend-only filter fallback: %.2f%%; %s than the winner.' % (tm['return_pct'],'better' if tm['return_pct'] > wm['return_pct'] else 'no better'),
        'Mean daily excess 95%% block-bootstrap CI: [%.4f%%, %.4f%%]; two-sided p=%.4f.' % (ci['ci_low_pct'],ci['ci_high_pct'],ci['p_two_sided']),
        'Too few independent market days to establish an edge. No live setting changes recommended.'
    ]
    assumptions = [
        'The fixed 30 variants and selection criterion were declared before the test. Selection uses $400/base-cost validation excess return; ties prefer fewer trades, then declaration order. The last 20% is opened only after selection.json is written. The base and trend fallback, both capital sizes, three costs and paper control were predeclared; none can replace the winner headline.',
        'Charts of all variants use validation, not locked data: testing every variant on the holdout would defeat the requested one-winner holdout. Only the winner and predeclared controls are measured on locked data. Each segment starts with fresh capital and no carried risk halt; all segments may use price history strictly before each simulated moment.',
        'Entry/stop/target rules call desk.outcome._breakout and the vendored exit planner; sizing calls trader-budget.size_trade; circuit breakers call desk.breaker.status; calendar and early-close rules come from trader-entry and desk.settings.for_day. Time Machine uses its actual 10,000-path simulation (or the explicitly recorded CLI replay count), a fixed seed of 19 and only prior complete sessions. Its 20-session warmup is excluded from evaluation.',
        'No duplicated trading rule engine or broker connection. Default settings are copied in memory; user settings files are never loaded. Shorts are not tested because live-like accounts are cash accounts. Paper control uses PAPER_BOLD, immediate settlement and the desk’s buying-power calculation with 4x leverage; whole-share budget sizing still applies.',
        'Signals use only completed five-minute candles. Pending orders fill only in a later candle, at the limit or a better gap-open price. Stops win ambiguous bars; a gap below the stop fills at the lower open. Targets are never credited in the fill candle. At flat-by, the known calendar schedules a market exit at that candle’s open, observed when its candle closes.',
        'Base execution cost is the desk’s round-trip est_cost_per_share plus $0.01 per share per side, charged as cash deductions. Zero and double scenarios re-simulate sizing and cash, not just subtract fees afterward. Whole shares; no leverage and T+1 sale proceeds for live-like accounts. Pending orders reserve cash, including entry costs.',
        'SPY is a fractional, fully invested price-return benchmark bought at the previous close before each segment, so the first day return is included. Dividends, taxes, interest and SPY trading friction are excluded; strategy costs are explicit. Yahoo close (not adjusted close) is used. This is a downloaded history, not an as-of vendor archive: corrections or retroactive corporate-action adjustments can affect whole-share affordability and cent rounding. Timestamp blindness does not eliminate that revision bias. R uses planned limit-to-stop risk, including net costs in the numerator.',
        'Sharpe and Sortino use daily returns, 252 trading days/year, risk-free rate zero; Sortino uses all days in downside deviation. CAGR is suppressed below 252 sessions. Drawdown includes the starting balance. Time in market is the fraction of regular-session five-minute intervals with exposure, not capital-weighted. Buy-and-hold trade win rate/R/profit factor are N/A. Profit factor with no losses, or ratios with zero denominators, are N/A.',
        'Inference: 10,000 paired circular moving-block bootstrap resamples, five-session blocks, seed 19. CI estimates the mean daily strategy-minus-SPY return; the two-sided p-value resamples centered differences under the null. Dependence and regime shifts can invalidate it. Validation p is Bonferroni-adjusted for 30 variants; this is a conservative multiple-testing correction, not a claim that tuning creates 30 independent trials.',
        'Rolling five-session tuning diagnostics are logged. No repeated model refit or additional rolling winner selection is performed: the available history is too short after the 20-session odds warmup. Test controls are sensitivity checks, not additional opportunities to select a winner.',
        'Survivorship: the Nasdaq Trader directory is current, not historical. Delisted stocks and prior ticker mappings are missing, and IPOs need sufficient prior bars. This could reverse a small apparent edge; its magnitude cannot be estimated from this dataset. Failed daily downloads are explicitly listed in data_failures_daily.json; screen results cover only successful current listings, not a survivorship-free historical market.',
        'Movers: each 09:00 screen takes the top 25 prior-session volumes, top 25 prior-close gains and top 25 losses across the available directory. The desk’s average-volume/price filters, 40-candidate chart budget and prior-day ATR ranking are reused. No present-day Yahoo screener is used. Core watchlist order breaks simultaneous-signal cash competition; pending fills use alphabetical order.',
        'Missing bars block new signals through that moment. Missing candles while holding or with pending orders cause a hard failure, rather than a made-up exit. The feed is unofficial, may be delayed/revised and is not a historical order book. Full fills assume liquidity; spreads, queue priority, halts and within-candle chronology are not observable. No historical headlines or Claude actions are included.',
        'Data-source check: Yahoo actually returned five-minute data from 2026-08-27 and hourly data from 2023-12-22 in this download. Hourly bars cannot recreate a 15-minute opening range. Alpha Vantage historical intraday is premium and needs a key, so it was not used. Dukascopy exposes longer historical bid/ask exports; its FAQ defines volume as summed best-bid or best-ask volume. That is not the exchange share-volume input this screener requires, so it was not substituted for Yahoo OHLCV. Stooq’s archive presented a JavaScript verification barrier. No sign-up, paid source or account creation was attempted.',
        'No live setting change is supported by this short locked sample, regardless of the winner’s sign. Observe several months of genuinely new paper trading before treating any apparent excess return as evidence. Tuning/data/code hashes and the selected parameters are sealed in selection.json; identical reruns render saved experiment results without replaying the holdout.'
    ]
    metric_rows = []
    all_runs = dict(result['runs'])
    all_runs.update({'SPY | $'+capital+' | locked':run for capital,run in result['spy'].items()})
    for phase, source, comparison in [('tuning',result['training_runs'],result['training_spy']),('validation',result['validation_runs'],result['validation_spy'])]:
        for name in ('base',seal['winner']['name']):
            label = name+' | $400 | '+phase
            all_runs[label] = source[name]
        all_runs['SPY | $400 | '+phase] = comparison
    for label,run in all_runs.items():
        comparison = result['spy'][str(int(run['initial']))] if label.endswith('locked') else (result['validation_spy'] if label.endswith('validation') else result['training_spy'])
        metric_rows.append(dict(run=label, **metrics(run,comparison,label.startswith('SPY'))))
    write_csv(OUT/'metrics.csv',metric_rows)
    daily_rows,trade_rows,decision_rows = [],[],[]
    for label,run in all_runs.items():
        daily_rows.extend(dict(run=label,**d) for d in run['daily'])
        trade_rows.extend(dict(run=label,**t) for t in run['trades'])
        decision_rows.extend(dict(run=label,**d) for d in run['decisions'])
    # Keep the complete tuning and validation audit, including rejected variants.
    for phase,source in [('tuning',result['training_runs']),('validation',result['validation_runs'])]:
        for name,run in source.items():
            if name in ('base',seal['winner']['name']):
                continue
            label = name+' | $400 | '+phase
            daily_rows.extend(dict(run=label,**d) for d in run['daily'])
            trade_rows.extend(dict(run=label,**t) for t in run['trades'])
            decision_rows.extend(dict(run=label,**d) for d in run['decisions'])
    write_csv(OUT/'daily.csv',daily_rows)
    write_csv(OUT/'trades.csv',trade_rows,('run','date','symbol','pnl','r'))
    write_csv(OUT/'decisions.csv',decision_rows)
    write_csv(OUT/'variants.csv',result['variants'])
    (OUT/'data_failures_daily.json').write_text(json.dumps(result['daily_errors'],indent=2))
    sources = [('Yahoo observed five-minute SPY endpoint','https://query2.finance.yahoo.com/v8/finance/chart/SPY?range=60d&interval=5m'),
               ('Nasdaq Trader symbol directory','https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs'),
               ('Alpha Vantage historical intraday documentation','https://www.alphavantage.co/documentation/'),
               ('Dukascopy historical export','https://www.dukascopy.com/swiss/english/marketwatch/historical/'),
               ('Dukascopy volume definition','https://www.dukascopy.com/swiss/english/about/faq/'),
               ('Stooq archive','https://stooq.com/db/h/')]
    text = '# Backtest results\n\n'+'\n'.join(str(i+1)+'. '+s for i,s in enumerate(summary))
    text += '\n\n## Full metrics\n\n'+markdown_table(metric_rows)
    text += '\n\n## Selection and inference\n\n'+json.dumps(seal,indent=2).join(['```json\n','\n```'])
    text += '\n\n'+markdown_table([ci])+'\n\nValidation Bonferroni p (30 trials): '+fmt(corrected)
    text += '\n\n## Methods, recommendations and limits\n\n'+'\n\n'.join(assumptions)
    text += '\n\n## Sources\n\n'+'\n'.join('- [%s](%s)' % pair for pair in sources)
    text += '\n\n## Reproduce\n\n```sh\ncd ~/Projects/trading-desk-public-main\npython3 research/backtest/backtest.py --variant base\nopen research/backtest/out/report.html\npython3 -m unittest discover -s research/backtest\npython3 -m unittest discover -s tests\n```\n'
    (HOME/'RESULTS.md').write_text(text)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Blind backtest — locked results</title>',
             '<style>:root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:1200px;margin:30px auto;padding:0 20px;line-height:1.5}h1{font-size:2rem}section{margin:28px 0}svg{width:100%;height:auto}svg text{font-size:12px;fill:currentColor}.grid{stroke:currentColor;opacity:.15}.scroll{overflow:auto}table{border-collapse:collapse;font-size:12px}th,td{border:1px solid #8886;padding:7px;white-space:nowrap;text-align:right}th:first-child,td:first-child{text-align:left}.note{border-left:4px solid #e79824;padding:10px 18px}a{color:#3087e5}</style>',
             '<h1>Blind backtest: locked test results</h1><div class="note">'+''.join('<p>'+html.escape(s)+'</p>' for s in summary)+'</div>']
    parts.append(equity_chart('Validation winner on locked data — $400, base costs',winner,spy))
    labels = ['Start']+[d['date'][5:] for d in winner['daily']]
    parts.append(chart('Locked drawdowns', [('Strategy',[100*x for x in drawdowns([400]+[d['equity'] for d in winner['daily']])[0]]),('SPY',[100*x for x in drawdowns([400]+[d['equity'] for d in spy['daily']])[0]])],labels,'Drawdown (%)'))
    parts.append(chart('Locked daily P&L',[('Net P&L',[d['pnl'] for d in winner['daily']])],[d['date'][5:] for d in winner['daily']],'Daily P&L ($)',True))
    rs = [t['r'] for t in winner['trades']]
    if rs:
        lo,hi = min(rs),max(rs)
        width = (hi-lo)/12 or 1
        bins = [0]*12
        for r in rs:
            bins[min(11,int((r-lo)/width))] += 1
        parts.append(chart('Locked R per trade histogram',[('Trade count',bins)],['%.2f' % (lo+(i+.5)*width) for i in range(12)],'Trades',True))
    else:
        parts.append('<p>No locked trades: R histogram is empty.</p>')
    parts.append('<h2>Predeclared test controls</h2>')
    for label,run in result['runs'].items():
        if label != result['winner_label']:
            parts.append(equity_chart(label,run,result['spy'][str(int(run['initial']))]))
    parts.append('<h2>Full metrics</h2>'+table(metric_rows))
    parts.append('<h2>Uncertainty</h2>'+table([ci])+'<p>Validation Bonferroni-adjusted p (30 trials): '+fmt(corrected)+'</p>')
    parts.append('<h2>All 30 variants: validation only</h2><p>These charts cannot be used to choose another headline from the locked test.</p>'+table(result['variants']))
    for name,run in result['validation_runs'].items():
        parts.append(equity_chart(name+' — validation',run,result['validation_spy']))
    parts.append('<h2>Methods and limitations</h2>'+''.join('<p>'+html.escape(s)+'</p>' for s in assumptions))
    parts.append('<h2>Sources</h2><ul>'+''.join('<li><a href="%s">%s</a></li>' % (url,html.escape(name)) for name,url in sources)+'</ul>')
    parts.append('<p>All charts and styles are embedded. This report works offline; external links are optional references.</p></html>')
    (OUT/'report.html').write_text(''.join(parts))
