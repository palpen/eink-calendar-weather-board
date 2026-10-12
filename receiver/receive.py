#!/usr/bin/env python3
"""Restricted SSH image receiver. Compatible with the Pi's existing Python 3.9."""

import argparse
import fcntl
import hashlib
import io
import json
import os
import signal
import sys
import time
from pathlib import Path

from PIL import Image

MAX_BYTES = 1024 * 1024
COLORS = [(0, 0, 0), (255, 255, 255), (0, 120, 60), (0, 72, 160),
          (200, 0, 0), (255, 215, 0), (240, 110, 0)]
ROOT = Path(__file__).resolve().parents[1]


def read_payload(stream, timeout=30):
    """Bound how long an authenticated client can leave its upload unfinished."""
    def expired(signum, frame):
        raise TimeoutError('Image upload timed out')

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(timeout)
    try:
        return stream.read(MAX_BYTES + 1)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def validate(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('Image size exceeds the receiver limit')
    Image.MAX_IMAGE_PIXELS = 600 * 448
    with Image.open(io.BytesIO(payload)) as source:
        if source.format != 'PNG' or source.size != (600, 448) or source.mode != 'P':
            raise ValueError('Only indexed 600 x 448 PNG images are accepted')
        source.load()
        if source.getpalette()[:21] != [v for rgb in COLORS for v in rgb]:
            raise ValueError('Unexpected seven-color palette')
        if source.getextrema()[1] > 6:
            raise ValueError('Unexpected color index')
        return source.copy()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n')
    temporary.replace(path)


def receive(payload, prepare_only=False, force=False):
    os.umask(0o077)
    image = validate(payload)
    digest = hashlib.sha256(payload).hexdigest()
    state = ROOT / '.state'
    state.mkdir(exist_ok=True, mode=0o700)
    # Share the lock and cooldown with the already-installed artwork helper.
    legacy = Path.home() / 'eink-frame'
    lock_dir = legacy if legacy.is_dir() else state
    with (lock_dir / '.display.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'sha256': digest}
        last_file = state / 'last-display.json'
        last = json.loads(last_file.read_text()) if last_file.is_file() else {}
        if not prepare_only and not force and last.get('sha256') == digest:
            return {'status': 'unchanged', 'sha256': digest}
        stamp = lock_dir / '.last-refresh'
        if not prepare_only and stamp.is_file():
            elapsed = time.time() - float(stamp.read_text())
            if elapsed < 180:
                return {'status': 'cooldown', 'sha256': digest,
                        'retry_after_seconds': round(180 - elapsed)}
        from inky.auto import auto
        display = auto()
        if tuple(display.resolution) != (600, 448) or type(display).__module__ != 'inky.inky_uc8159':
            raise RuntimeError('Unexpected panel; receiver is for Inky Impression 5.7-inch only')
        display.set_image(image)
        preview = Image.fromarray(display.buf, mode='P')
        colors = display._palette_blend(0.8)
        preview.putpalette(colors + [0] * (768 - len(colors)))
        preview.convert('RGB').save(state / 'palette-preview.png')
        image.save(state / 'prepared.png')
        if prepare_only:
            return {'status': 'prepared', 'sha256': digest, 'dimensions': [600, 448]}
        # Stamp before hardware I/O; a failed/partial refresh still needs cooldown.
        stamp.write_text(str(time.time()))
        started = time.monotonic()
        display.show()
        result = {'status': 'displayed', 'sha256': digest,
                  'refresh_seconds': round(time.monotonic() - started, 1),
                  'completed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
        atomic_json(last_file, result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--force', action='store_true', help='Admin-only manual refresh, still respecting the panel cooldown')
    args = parser.parse_args()
    # The forced SSH command ignores SSH_ORIGINAL_COMMAND and all client arguments.
    try:
        payload = read_payload(sys.stdin.buffer)
        result = receive(payload, args.prepare_only, args.force)
        print(json.dumps(result), flush=True)
        return 0 if result['status'] in ('displayed', 'unchanged', 'prepared') else 75
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error': type(exc).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
