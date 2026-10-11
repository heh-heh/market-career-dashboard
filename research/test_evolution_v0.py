"""Small deterministic fixtures, explicitly synthetic. No real data/broker/network."""
import ast
import contextlib
import copy
import csv
from dataclasses import replace
from datetime import datetime
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from research.backtest_v4_strategies import Bar, MINUTE, NY, exchange_sessions
from research.evolution.account import Account, ExecutionCostModel
from research.evolution.config import Config
from research.evolution.features import MinuteFeatureProvider, POLICY_FEATURES, POLICY_BASE, benchmark_for_symbol
from research.evolution.genome import Genome, random_genome, mutate, crossover
from research.evolution.engine import evaluate_day
from research.evolution.population import Bot, aggregate_window_evaluations, daily_evaluation, initial_population, reproduce
from research.evolution.run_evolution_v0 import main, population_hash, parser, prepare_inputs
from research.evolution.monitor import status

ROOT = Path(__file__).resolve().parents[1]


def fixture(day="2024-06-03", *, count=390, rising=True):
    opening, closing = exchange_sessions(day, day)[day]
    rows = {}
    for s, base, drift in (("SPY", 400, .00001), ("QQQ", 350, .00002), ("SOXX", 220, .00003),
                           ("TQQQ", 60, .00012), ("AAPL", 180, .00008)):
        minutes = {}
        for i in range(min(count, int((closing-opening).total_seconds()/60))):
            t = opening+i*MINUTE
            c = base*(1+(1 if rising else -1)*drift*i+.0001*((i % 7)-3))
            minutes[t] = Bar(t, t+MINUTE, c, c*1.0002, c*.9998, c, 1000+i % 11)
        rows[s] = minutes
    return rows, opening, closing


def buy_genome(config=None):
    config = config or Config()
    w = [0.0]*len(POLICY_FEATURES)
    w[POLICY_FEATURES.index("available_cash")] = 2
    return Genome(tuple(w), .5, -2, .01, .04, 20, .5).validate(config)


def signal(t):
    return dict(signalTimestamp=t.isoformat(), sourceCandle={"end": t.isoformat()}, features={}, score=2, benchmark="QQQ")


class FeatureTests(unittest.TestCase):
    def test_future_mutation_cannot_change_past_features(self):
        rows, o, c = fixture(count=65)
        other = copy.deepcopy(rows)
        for s in other:
            for t, b in list(other[s].items()):
                if t >= o+50*MINUTE:
                    other[s][t] = replace(b, o=b.o*2, h=b.h*2, l=b.l*2, c=b.c*2, v=b.v*20)
        a = MinuteFeatureProvider(Config()).prepare(rows, o, c, ["TQQQ"])
        b = MinuteFeatureProvider(Config()).prepare(other, o, c, ["TQQQ"])
        np.testing.assert_array_equal(a.matrix[:50], b.matrix[:50])
        np.testing.assert_array_equal(a.valid[:50], b.valid[:50])

    def test_completed_end_timestamp_and_no_future_benchmark_fill(self):
        rows, o, c = fixture(count=65)
        t = o+40*MINUTE
        del rows["QQQ"][t-MINUTE]
        f = MinuteFeatureProvider(Config()).prepare(rows, o, c, ["TQQQ"])
        self.assertFalse(f.valid[39, 0])
        self.assertNotIn(("QQQ", t), f.raw)
        self.assertEqual(f.times[0], o+MINUTE)
        self.assertEqual(f.bars["TQQQ"][o+MINUTE].start, o)

    def test_volume_mean_excludes_current_and_return_exact_horizon(self):
        rows, o, c = fixture(count=50)
        t = o+40*MINUTE
        f = MinuteFeatureProvider(Config()).prepare(rows, o, c, ["TQQQ"])
        x = f.raw["TQQQ", t]
        expected = np.mean([rows["TQQQ"][o+i*MINUTE].v for i in range(19, 39)])
        self.assertEqual(x["rolling_volume_mean"], expected)
        self.assertAlmostEqual(x["return_5m"], rows["TQQQ"][o+39*MINUTE].c/rows["TQQQ"][o+34*MINUTE].c-1)
        self.assertEqual(x["momentum_5m"], x["return_5m"])
        self.assertFalse(f.matrix.flags.writeable)

    def test_gap_no_forward_fill_and_prefix_vwap_explicitly_invalid(self):
        rows, o, c = fixture(count=70)
        del rows["TQQQ"][o+40*MINUTE]
        f = MinuteFeatureProvider(Config()).prepare(rows, o, c, ["TQQQ"])
        self.assertTrue(f.valid[39, 0])
        self.assertFalse(f.valid[40:].any())
        self.assertNotIn(o+41*MINUTE, f.bars["TQQQ"])

    def test_est_edt_and_early_close(self):
        for d, offset, n in (("2024-03-08", -5, 390), ("2024-03-11", -4, 390), ("2024-11-29", -5, 210)):
            rows, o, c = fixture(d)
            f = MinuteFeatureProvider(Config()).prepare(rows, o, c, ["TQQQ"])
            self.assertEqual(o.utcoffset().total_seconds()/3600, offset)
            self.assertEqual(f.duration, n)
            self.assertEqual(f.times[-1], c)

    def test_all_universe_benchmark_mappings_explicit(self):
        for s in json.loads((ROOT/"research/universe_v3.json").read_text())["symbols"]:
            self.assertTrue(benchmark_for_symbol(s)[0])
        with self.assertRaises(ValueError): benchmark_for_symbol("UNKNOWN")


