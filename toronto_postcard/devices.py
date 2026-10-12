"""Manage the Mac publisher's devices without exposing calendar credentials."""

import argparse
from pathlib import Path

from .configuration import load_config, validate_config
from .sources import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='Show devices and whether publishing is enabled')
    add = commands.add_parser('add', help='Register a prepared Pi receiver')
    add.add_argument('--name', required=True)
    add.add_argument('--host', required=True)
    add.add_argument('--user', default='pi')
    add.add_argument('--port', type=int, default=22)
    add.add_argument('--identity-file', required=True)
    add.add_argument('--known-hosts-file', required=True)
    add.add_argument('--disabled', action='store_true')
    for command in ('enable', 'disable', 'set-host'):
        child = commands.add_parser(command)
        child.add_argument('name')
        if command == 'set-host':
            child.add_argument('host')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        targets = config.setdefault('targets', [])
        if args.command == 'list':
            for target in targets:
                state = 'enabled' if target.get('enabled', True) else 'paused'
                print(f"{target['name']}: {state} ({target['user']}@{target['host']}:{target.get('port', 22)})")
            if not targets:
                print('No devices registered')
            return 0
        if args.command == 'add':
            targets.append({'name': args.name, 'host': args.host, 'user': args.user, 'port': args.port,
                            'identity_file': args.identity_file, 'known_hosts_file': args.known_hosts_file,
                            'enabled': not args.disabled})
        else:
            target = next((target for target in targets if target['name'] == args.name), None)
            if target is None:
                raise ValueError('No device has that name')
            if args.command == 'set-host':
                target['host'] = args.host
            else:
                target['enabled'] = args.command == 'enable'
        validate_config(config)
        atomic_json(args.config, config)
        print('Saved device settings; the next publisher run will use them')
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(1, 'Device settings failed: ' + str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
