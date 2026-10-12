"""Behavior tests: recurrence, timezone boundaries, display selection and input validation."""

import importlib.util
import io
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from toronto_postcard.model import Event, agenda
from toronto_postcard.render import render, wrap
from toronto_postcard.sources import parse_ics
from toronto_postcard.configuration import validate_config
from toronto_postcard import devices, publisher

ZONE = ZoneInfo('America/Toronto')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receiver', ROOT / 'receiver/receive.py')
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
installer_spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install_mac.py')
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)


def event(title, hour, duration=60, all_day=False, day=date(2026, 10, 12)):
    start = datetime(day.year, day.month, day.day, hour, tzinfo=ZONE)
    return Event(title, title, start, start + timedelta(minutes=duration), all_day)


class CalendarTests(unittest.TestCase):
    def test_recurring_event_with_exception_and_cancellation(self):
        data = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:weekly\r
DTSTART;TZID=America/Toronto:20261005T090000\r
DTEND;TZID=America/Toronto:20261005T100000\r
RRULE:FREQ=WEEKLY;COUNT=4\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:weekly\r
RECURRENCE-ID;TZID=America/Toronto:20261012T090000\r
DTSTART;TZID=America/Toronto:20261012T110000\r
DTEND;TZID=America/Toronto:20261012T120000\r
SUMMARY:Moved meeting\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cancelled\r
DTSTART:20261012T130000Z\r
DTEND:20261012T140000Z\r
STATUS:CANCELLED\r
END:VEVENT\r
END:VCALENDAR\r
'''
        events = parse_ics(data, date(2026, 10, 12), ZONE)
        self.assertEqual([(e.title, e.start.hour) for e in events], [('Moved meeting', 11)])

    def test_all_day_exclusive_end_and_utc_overnight(self):
        data = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:holiday\r
DTSTART;VALUE=DATE:20261012\r
DTEND;VALUE=DATE:20261013\r
SUMMARY:Holiday\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:overnight\r
DTSTART:20261012T033000Z\r
DTEND:20261012T050000Z\r
SUMMARY:Overnight\r
END:VEVENT\r
END:VCALENDAR\r
'''
        events = parse_ics(data, date(2026, 10, 12), ZONE)
        self.assertEqual(len(events), 2)
        self.assertTrue(next(e for e in events if e.title == 'Holiday').all_day)
        self.assertEqual(parse_ics(data, date(2026, 10, 13), ZONE), [])

    def test_floating_time_and_missing_end(self):
        data = b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261012T090000\r\nSUMMARY:A\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'
        events = parse_ics(data, date(2026, 10, 12), ZONE)
        self.assertEqual(events[0].start.hour, 9)
        self.assertIsNotNone(events[0].start.tzinfo)


class AgendaTests(unittest.TestCase):
    def test_all_day_never_highlighted_and_next_event_survives_overflow(self):
        events = [event('holiday', 0, 1440, True)] + [event(str(hour), hour) for hour in range(7, 20)]
        all_day, visible, selected, hidden = agenda(events, datetime(2026, 10, 12, 14, 30, tzinfo=ZONE))
        self.assertEqual(selected.title, '14')
        self.assertIn(selected, visible)
        self.assertEqual(len(all_day), 1)
        self.assertEqual(hidden, 9)

    def test_finished_day_has_no_next_event(self):
        self.assertIsNone(agenda([event('early', 7)], datetime(2026, 10, 12, 22, tzinfo=ZONE))[2])

    def test_overlapping_events_are_retained(self):
        events = [event('one', 15), event('two', 15, 30)]
        self.assertEqual(len(agenda(events, datetime(2026, 10, 12, 14, tzinfo=ZONE))[1]), 2)

    def test_fall_back_repeated_hour_orders_by_actual_instant(self):
        first = Event('first', 'First 01:15', datetime(2026, 11, 1, 1, 15, tzinfo=ZONE, fold=0),
                      datetime(2026, 11, 1, 1, 30, tzinfo=ZONE, fold=0))
        second = Event('second', 'Second 01:15', datetime(2026, 11, 1, 1, 15, tzinfo=ZONE, fold=1),
                       datetime(2026, 11, 1, 1, 30, tzinfo=ZONE, fold=1))
        now = datetime(2026, 11, 1, 1, 40, tzinfo=ZONE, fold=0)
        self.assertEqual(agenda([second, first], now)[2].uid, 'second')


