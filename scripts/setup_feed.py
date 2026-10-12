#!/usr/bin/env python3
"""One-time localhost form: save a calendar feed over SSH without logging it."""

import argparse
import html
import re
import secrets
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


def validate_destination(destination):
    parts = destination.split('/')
    if (not re.fullmatch(r'/[A-Za-z0-9_/.-]+', destination)
            or any(part in ('', '.', '..') for part in parts[1:])
            or '.secrets' not in parts[1:-1]):
        raise ValueError('Destination must be an absolute file path inside .secrets without dot segments')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='An existing trusted SSH alias for the publisher Mac')
    parser.add_argument('--destination', required=True, help='Absolute private feed-file path on that Mac')
    parser.add_argument('--port', type=int, default=8769)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit('Port must be between 1 and 65535')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.host) or args.host.startswith('-'):
        raise SystemExit('Invalid SSH alias')
    try:
        validate_destination(args.destination)
    except ValueError as exc:
        raise SystemExit(str(exc))
    nonce = secrets.token_urlsafe(24)
    route = '/' + nonce
    saved = False
    authority = '127.0.0.1:' + str(args.port)
    origin = 'http://' + authority

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *unused):
            pass  # Never log requests, form values, or credentials.

        def page(self, status, body):
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(('<!doctype html><html><head><meta charset="utf-8"><title>Calendar feed setup</title>'
                             '<style>body{font:18px system-ui;max-width:520px;margin:60px auto;padding:24px;}'
                             'label{display:block;margin:24px 0 8px;}input{box-sizing:border-box;width:100%;padding:12px;font-size:18px;}'
                             'button{padding:12px 18px;font-size:18px;margin-top:20px;}</style></head><body>'
                             + body + '</body></html>').encode())

        def do_GET(self):
            if self.headers.get('Host') != authority or self.path != route:
                self.page(404, '<h1>Not found</h1>')
                return
            self.page(200, '<h1>Connect your calendar</h1><p>The private read-only link is saved only on your publisher Mac.</p>'
                      '<form method="post" action="' + html.escape(route, quote=True) + '">'
                      '<label for="feed">Private calendar feed URL</label>'
                      '<input id="feed" name="feed" type="password" autocomplete="off" required>'
                      '<button type="submit">Save on Mac mini</button></form>')

        def do_POST(self):
            nonlocal saved
            if (self.headers.get('Host') != authority or self.path != route
                    or self.headers.get('Origin') != origin):
                self.page(403, '<h1>Invalid setup session</h1>')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if not 0 < length <= 4096 or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                self.page(400, '<h1>Invalid calendar link</h1>')
                return
            try:
                value = parse_qs(self.rfile.read(length).decode(), max_num_fields=4).get('feed', [''])[0].strip()
            except (ValueError, UnicodeError, TimeoutError):
                self.page(400, '<h1>Invalid calendar link</h1>')
                return
            try:
                parsed = urlparse(value)
            except ValueError:
                self.page(400, '<h1>Invalid calendar link</h1>')
                return
            if (parsed.scheme != 'https' or parsed.netloc != 'calendar.google.com'
                    or '/private-' not in parsed.path or not parsed.path.endswith('.ics') or parsed.fragment):
                self.page(400, '<h1>Use your Google Calendar private HTTPS iCal link</h1>')
                return
            command = 'umask 077; cat > ' + args.destination + '; chmod 600 ' + args.destination
            try:
                subprocess.run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                '-o', 'ClearAllForwardings=yes', '-o', 'ForwardAgent=no',
                                '-o', 'ForwardX11=no', '-o', 'PermitLocalCommand=no',
                                '-o', 'ControlPath=none', args.host, command],
                               input=(value + '\n').encode(), capture_output=True, check=True, timeout=25)
            except Exception:
                self.page(502, '<h1>The link could not be saved</h1><p>Check the publisher SSH connection and try again.</p>')
                return
            value = ''
            saved = True
            self.page(200, '<h1>Calendar feed saved</h1><p>The publisher can now refresh your calendar automatically.</p>')

    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.timeout = 1
    print('Setup page: http://127.0.0.1:' + str(args.port) + route, flush=True)
    while not saved:
        server.handle_request()
    server.server_close()
    print('Private calendar feed saved on the publisher Mac.', flush=True)


if __name__ == '__main__':
    main()