class GenomeTests(unittest.TestCase):
    def test_json_roundtrip_and_hash(self):
        g = random_genome(random.Random(42), Config())
        self.assertEqual(g, Genome.from_dict(json.loads(json.dumps(g.to_dict()))))
        self.assertEqual(len(g.digest), 64)

    def test_mutation_bounds_parent_immutable(self):
        c = Config(mutation_probability=1, mutation_strength=2)
        parent = buy_genome(c)
        original = parent.to_dict()
        r = random.Random(9)
        for _ in range(100):
            child, changes = mutate(parent, r, c)
            child.validate(c)
            self.assertGreater(changes, 0)
        self.assertEqual(parent.to_dict(), original)

    def test_uniform_crossover_and_seed_determinism(self):
        c = Config()
        a, b = random_genome(random.Random(1), c), random_genome(random.Random(2), c)
        x = crossover(a, b, random.Random(3)).validate(c)
        self.assertTrue(all(v in (p, q) for v, p, q in zip(x.weights, a.weights, b.weights)))
        self.assertEqual(x, crossover(a, b, random.Random(3)))
        self.assertEqual(initial_population(c, random.Random(42), "2024-06-03"), initial_population(c, random.Random(42), "2024-06-03"))

    def test_invalid_config_and_gene_fail_closed(self):
        for kw in ({"population_size": 0}, {"initial_capital": -1}, {"mutation_probability": 2}, {"slippage_bps": 10000}):
            with self.assertRaises(ValueError): Config(**kw)
        with self.assertRaises(ValueError): replace(buy_genome(), position_fraction=2).validate(Config())


