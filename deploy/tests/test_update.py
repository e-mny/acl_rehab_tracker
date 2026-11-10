"""Exercise deployment decisions with a fake Docker CLI; no daemon or private data."""
import json
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'update.sh'
REVISION = 'a' * 40
PREVIOUS = 'b' * 40
CONFIG = {
    'services': {name: {'image': f'example/{name}:latest'} for name in ('stride', 'telegram', 'pose')},
    'volumes': {name: {'external': True, 'name': 'example_' + name} for name in ('stride_data', 'telegram_data')},
}

FAKE_DOCKER = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['COMMAND_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:1] == ['compose']:
    action = args[5:]
    if action == ['config', '--format', 'json']:
        print(os.environ['FAKE_CONFIG'])
    elif action[:1] == ['pull']:
        sys.exit(int(os.environ.get('PULL_STATUS', '0')))
    elif action[:1] == ['ps']:
        print('container-' + action[-1])
    elif action[:1] == ['up']:
        sys.exit(int(os.environ.get('UP_STATUS', '0')))
    else:
        sys.exit('Unexpected compose call: ' + str(action))
elif args[:2] == ['volume', 'inspect']:
    sys.exit(int(os.environ.get('VOLUME_STATUS', '0')))
elif args[:2] == ['image', 'inspect']:
    service = args[-1].split('/')[-1].split(':')[0]
    print(json.loads(os.environ['IMAGE_REVISIONS']).get(service, ''))
elif args[:1] == ['inspect']:
    if '.Mounts' in args[2]:
        print(os.environ.get('MOUNT_OVERRIDE', 'example_stride_data' if args[-1] == 'container-stride' else 'example_telegram_data'))
    else:
        print(os.environ['PREVIOUS_REVISION'])
else:
    sys.exit('Unexpected docker call: ' + str(args))
'''


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        (self.path / '.env').write_text('')
        (self.path / 'compose.production.yaml').write_text('')
        executable = self.path / 'docker'
        executable.write_text(FAKE_DOCKER)
        executable.chmod(0o755)
        self.log = self.path / 'commands.jsonl'
        self.env = dict(os.environ, STRIDE_DEPLOY_DIR=str(self.path),
                        PATH=str(self.path) + os.pathsep + os.environ['PATH'],
                        COMMAND_LOG=str(self.log), FAKE_CONFIG=json.dumps(CONFIG),
                        IMAGE_REVISIONS=json.dumps(dict.fromkeys(CONFIG['services'], REVISION)),
                        PREVIOUS_REVISION=PREVIOUS)

    def run_update(self, **env):
        return subprocess.run(['bash', str(SCRIPT)], env=self.env | env, capture_output=True, text=True)

    def actions(self):
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [call[5:] for call in calls if call[0] == 'compose']

    def assert_not_deployed(self, result):
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] == 'up' for call in self.actions()))
        self.assertFalse((self.path / '.deploy-state/current-release').exists())

    def test_complete_release_reconciles_and_records_rollback_revision(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.path / '.deploy-state/current-release').read_text().strip(), REVISION)
        self.assertEqual((self.path / '.deploy-state/previous-release').read_text().strip(), PREVIOUS)
        self.assertIn(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '180'], self.actions())

    def test_mixed_release_does_not_touch_running_containers(self):
        result = self.run_update(IMAGE_REVISIONS=json.dumps({'stride': REVISION, 'telegram': PREVIOUS, 'pose': REVISION}))
        self.assert_not_deployed(result)
        self.assertIn('different revisions', result.stderr)

    def test_pull_failure_does_not_recreate_containers(self):
        self.assert_not_deployed(self.run_update(PULL_STATUS='1'))

    def test_missing_or_wrong_volume_fails_before_pull(self):
        for variable in ({'VOLUME_STATUS': '1'}, {'MOUNT_OVERRIDE': 'some-other-data'}):
            with self.subTest(variable=variable):
                self.log.unlink(missing_ok=True)
                self.assert_not_deployed(self.run_update(**variable))
                self.assertFalse(any(call[0] == 'pull' for call in self.actions()))

    def test_missing_image_revision_is_rejected(self):
        self.assert_not_deployed(self.run_update(IMAGE_REVISIONS='{}'))

    def test_unhealthy_update_is_not_marked_successful(self):
        result = self.run_update(UP_STATUS='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / '.deploy-state/current-release').exists())
        self.assertEqual((self.path / '.deploy-state/previous-release').read_text().strip(), PREVIOUS)

    def test_app_only_profile_ignores_unused_bot_volume(self):
        config = {'services': {'stride': CONFIG['services']['stride']},
                  'volumes': {'stride_data': CONFIG['volumes']['stride_data']}}
        result = self.run_update(FAKE_CONFIG=json.dumps(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('container-telegram', self.log.read_text())

    def test_repeated_reconcile_keeps_rollback_revision(self):
        self.assertEqual(self.run_update().returncode, 0)
        self.assertEqual(self.run_update(PREVIOUS_REVISION=REVISION).returncode, 0)
        self.assertEqual((self.path / '.deploy-state/previous-release').read_text().strip(), PREVIOUS)

    def test_overlapping_run_exits_without_docker_calls(self):
        state = self.path / '.deploy-state'
        state.mkdir()
        with (state / 'update.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
