"""Pre-registered research-only candidates. No broker or paper-engine imports."""
try:
    from .intraday_v4_engine import allowed_context, evaluate, SEMI
except ImportError:
    from intraday_v4_engine import allowed_context, evaluate, SEMI

VARIANTS = ("baseline", "ir1-r1")


def maintenance_context(strategy, symbol, ctx, sessions, timestamp, stage):
    """IR1-R1: preserve a neutral pullback, never admit a neutral signal/entry.

    Every existing numeric threshold, data requirement, sector confirmation,
    timeout, execution check and exit remains unchanged. No RANGE/DOWN/UNKNOWN
    relaxation. This hypothesis is not fitted to the six archived trades.
    """
    if allowed_context(strategy, symbol, ctx, sessions, timestamp, stage):
        return True
    if (strategy != "ir1" or stage not in {"SETUP", "ARMED"}
            or ctx["direction"] != "MIXED" or ctx["volatility"] != "NORMAL_VOL"
            or ctx.get("qqq") not in {"UP", "OTHER"}
            or ctx.get("spy") not in {"UP", "OTHER"}):
        return False
    if symbol in SEMI:
        reference = sessions.get("SOXX")
        x = reference.snapshots.get(timestamp) if reference else None
        return bool(x and x.get("u20") is not None and x["u20"] >= 0
                    and x.get("vwap") is not None and x["close"] > x["vwap"])
    return True


def evaluate_candidate(event, session, sessions, timestamp, ctx, queued,gate_trace=None):
    evaluate(event, session, sessions, timestamp, ctx, queued,
             maintenance_context=maintenance_context,gate_trace=gate_trace)


def metadata(variant):
    if variant not in VARIANTS:
        raise ValueError("Unknown research variant")
    return dict(name="V4 baseline" if variant == "baseline" else "V4-IR1-R1",
                version=1, status="UNVALIDATED" if variant != "baseline" else "FROZEN_BASELINE",
                changes=[] if variant == "baseline" else [
                    "Only IR1 SETUP/ARMED may persist in MIXED/NORMAL_VOL with neither reference DOWN",
                    "Original sector confirmation, full TREND_UP at signal and entry, all numeric rules retained"],
                hypothesis=None if variant == "baseline" else
                    "A controlled pullback may temporarily reduce market trend efficiency without invalidating continuation; require trend restoration before entry")