class RenderingTests(unittest.TestCase):
    def test_realistic_long_titles_and_all_day_events_fit(self):
        events = [event('School closed for the long weekend', 0, 1440, True), event('Public holiday', 0, 1440, True)]
        events += [event('Morning routine: prepare breakfast and bags', 7, 5),
                   event('Project planning with the design team - Meeting Room 3', 15),
                   event('Water the plants', 17), event('Write reflections and organize notes from today', 17, 25)]
        weather = {'temperature': -18, 'feels_like': -24, 'code': 85, 'is_day': False,
                   'high': -10, 'low': -21, 'rain': 20, 'hourly': [], 'observed_at': 1791777600}
        im = render(datetime(2026, 10, 12, 0, tzinfo=ZONE), events, weather, 'snapshot')
        self.assertEqual(im.size, (600, 448))
        self.assertEqual(im.mode, 'P')
        self.assertLessEqual(im.getextrema()[1], 6)
        payload = io.BytesIO()
        im.save(payload, format='PNG')
        self.assertEqual(receiver.validate(payload.getvalue()).size, (600, 448))

    def test_empty_and_unavailable_are_real_states(self):
        im = render(datetime(2026, 10, 12, 0, tzinfo=ZONE), [], None, 'unavailable', 'unavailable')
        self.assertEqual(im.size, (600, 448))

    def test_wrap_stays_within_two_lines(self):
        self.assertLessEqual(len(wrap('An exceptionally long event title ' * 8, 251)), 2)

    def test_receiver_rejects_wrong_dimensions_and_malformed_input(self):
        for payload in (b'not an image', b'x' * (receiver.MAX_BYTES + 1)):
            with self.assertRaises(Exception): receiver.validate(payload)
        im = render(datetime(2026, 10, 12, 0, tzinfo=ZONE), [], None)
        stream = io.BytesIO()
        im.resize((448, 600)).save(stream, format='PNG')
        with self.assertRaises(ValueError): receiver.validate(stream.getvalue())

    def test_manual_force_keeps_cooldown_and_normal_mode_skips_duplicates(self):
        im = render(datetime(2026, 10, 12, 0, tzinfo=ZONE), [], None)
        stream = io.BytesIO()
        im.save(stream, format='PNG')
        payload = stream.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.state').mkdir()
            (root / 'eink-frame').mkdir()
            (root / 'eink-frame/.last-refresh').write_text(str(time.time()))
            (root / '.state/last-display.json').write_text(json.dumps({'sha256': hashlib.sha256(payload).hexdigest()}))
            with patch.object(receiver, 'ROOT', root), patch.object(receiver.Path, 'home', return_value=root):
                self.assertEqual(receiver.receive(payload)['status'], 'unchanged')
                self.assertEqual(receiver.receive(payload, force=True)['status'], 'cooldown')


