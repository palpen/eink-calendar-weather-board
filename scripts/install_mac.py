#!/usr/bin/env python3
"""Install/update this checkout's launchd publisher as the logged-in Mac user."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'local.toronto-postcard.publisher'
sys.path.insert(0, str(ROOT))

from toronto_postcard.configuration import load_config


def main():
    os.umask(0o077)
    config = ROOT / 'config.json'
    if not config.is_file():
        raise SystemExit('Create config.json before installing the publisher')
    settings = load_config(config)
    interval = settings.get('publish_interval_seconds', 1800)
    state = ROOT / settings.get('state_dir', '.state')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    python = ROOT / '.venv/bin/python'
    if not python.is_file():
        raise SystemExit('Create .venv and install this project first')
    path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    path.parent.mkdir(exist_ok=True)
    logs = Path.home() / 'Library/Logs/toronto-postcard'
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    job = {'Label': LABEL, 'ProgramArguments': [str(python), '-m', 'toronto_postcard.publisher',
            '--config', str(config)], 'WorkingDirectory': str(Path.home()), 'StartInterval': interval,
           'RunAtLoad': True, 'ProcessType': 'Background', 'Umask': 0o077,
           'StandardOutPath': str(logs / 'publisher.log'), 'StandardErrorPath': str(logs / 'publisher-error.log'),
           'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'PYTHONPATH': str(ROOT)}}
    path.write_bytes(plistlib.dumps(job))
    domain = 'gui/' + str(os.getuid())
    subprocess.run(['launchctl', 'bootout', domain + '/' + LABEL], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
    print(f'Installed {LABEL} (every {interval / 60:g} minutes while this user is logged in)')


if __name__ == '__main__':
    main()
