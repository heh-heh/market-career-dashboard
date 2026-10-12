"""Offline guarded log-overlay tests. Never touches production services or data."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_collection_logging as mod


class LoggingInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'prod'
        self.source.mkdir()
        self.target.mkdir()
        for name in mod.KNOWN:
            if name == 'research/collection_activity.py':
                continue
            p = self.target / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self.fake_blob("OLD", name))
        (self.target / '.env').write_text('DO_NOT_CHANGE=secret')
        (self.target / 'data').mkdir()
        (self.target / 'data' / 'AAPL.sqlite3').write_bytes(b'KEEP_DATA')

    @staticmethod
    def fake_blob(version, path):
        return (('<p>' + version + '</p>\n') if path.endswith('.html')
                else ('# ' + version + '\nvalue = ' + ('1' if version == 'OLD' else '2') + '\n')).encode()

    def fake_git(self, source, *args):
        if args == ('rev-parse', 'HEAD'):
            return (mod.RELEASE + '\n').encode()
        if args[0] == 'show':
            ref, name = args[1].split(':', 1)
            return self.fake_blob('NEW' if ref == mod.RELEASE else 'OLD', name)
        raise AssertionError(args)

    def test_known_overlay_only_and_preserve_runtime(self):
        with patch.object(mod, 'git', side_effect=self.fake_git):
            payload, entries = mod.plan(self.source, self.target)
        self.assertEqual(len(entries), 6)
        self.assertEqual(len([x for x in entries if x['action']=='UPDATE']), 5)
        self.assertEqual(len([x for x in entries if x['action']=='CREATE']), 1)
        self.assertEqual(set(payload), set(mod.KNOWN))
        self.assertEqual((self.target/'data/AAPL.sqlite3').read_bytes(), b'KEEP_DATA')
        self.assertIn('secret', (self.target/'.env').read_text())

    def test_conflicting_operator_change_blocks(self):
        (self.target/'server.py').write_text('CUSTOM_LOCAL_CHANGE\n')
        with patch.object(mod, 'git', side_effect=self.fake_git):
            with self.assertRaisesRegex(mod.Blocked, 'LOCAL_CODE_DIFFERENT'):
                mod.plan(self.source, self.target)

    def test_symlink_is_blocked(self):
        p = self.target/'collection.html'
        p.unlink()
        p.symlink_to(self.target/'server.py')
        with patch.object(mod, 'git', side_effect=self.fake_git):
            with self.assertRaisesRegex(mod.Blocked, 'SYMLINK_BLOCKED'):
                mod.plan(self.source, self.target)

    def test_apply_and_verified_logging_endpoint_preserve_data(self):
        status = {'ok': True, 'finalOosGate': 'CLOSED',
                  'scheduler': {'mode': 'PAUSED', 'stale': False, 'collector': None,
                                'automatic': True}}
        calls = []
        with (patch.object(mod, 'git', side_effect=self.fake_git),
              patch.object(mod, 'BACKUPS', self.root/'backups'),
              patch.object(mod.os, 'geteuid', return_value=0),
              patch.object(mod, 'check_idle'),
              patch.object(mod, 'systemctl', side_effect=lambda *a: calls.append(a) or 'active'),
              patch.object(mod, 'json_url', side_effect=lambda u: {'ok': True, 'events': []}
                           if u == mod.LOGS else status)):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                mod.apply(self.source, self.target)
            result = json.loads(out.getvalue())
            self.assertEqual(result['phase'], 'COMMITTED')
            self.assertEqual(result['finalOosGate'], 'CLOSED')
            backup = Path(result['transaction'])
            self.assertEqual(json.loads((backup/'manifest.json').read_text())['phase'], 'COMMITTED')
            self.assertEqual((backup/'before/collection.html').read_bytes(), self.fake_blob('OLD','collection.html'))
            self.assertEqual((self.target/'collection.html').read_bytes(), self.fake_blob('NEW','collection.html'))
            self.assertEqual((self.target/'research/collection_activity.py').read_bytes(), self.fake_blob('NEW','research/collection_activity.py'))
            self.assertEqual((self.target/'data/AAPL.sqlite3').read_bytes(), b'KEEP_DATA')
            self.assertEqual((self.target/'.env').read_text(), 'DO_NOT_CHANGE=secret')
            self.assertEqual(calls[:2], [('restart', mod.SCHEDULER),('restart', mod.API)])

    def test_refuse_to_overwrite_dirty_app_without_check_idle(self):
        (self.target/'collection.html').write_text('<p>operator customization</p>')
        with (patch.object(mod, 'git', side_effect=self.fake_git),
              patch.object(mod.os, 'geteuid', return_value=0),
              patch.object(mod, 'check_idle') as idle):
            with self.assertRaisesRegex(mod.Blocked, 'LOCAL_CODE_DIFFERENT'):
                mod.apply(self.source, self.target)
            idle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