class PublishingTests(unittest.TestCase):
    @staticmethod
    def target(name, enabled=True):
        return {'name': name, 'host': name + '.local', 'user': 'pi', 'enabled': enabled,
                'identity_file': '.secrets/' + name, 'known_hosts_file': '.secrets/' + name + '-hosts'}

    def test_offline_and_paused_devices_do_not_stop_other_deliveries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text(json.dumps({'targets': [self.target('offline'), self.target('paused', False),
                                                     self.target('working')]}))
            image = render(datetime(2026, 10, 12, 0, tzinfo=ZONE), [], None)

            def reply(command, **kwargs):
                if 'pi@offline.local' in command:
                    raise subprocess.CalledProcessError(255, command,
                                    stderr=b'ssh: connect to host offline.local port 22: Operation not permitted')
                digest = hashlib.sha256(kwargs['input']).hexdigest()
                return subprocess.CompletedProcess(command, 0, json.dumps({'status': 'displayed',
                                                          'sha256': digest, 'refresh_seconds': 27.8}).encode())

            with patch.object(publisher, 'load_calendar', return_value=([], 'live')), \
                 patch.object(publisher, 'load_weather', return_value=(None, 'unavailable')), \
                 patch.object(publisher, 'render', return_value=image), \
                 patch.object(publisher.subprocess, 'run', side_effect=reply) as transport, \
                 patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(publisher.run(config), 1)
                status = json.loads((root / '.state/status.json').read_text())
                self.assertEqual([device['status'] for device in status['targets']],
                                 ['error', 'disabled', 'displayed'])
                self.assertEqual(status['targets'][0]['exit_code'], 255)
                self.assertIn('Operation not permitted', status['targets'][0]['ssh_error'])
                self.assertEqual(transport.call_count, 2)
                self.assertEqual(publisher.run(config), 1)
                status = json.loads((root / '.state/status.json').read_text())
                self.assertEqual(status['targets'][2]['status'], 'unchanged')
                self.assertEqual(transport.call_count, 3)

    def test_device_commands_preserve_feeds_and_control_delivery_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            feeds = [{'url_file': '.secrets/private-calendar-marker'}]
            config.write_text(json.dumps({'calendar_feeds': feeds, 'targets': []}))
            prefix = ['--config', str(config)]
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                devices.main(prefix + ['add', '--name', 'kitchen', '--host', 'kitchen.local',
                                       '--identity-file', '.secrets/kitchen', '--known-hosts-file',
                                       '.secrets/kitchen-hosts', '--disabled'])
                self.assertFalse(json.loads(config.read_text())['targets'][0]['enabled'])
                devices.main(prefix + ['enable', 'kitchen'])
                devices.main(prefix + ['set-host', 'kitchen', '10.0.0.123'])
                devices.main(prefix + ['list'])
                settings = json.loads(config.read_text())
                self.assertTrue(settings['targets'][0]['enabled'])
                self.assertEqual(settings['targets'][0]['host'], '10.0.0.123')
                self.assertEqual(settings['calendar_feeds'], feeds)
                self.assertNotIn('private-calendar-marker', output.getvalue())
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
                devices.main(prefix + ['disable', 'kitchen'])
                self.assertFalse(json.loads(config.read_text())['targets'][0]['enabled'])

    def test_invalid_schedules_and_duplicate_devices_are_rejected(self):
        self.assertEqual(validate_config({}).get('publish_interval_seconds', 1800), 1800)
        for interval in (0, 179, True, '1800', 30.5):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                validate_config({'publish_interval_seconds': interval})
        with self.assertRaises(ValueError):
            validate_config({'targets': [self.target('same'), self.target('same')]})
        with self.assertRaises(ValueError):
            validate_config({'targets': [dict(self.target('bad'), enabled='false')]})

    def test_installer_uses_saved_schedule(self):
        for interval in (1800, 3600):
            with self.subTest(interval=interval), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / '.venv/bin').mkdir(parents=True)
                (root / '.venv/bin/python').touch()
                home = root / 'home'
                (home / 'Library').mkdir(parents=True)
                (root / 'config.json').write_text(json.dumps({'publish_interval_seconds': interval}))
                original_umask = os.umask(0o077)
                try:
                    with patch.object(installer, 'ROOT', root), \
                         patch.object(installer.Path, 'home', return_value=home), \
                         patch.object(installer.subprocess, 'run') as launchctl, \
                         patch('sys.stdout', new_callable=io.StringIO):
                        installer.main()
                    job = plistlib.loads((home / 'Library/LaunchAgents' / (installer.LABEL + '.plist')).read_bytes())
                    self.assertEqual(job['StartInterval'], interval)
                    self.assertTrue(job['RunAtLoad'])
                    self.assertEqual(job['ProgramArguments'][-1], str(root / 'config.json'))
                    self.assertEqual(job['WorkingDirectory'], str(home))
                    self.assertEqual(job['EnvironmentVariables']['PYTHONPATH'], str(root))
                    self.assertEqual(job['StandardOutPath'], str(home / 'Library/Logs/toronto-postcard/publisher.log'))
                    self.assertEqual(launchctl.call_args.args[0][1], 'bootstrap')
                finally:
                    os.umask(original_umask)


if __name__ == '__main__':
    unittest.main()
