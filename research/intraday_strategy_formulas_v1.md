# Intraday strategy formulas and execution specification v1

Date: 2026-10-10. Contract ID: `IR_SPEC_V1`.
Read [the research document](intraday_strategy_research_v1.md) for hypotheses,
evidence limits and acceptance criteria. IR1/IR2/IR3 are a NEW research namespace.
They do not amend frozen V4 S1/S2/S3, V3 or Simple Momentum V1.
Status: specified for future synthetic implementation; no profitability evidence.
Primary-source review and actual data/fee confirmations remain prerequisites.

## 1. Time, availability and missing data

`i` is a symbol, `b` its declared benchmark, `d` a New York trading date.
`m_t=(O,H,L,C,V,start,end)` is a 1-minute bar; `B_j` is a 5-minute bar.
`t` denotes an EXCLUSIVE bar end, independently of the raw source label.
START 10:00 means [10:00,10:01); exclusive END 10:01 means the same bar.
Inclusive :59 end labels require a separately reviewed contract, not guesswork.

- Aggregate from the official regular-session open. The 10:00–10:04 raw bars
  form a 5-minute bar available only after 10:05 and receipt of all five bars.
- Exactly five consecutive completed minutes are required. Never bridge gaps,
  forward-fill OHLCV, or expose future completed aggregates to earlier minutes.
- At signal evaluation, `W=min(latest completed minute end of required assets)`.
  Required 1-minute reference endpoints must match W exactly. Latest 5-minute
  endpoints must be <=W. No nearest/future matching or backward fill.
- A missing endpoint/window, invalid source, conflicting duplicate or zero
  denominator yields unavailable + reason; never substitute zero or neutrality.
- Source timezone and START/exclusive-END semantics require reviewed provenance.
  Unconfirmed/naive/ambiguous interpretations block new entries. Store raw labels,
  source timezone, normalized ends, receipt and decision times separately.
- Daily inputs and metadata must be known before session d. No current-day final
  volume/close/high/low. Rolling intraday returns/ER must stay inside that session.
- ATR5 alone may warm up with prior RTH bars. The first RTH bar's TR5 is H-L;
  exclude the overnight gap from intraday TR. Daily TR includes overnight gaps.
- Reset VWAP/OR/event state on a new session, never erase unresolved positions.
- No 15-minute indicator is required; OR15 is a frozen session structure.

### Raw variables

| Variable | Formula/units/valid range | Lookback; data; causal availability | Baseline |
|---|---|---|---|
| O,H,L,C,V | USD prices, shares; positive prices, H>=max(O,C), L<=min(O,C), H>=L, V>=0 | Completed 1-minute OHLCV after end AND receipt | Raw 1m |
| O5,H5,L5,C5,V5 | First O, max H, min L, last C, sum V | Five consecutive completed RTH minutes after final end | 5m |
| P_obs | Current bid for long exit/mark if valid bid/ask, otherwise current last; finite >0 | Actual source/receipt/decision timestamps | Fresh quote preferred |
| bid,ask | USD; 0<bid<=ask | Current validated source times | Optional |
| C_prev,O_RTH | Prior RTH close; today's first RTH open; USD >0 | Previous completed session / known opening bar | Per session |
| H_sofar,L_sofar | Max/min of completed RTH highs/lows so far | Prefix ending <=W | Diagnostic |
| tau_trigger,tau_decision | Trigger's logical end; actual signal-processing time | Aware timestamps | Distinct fields |
| tau_source,tau_receive,tau_exec | Source observation, receipt, simulated fill decision | source<=receive<=decision | Distinct fields |

## 2. Common feature dictionary

`C[t-n]` means the completed close exactly n minutes earlier, not the nth available
row across a gap. All reference inputs use the same endpoints and price basis.

