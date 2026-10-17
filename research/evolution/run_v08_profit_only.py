#!/usr/bin/env python3
"""Opt-in V08 research only; no collector, broker or live-trading imports."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.evolution.run_v07_available import parser as base_parser, run


def parser():
    p = base_parser()
    p.description = __doc__
    p.set_defaults(config=Path(__file__).with_name('v08_profit_only_config.json'),
                   population=100, results_dir=Path('/mnt/research/evolution-v08-profit-only'))
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    import json
    if json.loads(args.config.read_text()).get('selection_policy') != 'v08_profit_only':
        p.error('V08 requires explicit v08_profit_only selection policy')
    if args.source_run and json.loads((args.source_run/'run.json').read_text()).get('config',{}).get('selection_policy') != 'v08_profit_only':
        p.error('V08 frozen evaluation requires V08 source; evaluate V07 controls with the original driver')
    if args.population != 100:
        p.error('V08 baseline fixes population at 100; use paired comparison for smaller research controls')
    try:
        return run(args)
    except (ValueError, OSError) as exc:
        p.exit(2, f'V08 RESEARCH BLOCKED: {exc}\n')


if __name__ == '__main__':
    main()
