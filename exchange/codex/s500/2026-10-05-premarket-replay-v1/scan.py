"""Retrospective premarket screen; never imports broker code or submits orders."""
import concurrent.futures
import hashlib
import json
import statistics
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo('America/New_York')
DAY = '2026-10-05'
CUTOFF = datetime.fromisoformat(DAY + 'T09:25:00').replace(tzinfo=ET)
UNIVERSE = {
    'broad_etf': ['SPY', 'QQQ', 'IWM', 'DIA'],
    'sector_etf': ['XLK', 'XLF', 'XLE', 'XLV', 'XLI', 'XLY', 'XLP', 'XLU', 'XLB', 'XLRE', 'XLC'],
    'stock': ['AAPL', 'MSFT', 'NVDA', 'AMD', 'AVGO', 'TSLA', 'META', 'AMZN', 'GOOGL', 'NFLX', 'PLTR', 'COIN', 'HOOD', 'MU', 'ARM', 'ORCL', 'SMCI', 'RKLB', 'IONQ'],
    'leveraged_etf': ['TQQQ', 'SOXL'],
}


def fetch(symbol):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=5d&includePrePost=true'
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(request, timeout=25) as response:
            content = response.read()
        (ROOT / 'raw' / (symbol + '.json')).write_bytes(content)
        result = json.loads(content)['chart']['result'][0]
        return {'symbol': symbol, 'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'raw_sha256': hashlib.sha256(content).hexdigest(), 'chart': result}
    except urllib.error.HTTPError as error:
        return {'symbol': symbol, 'url': url, 'error': f'HTTP_{error.code}'}
    except Exception as error:
        return {'symbol': symbol, 'url': url, 'error': type(error).__name__}


def analyze(source, category):
    result = {k: v for k, v in source.items() if k != 'chart'}
    result['category'] = category
    if 'error' in source:
        result['screen_status'] = 'data_unavailable'
        return result
    chart = source['chart']
    quotes = chart['indicators']['quote'][0]
    # Never use metadata's regularMarketPrice/high/low/volume or today's regular-session bars.
    bars = []
    excluded = 0
    for i, stamp in enumerate(chart.get('timestamp', [])):
        start = datetime.fromtimestamp(stamp, ET)
        if start + timedelta(minutes=5) > CUTOFF:
            excluded += 1
            continue
        if any(quotes[k][i] is None for k in ('open', 'high', 'low', 'close')):
            continue
        bars.append({'start': start.isoformat(), 'end': (start + timedelta(minutes=5)).isoformat(),
                     **{k: quotes[k][i] for k in ('open', 'high', 'low', 'close', 'volume')}})
    today = [b for b in bars if b['start'][:10] == DAY and time(4) <= datetime.fromisoformat(b['start']).time() < time(9, 25)]
    previous = [b for b in bars if b['start'][:10] < DAY and time(9, 30) <= datetime.fromisoformat(b['start']).time() < time(16)]
    result['discarded_future_or_incomplete_bars'] = excluded
    if not today or not previous:
        result['screen_status'] = 'missing_premarket_or_previous_session'
        return result
    previous_day = max(b['start'][:10] for b in previous)
    previous_session = [b for b in previous if b['start'][:10] == previous_day]
    last = today[-1]
    previous_close = previous_session[-1]['close']
    gap = 100 * (last['close'] / previous_close - 1)
    late = [b for b in today if datetime.fromisoformat(b['start']).time() >= time(9)]
    late_return = 100 * (last['close'] / late[0]['open'] - 1) if late else None
    threshold = 1.0 if category in ('stock', 'leveraged_etf') else 0.5
    fresh = (CUTOFF - datetime.fromisoformat(last['end'])).total_seconds() <= 300
    same_direction = late_return is not None and gap * late_return > 0
    up = abs(gap) >= threshold and fresh and same_direction
    volumes = [b['volume'] for b in today if b['volume'] is not None]
    volume_complete = len(volumes) == len(today) and sum(volumes) > 0
    historical_volumes = []
    prior_dates = sorted({b['start'][:10] for b in bars if b['start'][:10] < DAY})
    for prior_day in prior_dates:
        sample = [b for b in bars if b['start'][:10] == prior_day and time(4) <= datetime.fromisoformat(b['start']).time() < time(9, 25)]
        if sample and all(b['volume'] is not None for b in sample) and sum(b['volume'] for b in sample) > 0:
            historical_volumes.append(sum(b['volume'] for b in sample))
    result.update({
        'previous_session': previous_day, 'previous_close_proxy': previous_close,
        'previous_close_proxy_basis': 'last available completed 5m regular-session close, not official auction close',
        'last_completed_bar_start': last['start'], 'last_completed_bar_end': last['end'],
        'premarket_last': last['close'], 'premarket_gap_pct': gap,
        'premarket_high': max(b['high'] for b in today), 'premarket_low': min(b['low'] for b in today),
        'premarket_bar_count': len(today), 'late_premarket_return_pct': late_return,
        'recorded_premarket_volume': sum(volumes), 'positive_complete_volume_available': volume_complete,
        'prior_same_window_volume_days': len(historical_volumes),
        'premarket_volume_ratio_to_short_history': sum(volumes) / statistics.median(historical_volumes) if volume_complete and historical_volumes else None,
        'same_direction_as_0900_to_0925': same_direction, 'gap_threshold_pct': threshold,
        'screen_status': 'directional_watch_only' if up else 'does_not_pass_directional_screen',
        'execution_eligible': False,
        'execution_blockers': ['no archived contemporaneous option bid/ask', 'no timestamp-verified premarket news review'],
        'retained_premarket_bars': today,
        'source_previous_session_final_bar': previous_session[-1],
    })
    return result


def main():
    (ROOT / 'raw').mkdir(exist_ok=True)
    categories = {s: group for group, symbols in UNIVERSE.items() for s in symbols}
    protocol = {'method_version': 'premarket-replay-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'cutoff': CUTOFF.isoformat(), 'universe': UNIVERSE,
                'screen': 'ETFs abs gap >=0.5%; stocks/leveraged ETFs >=1%; 09:00-09:25 direction must agree; last bar end within 5 minutes of cutoff. Price-only watchlist, no options entry.',
                'bias_disclosure': 'Retrospective reconstruction after already viewing SPY/QQQ intraday moves; not blind or point-in-time archived validation.'}
    (ROOT / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = [analyze(s, categories[s['symbol']]) for s in pool.map(fetch, categories)]
    output = {'protocol': protocol, 'results': results, 'orders_submitted': 0, 'simulated_fills': 0}
    (ROOT / 'scan-results.json').write_text(json.dumps(output, indent=2) + '\n')
    for row in sorted(results, key=lambda r: -abs(r.get('premarket_gap_pct', 0))):
        print(json.dumps({k: v for k, v in row.items() if k in ('symbol', 'category', 'error', 'screen_status', 'premarket_last', 'premarket_gap_pct', 'late_premarket_return_pct', 'recorded_premarket_volume', 'positive_complete_volume_available', 'last_completed_bar_end', 'previous_close_proxy', 'premarket_high', 'premarket_low')}))


if __name__ == '__main__':
    main()