| Variable | Formula / units / valid range | Lookback, required data, causal timestamp | Default |
|---|---|---|---|
| u(n) | ln(C[t]/C[t-n]); log return, real | n+1 completed same-session minute closes, t<=W | n=20; recovery uses 5 |
| TR5[j] | max(H5-L5,abs(H5-C5_prev),abs(L5-C5_prev)); first session bar H5-L5; USD>=0 | Completed 5m at j end | One bar |
| ATR5[j] | Mean of 14 TR5 through j; USD>0 | 14 completed 5m, prior RTH warmup allowed | 14, SMA not Wilder |
| A | ATR5[j-1] immediately before event SETUP; USD>0, frozen | Available at SETUP, never updated during the event/trade | Fixed event scale |
| ATR_D | Mean previous 14 daily TR; TR=max(H-L,abs(H-Cprev),abs(L-Cprev)); USD>0 | Previous completed daily bars only | 14 |
| ADV20 | Median(daily close*volume); USD>=0 | Previous 20 completed sessions; reviewed point-in-time dollar basis | 20 |
| VWAP[t] | sum(((H+L+C)/3)*V)/sum(V); USD>0 | Completed RTH prefix; volume sum>0 | Session reset |
| DIST[t] | (C[t]-VWAP[t])/latest completed ATR5; ATR units, real | Completed minute and past 5m | Direction sign/diagnostic |
| S10[t] | (VWAP[t]-VWAP[t-10])/latest completed ATR5; ATR units, real | Eleven valid same-session VWAP snapshots | 10 minutes |
| ER20 | abs(C[t]-C[t-20])/sum(k=t-19..t,abs(C[k]-C[k-1])); [0,1] | 21 completed minutes; zero path length unavailable | 20 changes |
| RV20 | sqrt(sum(k=t-19..t,ln(C[k]/C[k-1])^2)); nonnegative log-return scale | 21 completed same-session closes | 20 minutes |
| p_vol | Past same-clock percentile of today's RV20; [0,1] | Previous 60 sessions, min40 valid observations, never today in denominator | 60/min40 |
| G | (O_RTH-C_prev)/ATR_Dprev; signed daily ATR units | After opening price known, previous daily ATR | IR2 abs(G)<=1 |
| RVOLcum | Current completed RTH cumulative V / median(previous20 same-prefix V); [0,infinity) | Entire prefix complete, min10 valid prior sessions, no final-day V | 20/min10 |
| RVOLopen | Opening30 V / median(previous20 opening30 V); [0,infinity) | Exact 09:30–10:00 completed interval, min10 prior sessions | 20/min10 |
| VA1 | Current V / median(previous20 completed 1m V); [0,infinity) | Excludes current minute from denominator | 20 |
| RS20 | u_i(20)-1.0*u_b(20); log return, real | Symbol/benchmark exact endpoint match | Beta=1 proxy |
| RS20A | RS20*C_i[t-20]/A; ATR units, real | Frozen A and completed endpoints | IR1 >=0.25 |
| sigma_open | Sample std(previous60 opening returns); log return >0 | ddof1, min40 previous sessions | 60/min40 |
| Z_open | u_open/sigma_open; real, no mean subtraction | u_open=ln(C10:00/O09:30); denominator known before today | IR3 >=0.50 |

No EMA/RSI/ADX/Bollinger signal is required. Features are not universal AND gates.
RS20 is not beta-neutral alpha. Diagnostic beta60=Cov(past daily stock returns,
benchmark returns)/Var(benchmark returns) uses 60 aligned prior daily returns;
requires positive variance; it is NOT a baseline gate or optimization dimension.

Past-distribution percentile: `(count(y<x)+0.5*count(y=x))/N`.
Cross-sectional percentile: `(average ordinal rank-1)/(N-1)`, N=1 gives0.5.
Missing values remain missing. Do not confuse these two reference populations.

## 3. Universe and benchmark mapping

| Instrument group | RS benchmark | Required market inputs | Sector diagnostics | Baseline entries |
|---|---|---|---|---|
| NVDA AMD MU INTC AVGO MRVL AMAT LRCX KLAC TSM QCOM | SOXX | QQQ/SPY | SMH optional | IR1/IR2 |
| AAPL MSFT META AMZN GOOGL TSLA PLTR NFLX COIN MSTR | QQQ | QQQ/SPY | Declared sector optional | IR1/IR2 |
| SPY/QQQ | Self identity, RS unused | Both SPY and QQQ | None | IR3 only |
| IWM/SOXX/SMH | References only | Load only if used | Explicit metadata | No baseline entries |
| TQQQ/SQQQ/SOXL/SOXS | QQQ/QQQ/SOXX/SOXX underlying identities only | Inverse/leverage logic unspecified | SMH optional | EXCLUDED |
| Future common stocks | Explicit reviewed SPY/QQQ/sector mapping | Strategy's required assets | As declared | Unmapped fails closed |

Never silently substitute SMH for missing required SOXX. IR2 gates only market
recovery: missing optional sector diagnostics does not block it. IR1 semiconductor
SOXX is required. Long only; no automatic inverse rules or naked short selling.

