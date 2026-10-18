"""Offline-only hourly-budget approval tests: no AWS calls or spending."""
import unittest
from research.spot import v083_spot_lifecycle as spot
from research.test_v083_spot_lifecycle import sample


class SpotHourlyApprovalTests(unittest.TestCase):
    def test_approved_eight_hour_scenario(self):
        cfg = sample()
        result = spot.cost_breakdown(spot.validate_config(cfg))
        self.assertAlmostEqual(result["compute_usd"], 1.60)
        self.assertAlmostEqual(result["estimated_total_usd"], 2.00)
        self.assertAlmostEqual(result["approved_envelope_total_usd"], 2.00)
        self.assertEqual(result["max_approved_spot_bid_usd_hour"], 0.20)
        self.assertFalse(result["hard_invoice_cap"])

    def test_free_plan_spot_instance_passes_without_relaxing_limits(self):
        cfg = sample()
        cfg["instance_type"] = "m7i-flex.large"
        cfg["max_spot_price_usd_hour"] = 0.08
        cfg["max_compute_usd"] = 0.64
        loaded = spot.validate_config(cfg)
        self.assertEqual(spot.ALLOWED_TYPES["m7i-flex.large"], 2)
        self.assertLessEqual(spot.cost_breakdown(loaded)["estimated_total_usd"], 2.0)

    def test_spot_bid_over_020_fails_even_if_below_025(self):
        cfg = sample()
        cfg["max_spot_price_usd_hour"] = 0.21
        cfg["max_compute_usd"] = 1.68
        with self.assertRaisesRegex(ValueError, "BID_EXCEEDS_APPROVED"):
            spot.validate_config(cfg)
        with self.assertRaisesRegex(ValueError, "BID_EXCEEDS_APPROVED"):
            spot.issue_approval(cfg)

    def test_noncompute_estimate_exceeds_hourly_allowance(self):
        cfg = sample()
        cfg["cost_estimate"]["other_usd"] = 0.07
        with self.assertRaisesRegex(ValueError, "HOURLY_TOTAL_ESTIMATE"):
            spot.validate_config(cfg)

    def test_oversized_total_budget_cannot_be_authorized(self):
        cfg = sample()
        cfg["max_total_usd"] = 2.01
        with self.assertRaisesRegex(ValueError, "BUDGET_EXCEEDS_APPROVED"):
            spot.validate_config(cfg)

    def test_shorter_job_keeps_conservative_overhead_review(self):
        cfg = sample()
        cfg["max_hours"] = 4
        cfg["max_total_usd"] = 1
        with self.assertRaisesRegex(ValueError, "HOURLY_TOTAL_ESTIMATE"):
            spot.validate_config(cfg)

    def test_missing_extra_cost_estimate_is_blocked(self):
        cfg = sample()
        del cfg["cost_estimate"]["s3_usd"]
        with self.assertRaisesRegex(ValueError, "ADDITIONAL_COST"):
            spot.validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
