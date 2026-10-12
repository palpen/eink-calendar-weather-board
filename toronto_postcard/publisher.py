"""Publish one postcard to the enabled devices; launchd schedules refreshes."""

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .configuration import load_config
from .render import render
from .sources import atomic_json, load_calendar, load_weather


def ssh_command(target, root):
    for field in ('host', 'user'):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+', target[field]) or target[field].startswith('-'):
            raise ValueError('Unsafe SSH ' + field)
    port = int(target.get('port', 22))
    if not 1 <= port <= 65535:
        raise ValueError('Invalid SSH port')
    # Keep image delivery independent of administrator SSH aliases and sessions.
    return ['ssh', '-F', '/dev/null', '-T', '-p', str(port),
            '-o', 'ClearAllForwardings=yes', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-o', 'PermitLocalCommand=no', '-o', 'ControlPath=none', '-o', 'IdentityAgent=none',
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=30', '-o', 'ConnectionAttempts=2',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'UserKnownHostsFile=' + str(root / target['known_hosts_file']),
            '-i', str(root / target['identity_file']), target['user'] + '@' + target['host'], 'receive']


def run(config_path, push=True):
    os.umask(0o077)
    root = config_path.resolve().parent
    config = load_config(config_path)
    state = root / config.get('state_dir', '.state')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'publisher.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another refresh is already running', flush=True)
            return 0
        now = datetime.now(ZoneInfo(config.get('timezone', 'America/Toronto')))
        events, calendar_status = load_calendar(config, root, state, now)
        weather, weather_status = load_weather(config, state, now)
        image = render(now, events, weather, calendar_status, weather_status, config.get('city', 'Toronto'))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        payload = stream.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        (state / 'latest.png.tmp').write_bytes(payload)
        (state / 'latest.png.tmp').replace(state / 'latest.png')
        status = {'last_run': now.isoformat(), 'calendar': calendar_status, 'weather': weather_status,
                  'event_count': len(events), 'image_sha256': digest, 'targets': []}
        failures = 0
        delivered_file = state / 'delivered.json'
        delivered = json.loads(delivered_file.read_text()) if delivered_file.is_file() else {}
        for target in config.get('targets', []):
            name = target['name']
            delivery_key = '|'.join([name, target['host'], target['user'], str(target.get('port', 22))])
            result = {'name': name}
            if not target.get('enabled', True):
                result['status'] = 'disabled'
            elif not push:
                result['status'] = 'preview-only'
            elif delivered.get(delivery_key) == digest:
                result['status'] = 'unchanged'
            else:
                try:
                    command = ssh_command(target, root)
                    response = subprocess.run(command, input=payload, capture_output=True, timeout=110, check=True)
                    ack = json.loads(response.stdout)
                    if ack.get('status') not in ('displayed', 'unchanged') or ack.get('sha256') != digest:
                        raise ValueError('Receiver did not acknowledge this image')
                    delivered[delivery_key] = digest
                    atomic_json(delivered_file, delivered)
                    result['status'] = ack['status']
                    result['refresh_seconds'] = ack.get('refresh_seconds')
                except Exception as exc:
                    # Isolate target failures so an offline Pi cannot block the others.
                    result['status'] = 'error'
                    result['error'] = type(exc).__name__
                    if isinstance(exc, subprocess.CalledProcessError):
                        result['exit_code'] = exc.returncode
                        # SSH sees only the receiver and image, never calendar feed credentials.
                        if exc.stderr:
                            result['ssh_error'] = exc.stderr.decode(errors='replace').strip()[-1000:]
                        try:
                            ack = json.loads(exc.stdout)
                            if ack.get('status') in ('busy', 'cooldown', 'error'):
                                result['receiver_status'] = ack['status']
                            if re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', ack.get('error', '')):
                                result['receiver_error'] = ack['error']
                        except (ValueError, TypeError, AttributeError):
                            pass
                    failures += 1
            status['targets'].append(result)
        atomic_json(state / 'status.json', status)
        print(json.dumps(status), flush=True)
        return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--preview-only', action='store_true')
    args = parser.parse_args()
    try:
        return run(args.config, not args.preview_only)
    except Exception as exc:
        print('Publisher failed: ' + type(exc).__name__, file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