Daily eligibility:
```text
(USD and active and reviewed US common stock)
  OR (USD and active and reviewed equity ETF in {SPY,QQQ,IWM,SOXX,SMH})
AND previous reviewed raw point-in-time price >=5 USD
AND ADV20 >=50,000,000 USD
AND >=61 prior completed daily sessions and valid provenance/adjustment
AND, if actual bid/ask exists, (ask-bid)/mid <=0.0015
```
The OR applies only to instrument type. Strategy entry permissions remain as in
the table. No upper price cap. Exclude bond/opaque/non-equity ETFs, preferreds,
ETNs, warrants, rights, inactive/non-USD and leveraged/inverse instruments.
Market cap/catalyst is diagnostic if genuinely available, never fabricated.
Absolute $5/dollar-volume eligibility cannot use unknowingly back-adjusted prices.

### Operational candidate ranking, not expected-return estimation

Within the fixed eligible pilot seed, use batch fresh prices and prior cache for
cheap priority: `abs(ln(P_current/C_prev))/(ATR_D/C_prev)` descending, then ADV20,
then symbol. Fetch detailed candles for top15 stocks only. Required ETF references
and pending/open instruments have priority outside that stock-detail queue.
Do not derive RTH RVOL from a ranking volume whose session semantics are unknown.

For detailed eligible candidates at a common watermark:
```text
cost_rt = 2*s + (known spread fraction OR 2*halfspread_proxy)
          + (estimated entryFeeUSD+exitFeeUSD)/orderUSD
headroom = (ATR_D/C_prev)/cost_rt
CandidateScore = 50*P_cross(RVOLcum) + 50*P_cross(headroom)
```
Cost_rt>0; score[0,100]. Inadequate volume history gives score=null and
QUEUE_UNAVAILABLE, not zero. Equal ranks give the two operational dimensions equal
votes; they are not fitted profit weights. Tie: ADV20 descending, symbol ascending.
Persist the rank/score at signal creation; do not rank pending signals by their
later price paths. Process eligible pending signals in that frozen priority order.
IR3 capacity priority: Z_open descending, current spread ascending (unknown last),
symbol ascending. Log each prefilter/detail/capacity skip, not only traded winners.

## 4. Causal regime model

For b in {QQQ,SPY}:
`UP_b=(u20>0 AND DIST>0 AND ER20>=0.40)`;
`DOWN_b=(u20<0 AND DIST<0 AND ER20>=0.40)`.

```text
if required direction feature unavailable: direction=UNKNOWN
elif UP_QQQ and not DOWN_SPY: direction=TREND_UP
elif DOWN_QQQ and not UP_SPY: direction=TREND_DOWN
elif both ER20<=0.30 and both abs(S10)<=0.15: direction=RANGE
else: direction=MIXED

if volatility history unavailable: volatility=UNKNOWN
elif max(pQQQ,pSPY)>=0.90: volatility=HIGH_VOL
elif max(pQQQ,pSPY)<=0.20: volatility=LOW_VOL
else: volatility=NORMAL_VOL
```
Direction priority is as written. SectorUP=(SOXX u20>=0 AND C>VWAP).
RECOVERY=(QQQ u5>=0 AND SPY u5>=0 AND neither DOWN boolean).

- IR1 throughout pending entry: TREND_UP+NORMAL; semiconductor SectorUP.
- IR2 new SETUP: RANGE/MIXED+NORMAL; after breakdown: non-TREND_DOWN+NORMAL,
  and RECOVERY at reclaim/entry. A recovering UP market may finish that event.
- IR3: own ETF UP, other ETF not DOWN, NORMAL/HIGH, full390-minute session.
- UNKNOWN blocks new entries. Reference failure must NOT stop management of an
  existing position if its own fresh price exists. No regime-only baseline exit.

## 5. IR1_RS_PULLBACK

Hypothesis: relative impulse, limited lower-volume selling and recovery of a
frozen pullback structure proxy continuing demand. Common stocks only.
Entry window [10:15,14:00) New York. Completed structures remain within RTH.

