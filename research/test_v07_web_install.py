"""Safety regressions for backup-first surgical V0.7 web-console installer."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_v07_web_console_overlay.py"
spec = importlib.util.spec_from_file_location("v07_web_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class V07WebInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / "server.py"
        self.original = (
            'def run_admin_console(command):\n'
            '    # Locally customized EC2 logic MUST remain byte-identical.\n'
            '    if parts==["pwd"]:\n'
            '        return 0, "local"\n'
            '    return 1, "forbidden"\n'
        )
        self.server.write_text(self.original)
        self.pt = patch.object(installer, "TARGET", self.root)
        self.pm = patch.object(installer, "payload", lambda: (b'"""safe"""\n', "pin"))
        self.pt.start()
        self.pm.start()
        self.addCleanup(self.pt.stop)
        self.addCleanup(self.pm.stop)

    def test_insertion_is_surgical_and_idempotent(self):
        files, _ = installer.plan()
        before, after = files["server.py"]
        self.assertEqual(before.decode(), self.original)
        self.assertEqual(after.count(installer.INSERT.encode()), 1)
        self.assertIn(b"Locally customized EC2 logic MUST remain byte-identical", after)
        self.server.write_bytes(after)
        again, _ = installer.plan()
        self.assertEqual(again["server.py"][0], again["server.py"][1])

    def test_unexpected_dispatch_blocks_instead_of_clobber(self):
        self.server.write_text(self.original.replace('    if parts==["pwd"]:\n',
                                                       '    if parts and parts[0]=="v07":\n'
                                                       '        return 2, "custom"\n'
                                                       '    if parts==["pwd"]:\n'))
        with self.assertRaisesRegex(installer.Blocked, "ANCHOR_MISMATCH"):
            installer.plan()

    def test_symlinked_module_blocks(self):
        (self.root / "v07_web_jobs.py").symlink_to(self.server)
        with self.assertRaisesRegex(installer.Blocked, "MODULE_PATH_UNSAFE"):
            installer.plan()


if __name__ == "__main__":
    unittest.main()
