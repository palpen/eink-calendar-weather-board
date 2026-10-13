"""Keep unneeded image decoders outside the authenticated upload boundary."""

import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('format_receiver', ROOT / 'receiver/receive.py')
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class ReceiverFormatTests(unittest.TestCase):
    def test_other_formats_are_rejected_before_any_decoder(self):
        for payload in (b'RIFFsyntheticWEBP', b'GIF89asynthetic', b'II\x2asynthetic'):
            with self.subTest(payload=payload), patch.object(receiver.Image, 'open') as decoder:
                with self.assertRaises(ValueError):
                    receiver.validate(payload)
                decoder.assert_not_called()

    def test_valid_png_never_invokes_an_unrelated_registered_decoder(self):
        image = receiver.Image.new('P', (600, 448), 1)
        image.putpalette([v for rgb in receiver.COLORS for v in rgb] + [0] * (768 - 21))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        receiver.Image.init()

        def unsafe_decoder(*args):
            raise AssertionError('An unrelated decoder was invoked')

        with patch.object(receiver.Image, 'ID', ['CANARY', 'PNG']), \
             patch.dict(receiver.Image.OPEN, {'CANARY': (unsafe_decoder, lambda prefix: True)}):
            self.assertEqual(receiver.validate(stream.getvalue()).size, (600, 448))


if __name__ == '__main__':
    unittest.main()