| Local variable | Formula/units/range | Data, lookback, availability | Baseline |
|---|---|---|---|
| origin,end,advance | O5[j-2],C5[j],end-origin; USD, advance>0 | Three completed impulse bars | 3bars |
| pre_high | max(H5[j-8..j-3]); USD>0 | Six bars preceding impulse, no overlap | 30min |
| impulse_V | mean(V5[j-2..j]); shares/5min>0 | Freeze at SETUP | 3bars |
| pb_low | min(L5[j+1..j+n]); USD>0 | Subsequent1..3 completed5m | n1..3 |
| PB,VC | (end-pb_low)/advance; mean(V5pb)/impulse_V; ratios | Completed post-impulse bars only | PB[.20,.50],VC<=.70 |
| B,L_arm | Last qualifying pullback bar high+.10A; pb_low; USD | Freeze at FIRST qualifying ARMED | Never move |
| H_obs,R,MFE_R | Post-entry max observed mark; E-S; max(0,(H_obs-E)/R) | Actual post-entry observations only | Activate trail at1R |

State transitions:

| Transition | Exact observable condition / action |
|---|---|
| IDLE -> SETUP | First eligible impulse: advance>=1A, C5>pre_high+.10A, C5>VWAP, RS20A>=.25, valid context/window. Freeze impulse fields/A; consume daily attempt |
| SETUP -> ARMED | First n1..3 with PB[.20,.50], VC<=.70, all pullback closes>max(origin,VWAP_at_that_end). Freeze B/L_arm/armTime |
| SETUP -> CANCELLED | Completed1m low<end-.50*advance, close<=max(origin,VWAP), context fails, no qualification by third pullback, window ends |
| ARMED -> TRIGGERED | Later completed1m end>armTime and <=armTime+5min, close>B, VA1>=1, RS20A>=.25 and context valid |
| ARMED -> CANCELLED | Low<L_arm, close<=max(origin,VWAP), context/RS fails, required feature unavailable, deadline exceeded/window ends. Cancellation wins over simultaneous trigger |
| TRIGGERED -> ENTRY | Subsequent fresh quote, latency<=90s, context/RS/window maintained, E>B, E<=triggerClose+.30A, positive risk/cost guard, capacity |
| TRIGGERED -> CANCELLED | Guard/deadline/capacity fails or pre-entry observed mark<=L_arm. No replacement by a later event |
| ENTRY -> MANAGING | Record actual fill, fixed S=L_arm-.10A, fixed R=E-S; start observed excursions |
| MANAGING -> EXIT | Observed hard/trail stop, max45min, stall rule or session cutoff. Sell current observed price |

When observed MFE>=1R, effectiveStop=max(initialS,H_obs-1A), never loosen it.
No fixed profit target or partial exit. At first fresh observation at/after
entry+20min evaluate stall ONCE: MFE_R<.30 sets EXIT_PENDING for the next fresh
observation. Hard stops/deadlines take priority. Max hold45min sells at first
fresh observation after its deadline; it does not require an additional scan.

| Primary parameter/units | Default | Future coarse values, one-at-a-time |
|---|---:|---|
| impulse_atr /A |1.0 |[.75,1.0,1.25] |
| pullback_max /advance fraction |.50 |[.40,.50,.60] |
| contraction_max /ratio |.70 |[.60,.70,.80] |
| rs_min /ATR-normalized proxy |.25 |[.15,.25,.35] |
| trigger_VA1_min /ratio |1.0 |[.75,1.0,1.25] |
| max_hold /minutes |45 |[30,45,60] |

FIXED:3 impulse/6 pre-high/1..3 pullback bars, PBmin.20, buffer.10A, wait5min,
chase.30A, stop pullback-.10A, activation1R, trail1A, stall20min/.30R.
Expected frequency MEDIUM to LOW; supply contraction can instead be demand loss,
and beta/news/trend termination can defeat the event. No same-day re-arm.

## 6. IR2_FAILED_OR

Hypothesis: genuine trading outside a frozen opening range, inability to extend
lower and recovery inside proxy failed price acceptance, not generic VWAP fading.
Common stocks. Entry [10:00,11:00). Waiting until10:00 provides current-session
20-minute market context; this baseline does not test the earliest opening edge.

