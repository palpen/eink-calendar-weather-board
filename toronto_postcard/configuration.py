"""Shared validation for the publisher, installer, and device settings."""

import json
import re


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError('Configuration must be an object')
    interval = config.get('publish_interval_seconds', 1800)
    if type(interval) is not int or interval < 180:
        raise ValueError('publish_interval_seconds must be an integer of at least 180 seconds')
    targets = config.get('targets', [])
    if not isinstance(targets, list):
        raise ValueError('targets must be a list')
    names = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError('Each target must be an object')
        name = target.get('name', '')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name):
            raise ValueError('Device names must use 1-64 letters, numbers, dots, dashes, or underscores')
        if name in names:
            raise ValueError('Device names must be unique')
        names.add(name)
        if type(target.get('enabled', True)) is not bool:
            raise ValueError('Device enabled must be true or false')
        for field in ('host', 'user'):
            value = target.get(field, '')
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', value) or value.startswith('-'):
                raise ValueError('Invalid device ' + field)
        port = target.get('port', 22)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Device port must be an integer from 1 to 65535')
        for field in ('identity_file', 'known_hosts_file'):
            if not isinstance(target.get(field), str) or not target[field].strip():
                raise ValueError('Device needs ' + field)
    return config


def load_config(path):
    return validate_config(json.loads(path.read_text()))
