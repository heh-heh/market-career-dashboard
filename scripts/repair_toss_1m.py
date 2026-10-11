#!/usr/bin/env python3
"""Read-only audit, isolated targeted collection, explicit offline repair CLI."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research.toss_1m_repair import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"REPAIR_BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