class AccountTests(unittest.TestCase):
    def test_flat_price_mfe_zero_mae_and_cost_loss(self):
        a = Account(100, ExecutionCostModel())
        t = datetime(2024, 6, 3, 10, tzinfo=NY)
        a.buy("AAPL", 180, t, 1, signal(t-MINUTE), buy_genome())
        trade = a.sell(180, t+MINUTE, "TEST", t.isoformat())
        self.assertEqual(trade["mfePct"], 0)
        self.assertLess(trade["maePct"], 0)
        self.assertEqual(trade["grossPnlUsd"], 0)
        self.assertLess(trade["pnlUsd"], 0)

    def test_fractional_cash_cost_and_both_pnl_forms(self):
        a = Account(100, ExecutionCostModel())
        t = datetime(2024, 6, 3, 10, tzinfo=NY)
        self.assertEqual(a.cash, 100)
        a.buy("AAPL", 180, t, 1, signal(t-MINUTE), buy_genome())
        self.assertLess(a.position["quantity"], 1)
        self.assertAlmostEqual(a.cash, 0)
        self.assertLess(a.equity, 100)
        a.mark(182, t+MINUTE)
        self.assertGreater(a.unrealized_pnl, 0)
        trade = a.sell(180, t+2*MINUTE, "TEST", (t+MINUTE).isoformat())
        self.assertLess(trade["pnlUsd"], 0)
        self.assertAlmostEqual(a.cash-100, a.realized_pnl)
        self.assertAlmostEqual(-a.realized_pnl, a.transaction_cost)
        self.assertAlmostEqual(trade["costUsd"], a.transaction_cost)

    def test_account_states_isolated_and_no_leverage(self):
        a, b = Account(100, ExecutionCostModel()), Account(100, ExecutionCostModel())
        t = datetime(2024, 6, 3, 10, tzinfo=NY)
        a.buy("AAPL", 180, t, .5, signal(t-MINUTE), buy_genome())
        self.assertEqual(b.cash, 100)
        self.assertIsNone(b.position)
        with self.assertRaises(ValueError): a.buy("QQQ", 350, t, 1, signal(t), buy_genome())
        with self.assertRaises(ValueError): b.buy("QQQ", 350, t, 2, signal(t), buy_genome())


class ExecutionTests(unittest.TestCase):
    def run_day(self, rows=None, count=390):
        data, o, c = fixture(count=count)
        if rows is not None: data = rows(data, o)
        cfg = Config(population_size=1)
        bots = [Bot("test", buy_genome(cfg), 0, [], "2024-06-03")]
        accounts, diag = evaluate_day(bots, data, o, c, ["TQQQ"], cfg)
        return bots, accounts[0], diag

    def test_entry_is_subsequent_observable_close_not_signal_or_open(self):
        def change(rows, o):
            t = o+31*MINUTE  # first signal at 10:01; next completed close at 10:02
            b = rows["TQQQ"][t]
            rows["TQQQ"][t] = replace(b, o=50, l=49, h=70, c=65)
            return rows
        _, a, _ = self.run_day(change)
        p = a.trades[0]
        self.assertEqual(p["entryMarketPrice"], 65)
        self.assertEqual(datetime.fromisoformat(p["entryTimestamp"])-datetime.fromisoformat(p["signalTimestamp"]), MINUTE)
        self.assertAlmostEqual(p["entryFillPrice"], 65*1.0003)

    def test_missing_next_observation_cancels_entry(self):
        def change(rows, o):
            del rows["TQQQ"][o+31*MINUTE]
            return rows
        _, a, _ = self.run_day(change)
        self.assertEqual(a.cancellations["MISSING_OR_LATE_ENTRY_OBSERVATION"], 1)
        self.assertFalse(a.trades)

    def test_future_high_low_do_not_trigger_same_entry_bar_exit(self):
        def change(rows, o):
            t = o+31*MINUTE
            rows["TQQQ"][t] = replace(rows["TQQQ"][t], h=10000, l=.01)
            return rows
        _, a, _ = self.run_day(change)
        self.assertEqual(a.trades[0]["exitReason"], "MAX_HOLD")
        self.assertGreater(a.trades[0]["exitTimestamp"], a.trades[0]["entryTimestamp"])

    def test_gap_through_stop_uses_worse_observed_close(self):
        def change(rows, o):
            for i, mark in ((32, 55), (33, 50)):
                t = o+i*MINUTE
                rows["TQQQ"][t] = replace(rows["TQQQ"][t], o=mark, h=mark, l=mark, c=mark)
            return rows
        _, a, _ = self.run_day(change)
        p = a.trades[0]
        self.assertEqual(p["exitReason"], "STOP_LOSS")
        self.assertEqual(p["exitMarketPrice"], 50)
        self.assertLess(p["exitFillPrice"], p["stopPrice"])

    def test_incomplete_session_cannot_fabricate_exit_or_parent(self):
        bots, a, _ = self.run_day(count=35)
        e = daily_evaluation(bots[0], a, "2024-06-03", Config())
        self.assertIsNone(e["dailyPnl"])
        self.assertFalse(e["reproductionEligible"])
        self.assertEqual(e["survival"], "INVALID")
        self.assertIsNotNone(e["unresolvedPosition"])

    def test_complete_session_no_overnight_and_cutoff(self):
        _, a, _ = self.run_day()
        self.assertIsNone(a.position)
        self.assertTrue(all(datetime.fromisoformat(t["exitTimestamp"]).hour < 16 for t in a.trades))