| Local variable | Formula/units/range | Data/availability | Default |
|---|---|---|---|
| ORL,ORH,ORM | minL,maxH,(ORL+ORH)/2; USD>0 | Exact15 consecutive09:30–09:45 minutes; frozen | OR15 |
| width_A | (ORH-ORL)/A; ratio>0 | Freeze A at SETUP |[.50,2.50] |
| penetration | (ORL-L5_break)/A; ratio | Completed breakdown bar |[.10,.75] |
| failure_low,t_break | L5_break, breakdown end; USD/time | Frozen when ARMED | Never update |
| reclaim_count | First completed5m AFTER breakdown is1, then2 | Breakdown bar explicitly excluded | Max2 |
| S,T,R | failure_low-.10A,ORM,E-S; USD | Boundaries frozen; actual E at entry | Target midpoint |

| Transition | Exact condition |
|---|---|
| IDLE -> SETUP | First eligible >=10:00 snapshot, completedOR, widthA[.5,2.5], absG<=1, RANGE/MIXED+NORMAL. Freeze A and consume attempt |
| SETUP -> ARMED | Later completed5m: priorC5>=ORL, C5<ORL-.10A, penetration[.10,.75]. Freeze break low/time |
| SETUP -> CANCELLED | SETUP regime fails, any completed1m low<ORL-.75A, required data invalid, entry window ends |
| ARMED -> TRIGGERED | First or second subsequent completed5m close>ORL+.10A, ALL post-break completed1m lows>=failure_low, RECOVERY, non-TREND_DOWN+NORMAL |
| ARMED -> CANCELLED | New strict lower low, prohibited regime/data, second bar finishes without eligible reclaim, window ends. Cancellation checked first |
| TRIGGERED -> ENTRY | Fresh subsequent quote, E>ORL, E<=triggerClose+.30A, S<E<T, estimated net reward/R>=1, RECOVERY retained, common guards |
| TRIGGERED -> CANCELLED | Guards/deadlines/capacity fail or pre-entry mark<failure_low; no later signal hunting |
| ENTRY -> MANAGING | Fix S/T/R, never widen stop after a gap |
| MANAGING -> EXIT | Observed mark<=S or >=T, max30min, forced cutoff; actual observed exit price |

At a first price reclaim without RECOVERY, wait for second bar if no cancellation;
second invalid reclaim cancels. This is a NEW IR2 contract, not an amendment to
frozen V4. Low==failure_low is permitted; low<failure_low cancels, including a
reclaim candle's low. Third-bar reclaim never qualifies.

For planning only: `netReward=T*(1-s-h_exit_est)-E-estimatedExitFee/qty`,
where h_exit_est is current half-spread if known, otherwise1bp. R=E-S.
This is not a promised future bid or fill. Actual exit at a bid does not add that
planning spread again. No trail, partial exit or stall rule. Volume climax and
sector alignment diagnostic only. Bad news or delayed reclaim can destroy reward.

| Primary parameter/units | Default | Future coarse values |
|---|---:|---|
| OR_minutes /minutes |15 |[5,15,30] |
| penetration_min /A |.10 |[.05,.10,.20] |
| penetration_max /A |.75 |[.50,.75,1.00] |
| reclaim_max /completed5m |2 |[1,2,3] |
| gap_max /dailyATR |1.0 |[.75,1.0,1.25] |
| max_hold /minutes |30 |[20,30,45] |

FIXED:10–11 window, width[.5,2.5]A, buffer.10A, chase.30A, strict lower low,
ORM target. LOW expected frequency; no re-arm after first SETUP cancellation.

## 7. IR3_CLOSE_FLOW

Hypothesis: positive opening30 return/participation and aligned late direction
proxy continuing broad-market positioning into the close. SPY/QQQ only.
Full390-minute sessions only; early-close sessions DISABLED, not time-compressed.
This is a modified E1 hypothesis, not its exact replication.

| Local variable | Formula/units/range | Source/availability | Default |
|---|---|---|---|
| u_open | ln(C10:00/O09:30); log return | Exact opening30 completed minutes |30min |
| Z_open,RVOLopen | Common table | Prior-only denominators, opening fields fixed |>=.50 />=1.25 |
| signalTime,entryDeadline |15:30,min(15:30+90s,15:50); NY time | Verified full-session calendar |15:30 /15:31:30 |
| A,S,R | ATR5_15:25,E-1A,1A; USD>0 | Pre-signal ATR; stop fixed at entry |1ATR |

