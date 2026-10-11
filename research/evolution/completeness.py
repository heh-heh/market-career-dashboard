"""Read-only XNYS evaluation coverage diagnostics; never invent missing observations."""
from collections import Counter

from research.backtest_v4_strategies import MINUTE, exchange_sessions


def completeness_facts(info, summary=None):
    summary = summary or {}
    calendar = exchange_sessions(info["fromDate"], info["toDate"])
    missing = list(info.get("missingSessions") or summary.get("missingSessions") or [])
    non_sessions = sorted(d for d in missing if d not in calendar)
    real = sorted(d for d in missing if d in calendar)
    requested = info.get("requestedSessions", summary.get("sessionsRequested"))
    completed = info.get("completedSessions", summary.get("sessionsProcessed"))
    coverage = info.get("sessionCoverage") or summary.get("sessionCoverage") or {}
    cause = None
    if non_sessions or (requested is not None and requested != len(calendar)):
        cause = "BASELINE_CALENDAR_MISMATCH"
    elif real:
        cause = "BASELINE_DATA_MISSING"
    elif coverage.get("incompleteSessions", 0):
        cause = "BASELINE_DATA_MISSING"
    elif completed is not None and completed != len(calendar):
        cause = "BASELINE_SESSION_COUNT_MISMATCH"
    elif info.get("resultsValid") is False or summary.get("resultsValid") is False:
        cause = "BASELINE_RESULTS_INVALID"
    if cause is None and summary and (summary.get("sessionsRequested") != len(calendar) or summary.get("sessionsProcessed", len(calendar)) != len(calendar)):
        cause = "BASELINE_SESSION_COUNT_MISMATCH"
    return dict(rootCause=cause, missingCount=len(missing), missingDates=missing[:40],
                missingDatesTruncated=len(missing)>40, nonTradingDates=non_sessions[:40],
                realMissingDates=real[:40], resultsValid=info.get("resultsValid"),
                requestedSessions=requested, completedSessions=completed,
                requestedCount=requested, completedCount=completed,
                expectedSessions=len(calendar), calendar="XNYS", sessionCoverage=coverage)


class SessionCoverage:
    """Bounded samples, exact aggregate counts, one session's rows at a time."""
    def __init__(self, symbols, sample_limit=40):
        self.symbols = sorted(set(symbols))
        self.limit = sample_limit
        self.counts = Counter()
        self.samples = []
        self.incomplete = 0

    def observe(self, day, rows, opening, closing):
        expected = int((closing-opening)/MINUTE)
        failures = []
        for symbol in self.symbols:
            minutes = rows.get(symbol, {})
            observed = sum(opening+i*MINUTE in minutes for i in range(expected))
            missing = expected-observed
            self.counts[symbol] += missing
            if missing:
                failures.append(dict(symbol=symbol, missingMinutes=missing, expectedMinutes=expected,
                                     code="SYMBOL_SESSION_MISSING" if observed == 0 else "REGULAR_MINUTES_MISSING"))
        if failures:
            self.incomplete += 1
            if len(self.samples) < self.limit:
                self.samples.append(dict(date=day, earlyClose=expected<390, failures=failures))

    def snapshot(self):
        return dict(calendar="XNYS", timestampKind="end", incompleteSessions=self.incomplete,
                    missingMinutesBySymbol=dict(self.counts), samples=self.samples,
                    samplesTruncated=self.incomplete>len(self.samples))
