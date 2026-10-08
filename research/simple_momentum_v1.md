# SIMPLE_MOMENTUM_V1 forward paper protocol

V3's automatic `paper_trader.py` and Simple V1 coexist. They have independent
controls, accounts, positions, logs and APIs. Simple never calls LiveAutoTrader,
PaperBroker or broker orders. Both use real Toss data. New accounts start OFF.
Existing persisted controls are restored; starting paper never changes LIVE ARM.

## Data and signals (unchanged)

Scan every 60 seconds. Use Toss US realtime MARKET_TRADING_VOLUME top-100,
filter price >= $5, day gain >= 5%, volume >=100k, dollar amount >=$1M, and
shortlist 15 (configurable, maximum20). Metadata excludes inactive/non-USD,
non-common shares/warrants/rights/preferred/exotic/ETN/leveraged instruments.
Fetch candles only for shortlisted symbols. No historical data collection.
The read-only transport permits five whitelisted market-data GETs, uses shared
OAuth/rate limiter, and cannot express POST or arbitrary/order/account paths.

Most recent10 consecutive completed regular-session1m bars:
H = latest maximum high among first9 bars, excluding confirmation. At least
one completed pullback bar must follow H before confirmation. Pullback low
is the minimum of those pullback bars, excluding confirmation.

- Impulse:100*(H/first bar open-1) >=2%.
- Pullback:100*(1-confirmation close/H) in[1%,3%].
- Confirmation:close>open AND close>previous high AND low>=pullback low.
- One position/pending signal, $100 notional, fractional quantity, no borrowing,
  pyramiding, averaging or shorting. Independent initial cash:$10,000.
- A completed trade locks that symbol for its NY entry date. Cancellation does
  not lock a symbol. The same completed trigger cannot be reused.

All timestamps require explicit timezone offsets. `start` means[T,T+1m);
`end` means exclusive[T-1m,T). **No production semantics are inferred.**
`SIMPLE_TIMESTAMP_KIND_CONFIRMED=false` is the runtime default and blocks NEW
entries, with a status/UI warning. Confirm actual EC2 payload semantics before
setting it true. Logs retain sourceTimestamp, sourceTimezone, parsedTimezone,
bar start/end, declared timestamp kind and scanTimestamp.

## Forward execution (observable_scan_price_v2)

A completed confirmation creates ENTRY_PENDING. It cannot fill in its creation
scan. On the first subsequent scan with valid data, fill at the CURRENT observed
price plus2bps. Never retrieve a previously completed candle's open for execution.
Entry must be within90 seconds of trigger bar completion and before session
cutoff. Too late cancels; missing fresh observation waits only until that limit.

Prefer GET /api/v1/prices, matching symbol, positive finite lastPrice and aware
source timestamp no more than15 seconds old (configurable), never future-dated.
Receipt time and source time remain distinct; receiving an old quote does not
make it fresh. If quotes fail, an explicitly declared currently FORMING candle's
last closePrice may be used as a labelled snapshot. Its open/high/low are not
execution prices. Historical completed candles are never fallback fills.
Fallback logs the source failure and warning: its intrabar last-trade age cannot
be independently verified. Unconfirmed candle semantics disallow this fallback.

Entry records include triggerTimestamp (completion), triggerDecisionTimestamp,
entryDecisionTimestamp (actual scan decision), firstObservedPriceTimestamp
(receipt), entryTimestamp (decision), entryMarketPrice, entryFillPrice,
entryLatencySeconds (decision minus trigger completion), and full source
observation. `entryFill` is a compatibility alias for entryFillPrice.

## Stops/exits and sampled excursions

Hard stop remains entryFill*0.988. Trail activates when an actual post-entry
observation reaches entryFill*1.008. Track highest observed post-entry price;
trail = that high*0.994. These are sampled observations, not historical candle
high/low replays. Unobserved intrabar peaks and stops cannot be reconstructed
as executable opportunities. This change can miss brief moves between scans;
MFE/MAE explicitly describe sampled excursions, not complete market extremes.

Each fresh observation checks hard stop first, existing trail second, session
cutoff third, then time stop. Every sell fills at that SAME observable price
minus2bps. A gap below a stop receives the worse current price, never the stop
level. No favorable high-before-low or ideal retrospective intrabar ordering.
Price exits' trigger time is first detection/receipt, not an unknown missed tick.
Scheduled time/session exits use their known deadline as trigger time, but only
fill when a valid observation arrives. Latency is decision minus trigger time.

After15 minutes, exit if trail has never activated (+0.8% progress definition).
Otherwise continue trailing. Session cutoff is15:50 NY or actual regular close
minus10 minutes on early-close days, whichever is earlier. Toss calendar controls
session eligibility. OFF cancels pending entries but continues open risk management.

Exit record:exitTriggerTimestamp, exitObservationTimestamp (receipt),
exitDecisionTimestamp, exitTimestamp, exitMarketPrice, exitFillPrice, exitReason,
exitLatencySeconds, PnL/return, sampled MFE/MAE and actual hold duration.
`exitFill` is a compatibility alias. No overnight entry is allowed; liquidation
may be late during an outage and is not retroactively credited to a cutoff price.

## DATA_STALE and recovery

Missing/invalid/stale/future/cached observations never fabricate a fill or change
cash. Retain unresolved position, set dataStatus=DATA_STALE, staleSince and
staleObservationCount, persist and log each failed observation. Ignore historical
candles for fills/peaks even if they reveal a missed stop during the outage.

On recovery use first fresh current observation, log DATA_RECOVERED, retain
lastStaleSince, staleRecoveredAt, count and everDataStale. Resume current stop/
trail/deadline checks. A recovered price above stop does not claim a missed
historical stop; a recovered price below stop sells at that worse observed price.
All affected exits set exitAffectedByDataStale=true for separate analysis.
UI shows stale status/count, latency, source/bar/scan times and timestamp warnings.

Legacy pending signals lacking the new execution model/decision time are cancelled
on restart. Existing legacy positions/cash/history remain intact and flagged
legacyEntryFillSemantics; future management uses observable prices. Old trades
are never rewritten as though their execution model had changed.

## Persistence/API

Simple directory:SIMPLE_PAPER_DATA_DIR or
PERSISTENT_DATA_DIR/paper_simple_v1 (default
/var/lib/market-career-dashboard/paper_simple_v1).
V3 directory:PAPER_V3_DIR (default /var/lib/market-career-dashboard/paper_v3),
with its existing root/data/paper_v3 fallback. Server rejects identical resolved
account directories. Separate state.json, trades.jsonl and decisions.jsonl;
Simple also uses signals.jsonl revision journal and exclusive .writer.lock.
Atomic/fsynced checkpoint, crash-recoverable trade projections; corrupt/foreign
state fails closed. No service/collector restart or LIVE flag change is performed.

Existing authenticated admin APIs:
- V3:GET /api/trading/paper/v3/status; POST /auto and /scan under that prefix.
- Simple:GET /api/trading/paper/simple-v1/status; POST /auto and /scan under that prefix.

Admin paper tab has independent V3 and Simple sections. Live order endpoints
are untouched. Genuine response schemas/timezones/clock freshness, session
calendar, writable storage and rate-limit capacity still require EC2 checks.
