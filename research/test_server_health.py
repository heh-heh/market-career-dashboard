"""Offline tests for authenticated server-health helper; no EC2 calls."""
import unittest
from unittest.mock import patch
import server_health


class ServerHealthTests(unittest.TestCase):
    def test_mounts_cover_root_and_research_not_tmpfs(self):
        result = ("Filesystem Type 1B-blocks Used Available Use% Mounted on\n"
                  "/dev/nvme0n1p1 ext4 100000000000 60000000000 40000000000 60% /\n"
                  "/dev/nvme1n1 xfs 500000000000 100000000000 400000000000 20% /mnt/research\n"
                  "tmpfs tmpfs 10000 20 9980 1% /run\n")
        with patch.object(server_health, "_run", return_value=result):
            rows = server_health._mounts()
        self.assertEqual([x["mount"] for x in rows], ["/", "/mnt/research"])
        self.assertEqual(rows[1]["totalBytes"], 500000000000)
        self.assertEqual(rows[1]["usedPct"], 20.0)

    def test_devices_include_unmounted(self):
        result = ('NAME="nvme0n1" SIZE="100000000000" TYPE="disk" FSTYPE="" MOUNTPOINT=""\n'
                  'NAME="nvme0n1p1" SIZE="100000000000" TYPE="part" FSTYPE="ext4" MOUNTPOINT="/"\n'
                  'NAME="nvme1n1" SIZE="500000000000" TYPE="disk" FSTYPE="" MOUNTPOINT=""\n'
                  'NAME="loop0" SIZE="1024" TYPE="loop" FSTYPE="squashfs" MOUNTPOINT="/snap/foo"\n')
        with patch.object(server_health, "_run", return_value=result):
            rows = server_health._block_devices()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2]["name"], "nvme1n1")
        self.assertEqual(rows[2]["mount"], "")

    def test_cpu_pct_bounded(self):
        with patch.object(server_health, "_cpu_times",
                          side_effect=[(1000, 800), (1200, 850)]), \
             patch.object(server_health.time, "sleep") as sleep:
            self.assertEqual(server_health._cpu_pct(), 75.0)
            sleep.assert_called_once_with(0.15)

    def test_snapshot_is_readonly_and_does_not_return_secrets(self):
        mounts = [{"mount": "/mnt/research", "device": "/dev/nvme1n1", "type": "ext4",
                   "usedPct": 86.0, "totalBytes": 100, "usedBytes": 86,
                   "availableBytes": 14}]
        memory = {"usedPct": 91.0, "totalBytes": 100, "usedBytes": 91,
                  "availableBytes": 9, "swapUsedPct": None, "swapTotalBytes": 0,
                  "swapUsedBytes": 0}
        services = [{"name": server_health.SERVICE_NAMES[0], "state": "active"}]
        with patch.object(server_health, "_cpu_pct", return_value=92.0), \
             patch.object(server_health, "_meminfo", return_value=memory), \
             patch.object(server_health, "_mounts", return_value=mounts), \
             patch.object(server_health, "_block_devices", return_value=[]), \
             patch.object(server_health, "_services", return_value=services), \
             patch.object(server_health, "_network", return_value={"receivedBytes": 10, "sentBytes": 20}), \
             patch.object(server_health.os, "getloadavg", return_value=(0.5, 0.7, 0.8)), \
             patch.object(server_health.os, "cpu_count", return_value=2), \
             patch.object(server_health.Path, "read_text", return_value="12345.5 0"):
            data = server_health.snapshot()
        self.assertEqual(data["cpu"]["usedPct"], 92.0)
        self.assertEqual(data["uptimeSeconds"], 12345)
        self.assertIn("DISK_PRESSURE", data["warnings"])
        self.assertIn("MEMORY_PRESSURE", data["warnings"])
        self.assertIn("HIGH_CPU_SAMPLE", data["warnings"])
        self.assertNotIn("secret", str(data).lower())

    def test_partial_metrics_do_not_raise(self):
        with patch.object(server_health, "_cpu_pct", side_effect=OSError("no stat")), \
             patch.object(server_health, "_meminfo", side_effect=OSError("no memory")), \
             patch.object(server_health, "_mounts", return_value=[]), \
             patch.object(server_health, "_block_devices", return_value=[]), \
             patch.object(server_health, "_services", return_value=[]), \
             patch.object(server_health, "_network", return_value={"receivedBytes": None, "sentBytes": None}), \
             patch.object(server_health.os, "getloadavg", side_effect=OSError("no load")), \
             patch.object(server_health.Path, "read_text", side_effect=OSError("no uptime")):
            data = server_health.snapshot()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["cpu"]["usedPct"])
        self.assertGreaterEqual(len(data["unavailable"]), 2)

    def test_service_command_is_fixed_allowlist(self):
        self.assertEqual(len(server_health.SERVICE_NAMES), 5)
        self.assertFalse(any("bash" in x for x in server_health.SERVICE_NAMES))


if __name__ == "__main__":
    unittest.main()
