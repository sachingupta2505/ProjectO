import sys
sys.path.insert(0, '.')
import json
from pathlib import Path
from scripts.backtest_multi_index import backtest_index, INDEX_SPECS

df = backtest_index('NIFTY', INDEX_SPECS['NIFTY'])
records = []
for i, row in df.iterrows():
    reason = str(row['reason'])
    records.append({
        'trade_id': f'ORION_HIST_{i+1:03d}',
        'strategy': 'ORION-15',
        'index': 'NIFTY',
        'date': str(row['date']),
        'side': str(row['side']),
        'entry_time': str(row['entry_time']),
        'exit_time': str(row['exit_time']),
        'entry_premium': round(float(row['opt_entry']), 2),
        'exit_premium': round(float(row['opt_exit']), 2),
        'reason': reason,
        'breakeven_triggered': True if reason in ['TARGET 2', 'BREAKEVEN'] else False,
        'quantity': 65,
        'net_pnl': round(float(row['net_pnl']), 2),
        'mode': 'BACKTEST_BENCHMARK',
        'notes': f'6M Benchmark Trade {i+1}. Result: {reason}'
    })

out_path = Path('data/strategy_ledgers/orion_backtest_ledger.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(records, f, indent=2)
print(f'Populated {len(records)} benchmark trades into {out_path}')