| Transition | Exact condition |
|---|---|
| IDLE -> SETUP | First receipt of completed15:30 snapshot, lag<=90s, fullsession, Zopen>=.50, RVOLopen>=1.25, ownETF UP, otherETF not DOWN, NORMAL/HIGH. Clock check once/day |
| SETUP -> ARMED -> TRIGGERED | Atomic audit of that SAME available15:30 snapshot, freeze signalClose/A/context; no invented later confirmation bar |
| TRIGGERED -> ENTRY | First subsequent fresh observation by15:31:30, E<=signalClose+.30A, ownUP/othernonDOWN maintained, risk/quality/capacity guard |
| TRIGGERED -> CANCELLED | Lag/guards/context/capacity/history fails. Do not try15:35/15:40 again |
| ENTRY -> MANAGING | Fixed S=actualE-A, no target/trail |
| MANAGING -> EXIT | Observed mark<=S or first valid observation at/after15:50 |

Reconstruct the signal snapshot at15:30, never use15:31 data to pretend it was
known then. Entry may recheck the latest completed context at actual execution.
15:50 is both profit/time exit and force cutoff, normally about19-minute holding.
Do not add a15-minute stall or optimized partial take. LOW expected frequency,
up to2symbol signals/day, bookcapacity limits actual entries to1/day.

| Primary parameter/units | Default | Future coarse values |
|---|---:|---|
| opening_z_min /ratio |.50 |[.25,.50,.75] |
| opening_rvol_min /ratio |1.25 |[1.0,1.25,1.5] |
| stop_atr /A |1.0 |[.8,1.0,1.2] |

FIXED:opening30, past60/min40 std, signal15:30, cutoff15:50, no target/trail,
latency90, chase.30A. Overnight gap is separate; C10:00/Cprev is NOT opening return.
Closing-flow reversal, late receipt and omitted final10minutes can eliminate edge.

## 8. Observable execution, costs and positions

### Forward execution

Completed trigger -> ENTRY_PENDING -> FIRST fresh quote in a SUBSEQUENT scan.
Cannot fill in the scan that creates pending state. Observation must be the
correct symbol, finite positive, aware source time, source<=receive<=decision,
source age<=15s. Unknown source semantics do not certify an executable quote.
This new research model has no completed/open/forming-candle execution fallback.

```text
entryMarket = current ask if valid bid/ask else current last
exitMarket  = current bid if valid bid/ask else current last
s = 2/10000
h = 0 with valid bid/ask, otherwise research half-spread proxy1/10000
E = entryMarket*(1+s+h)
X = exitMarket*(1-s-h)
qty = 100/E
netPnL = qty*(X-E)-entryFee-exitFee
netR = netPnL/(qty*(E-S_initial))
```
Default unverified fees0 are placeholders, not verified free trading. Persist fee
basis/verification; economic PASS requires verification or a justified conservative
fee bound. No duplicate spread charge when already buying ask/selling bid.
Gap through stop sells the worse current observed price; do not repair it to the
stop. Target gap likewise sells current price. Stop levels are conditions, not fills.

### Historical OHLCV proxy, not quote reconstruction

`executionModel=COMPLETED_CLOSE_OBSERVATION_PROXY_60S`. Historical closes provide
later observed last-trade proxies, not proof of executable prices at REST scans.

- Observation clock: minute completion. Trigger T can first fill proxy atT+60s.
- Never use the trigger close, or retrospectively its next minute's open.
- Approve only using that later price and references already completed then.
- Post-entry stop/target/trail checks use subsequent observations. No pre-entry
  high/low excursions. An OHLC candle touching stop AND target is diagnostic
  AMBIGUOUS_INTRABAR; it cannot justify an ideal intrabar fill. A separate adverse
  bound scenario assumes stop first and explicitly labels its execution model.
- A gap/missing minute blocks new entry; open positions become DATA_STALE, no
  fabricated price. Resume with the first subsequent actual observed proxy.
- Quote age/spread/subminute latency cannot be verified from OHLCV. The result
  must state this limitation and cannot establish forward deployability alone.

Historical times are logical model times, not actual REST receipt timestamps.
Store `runMode=historical_proxy`, `quoteAgeVerified=false`,
`executabilityConfirmed=false`, `simulatedLatencySeconds=60`; unavailable actual
source/receipt/entry latency fields remain null. Keep the raw bar label and logical
observation/decision times. Do not fabricate source timestamps from bar boundaries.

### Common guards/account contract