class EvolutionTests(unittest.TestCase):
    def test_survival_epsilon_and_daily_vs_lifetime(self):
        c = Config()
        b = initial_population(c, random.Random(1), "2024-06-03")[0]
        for delta, expected in ((-1, "DEAD"), (0, "NEUTRAL"), (1e-9, "NEUTRAL"), (1, "SURVIVE")):
            a = Account(100, ExecutionCostModel()); a.cash += delta
            e = daily_evaluation(b, a, "2024-06-03", c)
            self.assertEqual(e["survival"], expected)
            self.assertEqual(e["reproductionEligible"], expected == "SURVIVE")
        self.assertAlmostEqual(b.lifetime_equity, 100+1e-9)
        self.assertEqual(Account(100, ExecutionCostModel()).cash, 100)

    def test_seven_session_window_does_not_kill_on_one_bad_day(self):
        c = Config(population_size=2, evaluation_window_sessions=7)
        bots = initial_population(c, random.Random(7), "2024-06-03")
        batches = []
        # Bot 0 loses day one but finishes the seven-session window positive.
        # Bot 1 finishes the whole window negative.
        p0 = [-1.0, .4, .4, .4, .4, .4, .4]
        p1 = [-.2, -.2, -.2, -.2, -.2, -.2, -.2]
        for i, (x, y) in enumerate(zip(p0, p1), start=1):
            batch = []
            for bot, pnl in zip(bots, (x, y)):
                a = Account(100, ExecutionCostModel())
                a.cash += pnl
                batch.append(daily_evaluation(
                    bot, a, f"2024-06-{i+2:02d}", c,
                    update_lifetime=False, mark_death=False,
                ))
            batches.append(batch)
        partial = aggregate_window_evaluations(bots, batches[:1], c, complete=False, mark_death=False)
        self.assertEqual(partial[0]["survival"], "DEAD")
        self.assertFalse(partial[0]["reproductionEligible"])
        self.assertIsNone(bots[0].death_date)

        final = aggregate_window_evaluations(bots, batches, c, complete=True, mark_death=True)
        self.assertEqual(final[0]["survival"], "SURVIVE")
        self.assertTrue(final[0]["reproductionEligible"])
        self.assertAlmostEqual(final[0]["windowPnl"], 1.4)
        self.assertIsNone(bots[0].death_date)
        self.assertEqual(final[1]["survival"], "DEAD")
        self.assertFalse(final[1]["reproductionEligible"])
        self.assertIsNotNone(bots[1].death_date)

    def test_v04_window_fitness_penalizes_overtrading_and_instability(self):
        c = Config(
            population_size=2, fitness_mode="v04_generalization",
            transaction_cost_penalty=1.0, excessive_trading_penalty=.0015,
            free_trade_count=2, losing_session_penalty=.01,
            return_volatility_penalty=.25, evaluation_window_sessions=7,
        )
        bots = initial_population(c, random.Random(4), "2024-06-03")
        def row(bot_id, ret, trades, cost):
            return dict(
                botId=bot_id, date="2024-06-03", survival="SURVIVE" if ret>0 else "DEAD",
                reproductionEligible=ret>0, evaluationValid=True,
                dailyPnl=ret*100, dailyReturn=ret, fitness=ret,
                maxDrawdown=.005, trades=trades, transactionCost=cost, turnover=10,
            )
        low=[]; high=[]
        low_returns=[.004,.003,-.001,.003,.002,-.001,.003]
        high_returns=[.014,-.010,.013,-.009,.012,-.008,.010]
        for lr,hr in zip(low_returns,high_returns):
            low.append(row(bots[0].bot_id,lr,1,.03))
            high.append(row(bots[1].bot_id,hr,8,.20))
        batches=[[low[i],high[i]] for i in range(7)]
        out=aggregate_window_evaluations(bots,batches,c,complete=True,mark_death=False)
        by={x["botId"]:x for x in out}
        self.assertGreater(by[bots[0].bot_id]["fitness"],by[bots[1].bot_id]["fitness"])
        self.assertEqual(by[bots[0].bot_id]["fitnessMode"],"v04_generalization")
        self.assertGreater(by[bots[1].bot_id]["tradesPerSession"],2)
        self.assertIsNotNone(by[bots[1].bot_id]["fitnessComponents"])

    def test_elite_preservation_and_dead_parent_exclusion(self):
        c = Config(population_size=20, elite_count=2, parent_fraction=1)
        bots = initial_population(c, random.Random(1), "2024-06-03")
        evaluations = []
        for i, b in enumerate(bots):
            a = Account(100, ExecutionCostModel()); a.cash += 2-i
            evaluations.append(daily_evaluation(b, a, "2024-06-03", c))
        original = [b.genome.digest for b in bots]
        children, m = reproduce(bots, evaluations, c, random.Random(2), 1, "2024-06-03")
        self.assertEqual(len(children), 20)
        self.assertEqual(m["parentIds"], [bots[0].bot_id, bots[1].bot_id])
        self.assertEqual(children[0].genome, bots[0].genome)
        self.assertEqual(children[0].lifetime_equity, 102)
        self.assertEqual(children[0].parent_ids, [bots[0].bot_id])
        self.assertTrue(all(set(x.parent_ids) <= set(m["parentIds"]) for x in children))
        self.assertEqual(original, [b.genome.digest for b in bots])

    def test_no_survivors_random_recovery_not_dead_copy(self):
        c = Config(population_size=20)
        bots = initial_population(c, random.Random(1), "2024-06-03")
        e = []
        for b in bots:
            a = Account(100, ExecutionCostModel()); a.cash -= 1
            e.append(daily_evaluation(b, a, "2024-06-03", c))
        children, m = reproduce(bots, e, c, random.Random(2), 1, "2024-06-03")
        self.assertTrue(m["randomRecovery"])
        self.assertTrue(all(not b.parent_ids and b.origin == "RANDOM_RECOVERY" for b in children))

    def test_fitness_equation(self):
        c = Config(free_trade_count=0)
        b = initial_population(c, random.Random(1), "2024-06-03")[0]
        a = Account(100, ExecutionCostModel()); a.cash = 102; a.max_drawdown = .02; a.transaction_cost = .10
        e = daily_evaluation(b, a, "2024-06-03", c)
        self.assertAlmostEqual(e["fitness"], .02-.5*.02-.25*.10/100)


