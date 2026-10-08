# SIMPLE_MOMENTUM_V1 forward paper protocol

This engine starts OFF, uses only real Toss market data, and owns an independent
USD cash account. It never calls `LiveAutoTrader`, `PaperBroker`, or order APIs.
The existing V3 strategy engine and legacy paper endpoints are unchanged. This
branch's legacy paper account supports manual orders; it has no separate V3
automatic paper runner. The admin paper panel labels that account separately.

## Market data and universe

Every 60 seconds while enabled (or managing a position), use the existing US
`MARKET_TRADING_VOLUME` realtime top-100 ranking. Filter price >= $5, day return
>= 5%, volume >= 100,000 shares and trading amount >= $1M. If amount is absent,
record the explicitly labelled price*volume proxy. Sort survivors by day gain,
then amount, then ticker. Fetch metadata in one request and 1m candles only for
the first 15 suitable candidates, plus any open/pending instrument. Metadata
indicating inactive/non-USD/non-common stock/warrant/right/preferred/exotic/ETN
or leverage is excluded. Known leveraged/inverse ETFs are excluded. No account
borrowing is simulated; entry notional must fit this paper account's cash.

The read-only transport allows exactly four market-data GET paths, applies the
existing shared Toss rate limiter, and obtains tokens via the existing shared
OAuth provider. No broker submit function is imported. Position processing has
priority over ranking requests, including when rankings fail.

## Entry

Use the most recent 10 consecutive COMPLETED regular-session 1m bars. Source
timestamps require explicit timezone offsets. `SIMPLE_TIMESTAMP_KIND=start`
means [T,T+1m); `end` means exclusive [T-1m,T). Future and incomplete OHLCV never
participate in the setup. An incomplete bar supplies only its known open.

Let H be the maximum high in the first nine bars (exclude confirmation). Use
the most recent bar attaining H. At least one subsequent completed pullback
bar must precede confirmation. The pullback low is the minimum low of those
subsequent bars, also excluding confirmation.

- Impulse = 100*(H / first bar open - 1), at least 2%.
- Pullback = 100*(1 - confirmation close / H), within [1%,3%].
- Confirmation close > confirmation open AND close > previous bar high AND
  confirmation low >= previously observed pullback low.
- One pending signal, one position, no pyramid/averaging, long only. A completed
  trade locks that symbol for its NY entry date. Cancelled signals do not lock it.
- A trigger is journaled at its bar end. It cannot fill in the same scan that
  creates it. A later scan must find the exact next minute's open; no skipping
  to a later candle. Market must still be regular, before the exit cutoff, and
  no more than 90 seconds may have elapsed from that expected open.

This is a minute-open paper simulation resolved on REST delivery, not a claim
that a real order could have executed at an already elapsed opening tick.
`entryTimestamp` is the simulated market minute; `entryObservedAt` and
`entryDeliveryDelaySeconds` record when that fill became available. Old signals
are never generated retroactively. Fill = real next-minute open * 1.0002;
quantity = $100 / fill. Initial paper cash is $10,000.

## Exit and candle ordering

Hard stop = fill*0.988. Trail activates once a completed held candle's high
reaches fill*1.008. Highest price resets at entry, never to the pre-entry high.
Activated trail = highest post-entry price * 0.994.

Opening gaps first: fill at the worse open if it is below an existing stop.
Then scheduled exits use the open. On completed candles, hard stop precedes
the previously established trail. Test old stops before raising/activating the
trail using this candle's high. The new trail becomes an intrabar stop only
next minute. If the current completed close already breaches it, exit at that
close (never infer a favorable high-before-low path). All sells subtract 2bps.
Stop-candle excursions include only its known open and exit; full held candles
contribute their high/low. These excursion values are conservative OHLC bounds.

If the trail has never activated after 15 minutes, exit at that minute's first
available open. A trade that already reached +0.8% can continue under its trail.
Exit at 15:50 NY, or regular close minus ten minutes on early-close days, whichever
is earlier. Toss's session calendar controls holidays and early closes.

Missing position candles never produce invented stops. Flatten at a fresh real
open if available, mark `dataGap=true` and `DATA_GAP_EXIT`, and exclude such trades
from clean execution analyses. If real data is unavailable, retain the unresolved
position and expose an error; session liquidation can be late during an outage.
No new entry can coexist with that position. Stopping AUTO cancels pending entries
but continues stop/session management. Manual scan while OFF does not enter.

## Persistence and integration

`SIMPLE_PAPER_DATA_DIR` defaults to
`/var/lib/market-career-dashboard/paper_simple_v1`; otherwise
`PERSISTENT_DATA_DIR/paper_simple_v1` supplies the base default. Give the service
user write permission before deployment. No service/collector restart is done
by this change. No real trading flags are changed.

- `state.json`: atomic, fsynced checkpoint; independent cash, pending/open
  position, peak/trail, last scan, counters, per-day locks and latest candidates.
- `signals.jsonl`: durable trigger/control/entry/exit journal with revisions and
  state snapshots. Restart repairs a committed event missing from checkpoint.
- `trades.jsonl`: completed trade projection, unique trade IDs; recovered from
  the journal if a crash occurred before this projection was written.
- `decisions.jsonl`: each analyzed candidate with prices, ranking/liquidity,
  current/previous OHLCV, impulse/pullback, confirmation flags and rejection.
- `.writer.lock`: one writer per account directory. Corrupt/torn files fail
  closed rather than reset balances or discard an existing position.

Authenticated routes use the unchanged admin Bearer-session check:
`GET /api/trading/paper/simple-v1/status`,
`POST /api/trading/paper/simple-v1/auto` with JSON boolean `enabled`,
`POST /api/trading/paper/simple-v1/scan`.

Parameters are documented in `.env.example`; there is no optimization or historical
backtest. Production verification still requires genuine Toss credentials, live
response timestamp conventions and writable persistence storage.