| Rule | Exact baseline |
|---|---|
| Latency | tau_exec-tau_trigger<=90s; receipt>signal decision; correct entry window |
| Chase | ActualE<=triggerClose+.30A; IR1 E>B, IR2 E>ORL |
| Risk | E>S; estimated roundtripCostUSD/(qty*(E-S))<=.20 |
| Cash | Separate10,000USD per NEW strategy book;100USD notional; fractional qty; no borrowing |
| Capacity |1open position/book; max3 across the3new books; no cash sharing with V3/Simple |
| Daily | First SETUP consumes one attempt per strategy*symbol*NYdate; max1completed trade |
| Cancel/reentry | CANCELLED/EXIT terminal for the day; no event substitution |
| Duplicate | (version,strategy,symbol,date,setupTime,triggerTime) persisted idempotency key |
| Same-scan conflicts | CandidateScore descending (IR3 Zopen), triggerTime, symbol; log CAPACITY skips |
| New entry cutoff | Strategy window and officialclose-10min, whichever is earlier |
| Forced exit |15:50 NY; earlyclose-10min for IR1/IR2; IR3 earlyclose disabled |
| Same-observation precedence | Hard stop, existing trail, target, forceddeadline, maxhold, stallpending; same observed market price |
| Outage | DATA_STALE, staleSince/count, no fill/cash change; persist/recover affected flags |
| Unresolved | Retain unresolved/open with PnL=null and report it; never silently drop failed liquidation |

Compute stops before updating a current observation's trailing state. Initialize
peak/trough with entry-time observable mark, not pre-entry candles or slipped fill.
MFE/MAE use post-entry marks; brief unobserved highs/lows remain unknown.
Scheduled exits use their known deadline as trigger time; price exits use first
actual detection/receipt time, not a guessed missed stop tick. Store exit latency
and exitAffectedByDataStale. Wide spreads must not block risk exits merely because
new-entry spread eligibility fails; use a valid current bid and record its cost.
No broker API/ARM/live account connection is part of this research contract.

## 9. State-machine pseudocode

Specification pseudocode, not runnable production code:

```python
for scan in causal_observation_stream:
    manage_open_books_with_fresh_quotes_first(scan)  # benchmarks not required to exit
    persist_stale_if_no_valid_price(scan)
    context = context_at_common_completed_watermark(scan)
    resolve_existing_pending_on_first_subsequent_observation(scan, context)
    # Newly generated signals below CANNOT fill above or elsewhere in this scan.
    for symbol in reviewed_top15_detail_queue(scan):
        ir1(symbol, context)
        ir2(symbol, context)
    ir3_clock_check(context, symbols=["SPY", "QQQ"])
    assign_capacity_in_fixed_order_and_log_every_rejected_signal()

# IR1
if IDLE and eligible and exact_impulse:
    freeze_impulse_and_pre_event_A(); consume_attempt(); SETUP
elif SETUP:
    if cancel_now: CANCELLED(reason)
    elif first_qualifying_completed_pullback: freeze_B_L_arm(); ARMED
    elif third_pullback_without_qualification: CANCELLED("PULLBACK_EXPIRED")
elif ARMED:
    if cancel_now_or_new_low: CANCELLED(reason)
    elif new_completed_1m_after_arm and close>B and VA1>=1 and RS20A>=.25:
        TRIGGERED(close, stop=L_arm-.10*A, trigger_time=W)
    elif W>armTime+5minutes: CANCELLED("TRIGGER_EXPIRED")
# Pending: latest context, current execution quote, chase/risk/window/capacity.
# Managing: observed stop,1R activation/1A trail,stall20/max45/cutoff.

# IR2; cancellation uses ALL completed1m lows, not just5m close.
if IDLE and eligible_OR_setup: freeze_OR_A(); consume_attempt(); SETUP
elif SETUP:
    if cancel_now: CANCELLED(reason)
    elif later_completed_5m_breaks_from_inside: freeze_failure_low_time(); ARMED
elif ARMED:
    if any_post_break_low<failure_low or cancel_now: CANCELLED(reason)
    elif new_completed_5m_AFTER_break:
        reclaim_count +=1
        if reclaim_count<=2 and close5>ORL+.10*A and RECOVERY:
            TRIGGERED(close5,stop=failure_low-.10*A,target=ORM,trigger_time=W)
        elif reclaim_count>=2: CANCELLED("RECLAIM_EXPIRED")
# Entry needs nettargetspace>=1R; managing stop/target/max30/cutoff.

# IR3; once-per-day15:30 check, no moving late-session search.
if first_receipt_of_completed_1530_snapshot and not checked_today:
    checked_today=True
    for ETF in ["SPY","QQQ"]:
        if fullsession and validhistory and Zopen>=.50 and RVOLopen>=1.25 \
           and ownUP and not otherDOWN and NORMAL_or_HIGH:
            freeze_A_1525(); consume_attempt()
            log_atomic_SETUP_ARMED_TRIGGERED(signal_close=close1530)
        else: log_NO_TRADE(exact_reason)
# First subsequent quote by15:31:30, updated context; stop E-A or15:50 exit.
```

