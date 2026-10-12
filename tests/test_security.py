"""Security boundaries using synthetic inputs and no live credentials or devices."""

import importlib.util
import io
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from toronto_postcard.publisher import ssh_command

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup_feed = load_script('security_setup_feed', 'scripts/setup_feed.py')
receiver = load_script('security_receiver', 'receiver/receive.py')


class ConnectionSecurityTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ssh'), 'OpenSSH is required to inspect effective options')
    def test_display_connection_ignores_hostile_ssh_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hostile = root / 'hostile-ssh-config'
            hostile.write_text('''Host *
    ProxyCommand /usr/bin/false
    ForwardAgent yes
    ForwardX11 yes
    PermitLocalCommand yes
    LocalCommand /usr/bin/false
    ControlMaster auto
    ControlPath /tmp/synthetic-hostile-multiplexer
    IdentityAgent /tmp/synthetic-hostile-agent
    UserKnownHostsFile /tmp/synthetic-untrusted-hosts
    GlobalKnownHostsFile /tmp/synthetic-global-hosts
    RemoteForward 2222 synthetic.invalid:22
''')
            target = {'host': 'synthetic.invalid', 'user': 'pi',
                      'identity_file': '.secrets/synthetic-key',
                      'known_hosts_file': '.secrets/synthetic-hosts'}

            def options(command):
                # -G prints configuration without connecting or executing commands.
                result = subprocess.run(command, capture_output=True, text=True,
                                        timeout=5, check=True)
                return dict(line.split(' ', 1) for line in result.stdout.splitlines()
                            if ' ' in line)

            unprotected = options(['ssh', '-G', '-F', str(hostile),
                                   'pi@synthetic.invalid'])
            self.assertEqual(unprotected['forwardagent'], 'yes')
            self.assertIn('proxycommand', unprotected)
            self.assertIn('remoteforward', unprotected)

            # The delivery command's explicit -F replaces an earlier config choice.
            effective = options(['ssh', '-G', '-F', str(hostile)]
                                + ssh_command(target, root)[1:])
            expected = {'forwardagent': 'no', 'forwardx11': 'no',
                        'permitlocalcommand': 'no', 'clearallforwardings': 'yes',
                        'controlmaster': 'false', 'identityagent': 'none',
                        'globalknownhostsfile': '/dev/null', 'batchmode': 'yes',
                        'identitiesonly': 'yes', 'stricthostkeychecking': 'true',
                        'userknownhostsfile': str(root / target['known_hosts_file'])}
            for key, value in expected.items():
                with self.subTest(option=key):
                    self.assertEqual(effective[key], value)
            for key in ('proxycommand', 'localcommand', 'remoteforward', 'controlpath'):
                with self.subTest(excluded_option=key):
                    self.assertNotIn(key, effective)

    def test_calendar_destination_requires_a_private_path_without_traversal(self):
        setup_feed.validate_destination('/home/publisher/postcard/.secrets/calendar.url')
        for destination in ('relative/.secrets/calendar.url',
                            '/home/publisher/calendar.url',
                            '/home/publisher/.secrets/../calendar.url',
                            '/home/publisher/.secrets/./calendar.url',
                            '/home/publisher/../.secrets/calendar.url',
                            '/home/publisher/.secrets//calendar.url',
                            '/home/publisher/.secrets/',
                            '/home/publisher/.secrets/calendar.url;touch-file'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                setup_feed.validate_destination(destination)


class CalendarSetupSecurityTests(unittest.TestCase):
    def test_setup_rejects_untrusted_requests_and_saves_only_a_valid_feed(self):
        authority = '127.0.0.1:8769'
        origin = 'http://' + authority
        route = '/synthetic-session'
        feed = 'https://calendar.google.com/calendar/ical/synthetic/private-fixture/basic.ics'
        valid_body = urlencode({'feed': feed}).encode()
        outcomes = []

        def request(expected, *, method='POST', host=authority, source=origin,
                    body=valid_body, content_type='application/x-www-form-urlencoded',
                    length=None, path=route):
            headers = Message()
            headers['Host'] = host
            if source is not None:
                headers['Origin'] = source
            headers['Content-Type'] = content_type
            headers['Content-Length'] = str(len(body)) if length is None else length
            return expected, method, path, headers, body

        scenarios = iter([
            request(404, method='GET', host='attacker.invalid:8769'),
            request(403, host='attacker.invalid:8769'),
            request(403, source='https://attacker.invalid'),
            request(403, source=None),
            request(403, path='/wrong-session'),
            request(400, length='invalid'),
            request(400, length='4097'),
            request(400, content_type='text/plain'),
            request(400, body=b'feed=\xff'),
            request(400, body=b'a=1&b=2&c=3&d=4&feed=fixture'),
            request(400, body=urlencode({'feed': feed.replace('https:', 'http:')}).encode()),
            request(400, body=urlencode({'feed': feed.replace('calendar.google.com',
                                                            'calendar.google.com.attacker.invalid')}).encode()),
            request(400, body=urlencode({'feed': feed + '#fragment'}).encode()),
            request(200),
        ])

        class SyntheticServer:
            def __init__(self, address, handler_type):
                self.address = address
                self.handler_type = handler_type
                self.closed = False

            def handle_request(self):
                try:
                    expected, method, path, headers, body = next(scenarios)
                except StopIteration:
                    raise AssertionError('Setup did not stop after accepting the valid feed')
                handler = self.handler_type.__new__(self.handler_type)
                handler.path = path
                handler.headers = headers
                handler.rfile = io.BytesIO(body)
                handler.wfile = io.BytesIO()
                statuses = []
                handler.send_response = statuses.append
                handler.send_header = lambda *unused: None
                handler.end_headers = lambda: None
                getattr(handler, 'do_' + method)()
                outcomes.append((expected, statuses, handler.wfile.getvalue()))

            def server_close(self):
                self.closed = True

        servers = []

        def server_factory(address, handler_type):
            server = SyntheticServer(address, handler_type)
            servers.append(server)
            return server

        destination = '/home/publisher/postcard/.secrets/calendar.url'
        with patch.object(setup_feed, 'HTTPServer', side_effect=server_factory), \
             patch.object(setup_feed.secrets, 'token_urlsafe', return_value='synthetic-session'), \
             patch.object(setup_feed.subprocess, 'run') as transport, \
             patch('sys.argv', ['setup_feed.py', '--host', 'synthetic-publisher',
                                '--destination', destination]), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            setup_feed.main()
            transport.assert_called_once()
            args, kwargs = transport.call_args
            self.assertEqual(args[0][-2], 'synthetic-publisher')
            self.assertEqual(kwargs['input'], (feed + '\n').encode())
            self.assertEqual(kwargs['timeout'], 25)
            self.assertNotIn(feed, output.getvalue())
        self.assertEqual(servers[0].address, ('127.0.0.1', 8769))
        self.assertTrue(servers[0].closed)
        self.assertEqual(len(outcomes), 14)
        for expected, statuses, body in outcomes:
            with self.subTest(expected=expected, response=body[:60]):
                self.assertEqual(statuses, [expected])
                self.assertNotIn(feed.encode(), body)


class ReceiverUploadSecurityTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'Upload deadlines require Unix signals')
    def test_incomplete_upload_exits_while_sender_keeps_stdin_open(self):
        program = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location('receiver_under_test', sys.argv[1])
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
try:
    receiver.read_payload(sys.stdin.buffer, timeout=1)
except TimeoutError:
    print('upload-timed-out', flush=True)
else:
    raise SystemExit('Incomplete upload returned without its deadline')
'''
        with subprocess.Popen([sys.executable, '-c', program, str(ROOT / 'receiver/receive.py')],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as child:
            try:
                child.stdin.write(b'partial synthetic upload')
                child.stdin.flush()
                self.assertEqual(child.wait(timeout=5), 0)
                self.assertFalse(child.stdin.closed)
                self.assertEqual(child.stdout.read().strip(), b'upload-timed-out')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'Upload deadlines require Unix signals')
    def test_upload_limit_is_bounded_and_signal_handler_is_restored(self):
        previous = signal.getsignal(signal.SIGALRM)
        payload = receiver.read_payload(io.BytesIO(b'x' * (receiver.MAX_BYTES + 2)), timeout=1)
        self.assertEqual(len(payload), receiver.MAX_BYTES + 1)
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)


if __name__ == '__main__':
    unittest.main()
