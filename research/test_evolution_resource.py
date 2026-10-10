import unittest
from types import SimpleNamespace

from research.evolution.resource import decide_population


class AdaptiveResourceTests(unittest.TestCase):
    def cfg(self):
        return SimpleNamespace(
            min_population=20,
            max_population=500,
            scale_up_factor=1.25,
            scale_down_factor=0.75,
            memory_soft_limit_pct=70.0,
            memory_hard_limit_pct=85.0,
            swap_soft_limit_pct=5.0,
            swap_hard_limit_pct=20.0,
            cpu_soft_limit_pct=85.0,
        )

    def test_scales_up_gradually_when_resources_are_free(self):
        d = decide_population(20, {"memoryPct": 40, "swapPct": 0, "cpuPct": 30}, self.cfg(), enabled=True)
        self.assertEqual(d["resourceDecision"], "SCALE_UP")
        self.assertEqual(d["nextPopulation"], 25)

    def test_soft_pressure_holds_population(self):
        for sample in (
            {"memoryPct": 72, "swapPct": 0, "cpuPct": 30},
            {"memoryPct": 50, "swapPct": 6, "cpuPct": 30},
            {"memoryPct": 50, "swapPct": 0, "cpuPct": 90},
        ):
            d = decide_population(100, sample, self.cfg(), enabled=True)
            self.assertEqual(d["resourceDecision"], "HOLD")
            self.assertEqual(d["nextPopulation"], 100)

    def test_hard_pressure_scales_down_but_never_below_minimum(self):
        d = decide_population(100, {"memoryPct": 90, "swapPct": 0, "cpuPct": 20}, self.cfg(), enabled=True)
        self.assertEqual(d["resourceDecision"], "SCALE_DOWN")
        self.assertEqual(d["nextPopulation"], 75)
        d = decide_population(20, {"memoryPct": 99, "swapPct": 50, "cpuPct": 100}, self.cfg(), enabled=True)
        self.assertEqual(d["nextPopulation"], 20)

    def test_maximum_and_disabled_modes_hold(self):
        d = decide_population(500, {"memoryPct": 20, "swapPct": 0, "cpuPct": 10}, self.cfg(), enabled=True)
        self.assertEqual(d["resourceDecision"], "HOLD_MAX")
        self.assertEqual(d["nextPopulation"], 500)
        d = decide_population(37, {"memoryPct": 99, "swapPct": 99, "cpuPct": 99}, self.cfg(), enabled=False)
        self.assertEqual(d["resourceDecision"], "HOLD_DISABLED")
        self.assertEqual(d["nextPopulation"], 37)


if __name__ == "__main__":
    unittest.main()