def write_fixtures(root, dates):
    files = {}
    for day in dates:
        rows, _, _ = fixture(day)
        for s, minutes in rows.items():
            files.setdefault(s, []).extend(minutes.values())
    root.mkdir(parents=True, exist_ok=True)
    for s, bars in files.items():
        with (root/(s+".csv")).open("w", newline="") as f:
            w = csv.writer(f); w.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
            for b in bars: w.writerow([b.end.isoformat(), s, b.o, b.h, b.l, b.c, b.v])


class PipelineTests(unittest.TestCase):
    def test_multi_generation_artifacts_reproducibility_and_split_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root/"data"
            dates = list(exchange_sessions("2024-06-03", "2024-06-07"))
            write_fixtures(data, dates)
            c = Config(population_size=20, evaluation_window_sessions=1,
                       train=("2024-06-03", "2024-06-04"),
                       validation=("2024-06-05", "2024-06-05"), final_oos=("2024-06-06", "2024-06-07"))
            cfg = root/"config.json"; cfg.write_text(json.dumps(c.snapshot()))
            args = ["--data-dir", str(data), "--results-dir", str(root/"results"), "--symbols", "TQQQ,AAPL",
                    "--from-date", dates[0], "--to-date", dates[-1], "--config", str(cfg), "--provisional", "--smoke", "--data-origin", "synthetic"]
            before = {p.name: p.read_bytes() for p in data.iterdir()}
            with contextlib.redirect_stdout(io.StringIO()):
                p1, p2 = main(args), main(args)
            self.assertNotEqual(p1, p2)
            info = json.loads((p1/"run.json").read_text())
            self.assertEqual(info["splitDays"], {"train": 2, "validation": 1, "final_oos": 2})
            self.assertEqual(info["generation"], 2)  # frozen training offspring evaluated in OOS
            self.assertEqual(info["completedTrainGenerations"], 2)
            self.assertTrue(info["researchOnly"])
            self.assertIn("research/evolution/engine.py", info["codeHashes"])
            self.assertEqual(info["dataMode"], "PROVISIONAL_UNREVIEWED_DATA")
            self.assertEqual(info["dataOrigin"], "synthetic")
            for f in ("generations.jsonl", "final_population.json", "lineage.jsonl", "best_genomes.json"):
                self.assertEqual((p1/f).read_bytes(), (p2/f).read_bytes())
            frozen = json.loads((p1/"frozen_population.json").read_text())["population"]
            self.assertEqual(frozen, json.loads((p1/"final_population.json").read_text()))
            self.assertEqual(len(list(p1.glob("generation_*"))), 2)
            self.assertTrue((p1/"generation_0000_session_01/trades.csv").exists())
            self.assertEqual(before, {p.name: p.read_bytes() for p in data.iterdir()})
            monitor = status(root/"results")
            self.assertEqual(monitor["phase"], "completed")
            self.assertNotIn("inputManifest", monitor)

    def test_default_seven_session_generation_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root/"data"
            dates = list(exchange_sessions("2024-06-03", "2024-06-11"))
            self.assertEqual(len(dates), 7)
            write_fixtures(data, dates)
            c = Config(population_size=4, evaluation_window_sessions=7,
                       train=("2024-06-03", "2024-06-11"),
                       validation=("2024-06-12", "2024-06-12"),
                       final_oos=("2024-06-13", "2024-06-14"))
            cfg = root/"config.json"; cfg.write_text(json.dumps(c.snapshot()))
            args = ["--data-dir", str(data), "--results-dir", str(root/"results"),
                    "--symbols", "TQQQ,AAPL", "--from-date", dates[0], "--to-date", dates[-1],
                    "--config", str(cfg), "--provisional", "--data-origin", "synthetic"]
            with contextlib.redirect_stdout(io.StringIO()):
                out = main(args)
            info = json.loads((out/"run.json").read_text())
            state = json.loads((out/"status.json").read_text())
            self.assertEqual(info["completedTrainGenerations"], 1)
            self.assertEqual(info["nextPopulationGeneration"], 1)
            self.assertEqual(state["evaluationWindowSessions"], 7)
            self.assertEqual(state["windowSessionsCompleted"], 7)
            self.assertTrue(state["windowComplete"])
            self.assertEqual(len(list(out.glob("generation_0000_session_*"))), 7)
            self.assertTrue((out/"generation_0000_session_07/window_evaluations.json").exists())

    def test_missing_benchmark_and_unreviewed_data_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_fixtures(root, ["2024-06-03"])
            (root/"QQQ.csv").unlink()
            args = parser().parse_args(["--data-dir", str(root), "--symbols", "TQQQ", "--provisional"])
            with self.assertRaisesRegex(ValueError, "QQQ"): prepare_inputs(args, ["TQQQ"])
            args.provisional = False
            with self.assertRaisesRegex(ValueError, "data-manifest"): prepare_inputs(args, ["TQQQ"])

    def test_mutated_input_cannot_finalize_results(self):
        from research.evolution import run_evolution_v0 as runner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_fixtures(root/"data", ["2024-06-03"])
            real = runner.evaluate_day
            def changed(*args, **kwargs):
                result = real(*args, **kwargs)
                with (root/"data/TQQQ.csv").open("a") as f:
                    f.write("\n")
                return result
            argv = ["--data-dir", str(root/"data"), "--results-dir", str(root/"results"), "--population", "2",
                    "--symbols", "TQQQ", "--from-date", "2024-06-03", "--to-date", "2024-06-03", "--provisional"]
            with patch.object(runner, "evaluate_day", side_effect=changed), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): runner.main(argv)
            info = json.loads(next((root/"results").glob("*/run.json")).read_text())
            self.assertEqual(info["phase"], "error")
            self.assertFalse(info["resultsValid"])
            self.assertIn("hash changed", info["error"])

    def test_reader_rejects_naive_conflicting_duplicate_and_out_of_order(self):
        from research.backtest_intraday_v4 import read_stream
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); write_fixtures(root, ["2024-06-03"])
            path = root/"TQQQ.csv"
            original = path.read_text().splitlines()
            with self.assertRaisesRegex(ValueError, "out-of-order"):
                path.write_text("\n".join([original[0], original[2], original[1]])+"\n")
                list(read_stream(path, "TQQQ", {"timestamp_kind": "end", "naive_timezone": None}))
            with self.assertRaisesRegex(ValueError, "conflicting duplicate"):
                row = original[1].split(","); row[-1] = "999999"
                path.write_text("\n".join([original[0], original[1], ",".join(row)])+"\n")
                list(read_stream(path, "TQQQ", {"timestamp_kind": "end", "naive_timezone": None}))
            path.write_text("\n".join([original[0], original[1].replace("-04:00", "")])+"\n")
            with self.assertRaisesRegex(ValueError, "timezone-aware"):
                list(read_stream(path, "TQQQ", {"timestamp_kind": "end", "naive_timezone": None}))

    def test_real_public_status_endpoint_is_read_only(self):
        # Compile only this new GET branch, avoiding any initialization of live services.
        text = (ROOT/"server.py").read_text()
        start = text.index('        if path=="/api/research/evolution/status":')
        end = text.index('        if path=="/api/health":', start)
        body = "def do_get(self, path):\n"+"\n".join(line[4:] for line in text[start:end].splitlines())
        ns = {"Path": Path, "os": __import__("os"), "ROOT": ROOT}
        exec(compile(body, "server.py:EvolutionGet", "exec"), ns)
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"EVOLUTION_RESULTS_DIR": tmp}):
            replies = []
            handler = type("Handler", (), {"send_json": lambda self, data, *args: replies.append(data)})()
            ns["do_get"](handler, "/api/research/evolution/status")
            self.assertTrue(replies[0]["ok"])
            self.assertTrue(replies[0]["researchOnly"])
            self.assertFalse(replies[0]["available"])

    def test_end_only_and_no_implicit_long_run(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit): parser().parse_args(["--timestamp-kind", "start"])
            with self.assertRaises(SystemExit): main([])

    def test_results_cannot_be_written_into_raw_data(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stderr(io.StringIO()):
            root = Path(tmp)
            with self.assertRaises(SystemExit):
                main(["--smoke", "--data-dir", str(root), "--results-dir", str(root/"outputs"), "--provisional"])
            self.assertFalse((root/"outputs").exists())

    def test_research_modules_cannot_import_order_execution(self):
        for p in (ROOT/"research/evolution").glob("*.py"):
            source = p.read_text()
            self.assertNotIn("/api/v1/orders", source)
            for node in ast.walk(ast.parse(source)):
                names = [x.name for x in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                self.assertTrue(all(not n.startswith(("live_trader", "toss_auth", "trading", "server", "paper_trader")) for n in names))

    def test_monitor_interrupted_without_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"20240101-run"; p.mkdir()
            (p/"status.json").write_text(json.dumps({"phase": "running", "pid": 999999999, "researchOnly": True}))
            self.assertEqual(status(tmp)["phase"], "interrupted")


if __name__ == "__main__":
    unittest.main()