IR3 failed clock check consumes the clock opportunity even if no SETUP was formed.
It cannot be retried with later prices. IR1/IR2 cancellation wins simultaneous
trigger events. Capacity failure remains a logged first-attempt failure.

## 10. Audit output, metrics and folds

Each decision: version/model/symbol/date/strategy, availability/source/bar/receive/
decision times, candidate rank, state transitions, frozen boundaries, market/sector
features, reason. Each trade: setup/arm/trigger/entry/exit/source/receipt times,
latencies, market/fill prices, quantity, fees/spread/slippage, initial/effective stop,
target, regime/benchmark identity, peak/trough/time, stale flags, manifest/code hashes.

```text
tradeReturn = X/E-1 - (entryFee+exitFee)/(qty*E)
winRate = count(netR>0)/completed_resolved_count
avgWinR = mean(netR>0); avgLossR = mean(abs(netR<0))
expectancyR = mean(all netR)  # include breakeven
PF_R = sum(positive netR)/abs(sum(negative netR))
PF_USD = sum(positive netPnL)/abs(sum(negative netPnL))
MFE_R = max(0,(max_post_entry_observed_mark-E)/R)
MAE_R = min(0,(min_post_entry_observed_mark-E)/R)
maxDD_R = max_t(max_{s<=t}(cumulativeR_s)-cumulativeR_t), initial0 included
entryToMFE/MAE = first extreme observation_time-entryDecisionTime
```
No losses plus positive profits: PF=null, profitFactorInfinite=true. All zero/empty:
PF=null/undefined. No nonfinite JSON; infinite PF is not evidence of robustness.
Breakeven means expectancy=pWin*avgWin-pLoss*avgLoss, not automatically
pWin*avgWin-(1-pWin)*avgLoss. Unavailable subset averages remain null.
Drawdown follows exit chronology, batch-summing tied timestamps.
Daily book mean/std*sqrt(252) is Sharpe-like only; include zero-trade days and
return null for zero std. Pooled unit-R curves are NOT cash equity/portfolio returns.

Report by strategy/symbol/year/month/regime/session/fold and entry-time buckets
09:30–10,10–11,11–12,12–14,14–15,15–16 NY. Record resolved clean/affected/unresolved
counts and total exposure. Do not remove stale/gap trades to improve headline metrics.
Keep sampled MFE/MAE separate from optional OHLC range diagnostics.

Rolling folds:12month train, next3 validation, next3OOS,3monthstep, minimum3OOS folds.
All symbols share session-boundary splits, one full-session embargo before OOS,
no carried positions. Past warmup allowed; future not. Three folds require roughly
24months PLUS warmup/embargo, not merely18. Frozen train metrics are descriptive.
An earlier OOS may become later descriptive past train; changing rules after
viewing it makes a new research version requiring future untouched evaluation.
Insufficient history/trades is INCONCLUSIVE/NOT_EVALUABLE, not invented robustness.
Acceptance/one-factor stresses are specified in the research document.
Report R and actual cash PnL together: positive unit-R expectancy alone cannot
establish positive PnL under fixed-dollar, variable-stop sizing. Unresolved loss
exposure prevents an unqualified PASS. Annual concentration cannot be assessed
as robust diversification from a single year or a short9-month pooled OOS.

## 11. Freeze and remaining prerequisites

Event equations/defaults/chronology are fixed for this first implementation
proposal. No extra indicator/threshold search. Historical close proxy is not proof
of forward quote execution. Primary-text/bibliography review, actual timestamp
semantics, adjustment and point-in-time eligibility, metadata, fee bounds and
complete chronological data remain required before an economic PASS.

Implementation order: causal shared helpers -> IR3 -> IR2 -> IR1 -> frozen
chronological baseline -> limited stresses -> isolated forward paper.
IR1 and IR3 may share market momentum/beta exposure despite different triggers.
Report day/exposure overlap; do not call them independent portfolio alpha.
