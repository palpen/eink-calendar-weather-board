"""Untrusted calendar content stays bounded before rendering or caching."""

import math
import signal
import time
import unittest
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

from toronto_postcard import sources
from toronto_postcard.render import ellipsis

DAY = date(2026, 10, 12)
ZONE = ZoneInfo('America/Toronto')


def calendar(entries):
    return ('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n' + ''.join(entries) + 'END:VCALENDAR\r\n').encode()


def entry(uid='example', summary='Example', location='', rule=''):
    return ('BEGIN:VEVENT\r\nUID:' + uid + '\r\n'
            'DTSTART;TZID=America/Toronto:20261012T090000\r\n'
            'DTEND;TZID=America/Toronto:20261012T090100\r\n'
            'SUMMARY:' + summary + '\r\nLOCATION:' + location + '\r\n' + rule +
            'END:VEVENT\r\n')


class FeedLimitTests(unittest.TestCase):
    def test_long_feed_text_is_limited_before_rendering(self):
        events = sources.parse_ics(calendar([entry(summary='Title ' * 2000, location='Room ' * 2000)]), DAY, ZONE)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].title), sources.MAX_EVENT_TEXT)
        self.assertEqual(len(events[0].location), sources.MAX_EVENT_TEXT)
        self.assertTrue(ellipsis(events[0].title, 100).endswith('…'))

    def test_too_many_calendar_entries_are_rejected_before_expansion(self):
        payload = calendar([entry(uid=str(i)) for i in range(11)])
        with patch.object(sources, 'MAX_CALENDAR_COMPONENTS', 10), \
             patch.object(sources.recurring_ical_events, 'of') as expand:
            with self.assertRaisesRegex(ValueError, 'too many events'):
                sources.parse_ics(payload, DAY, ZONE)
        expand.assert_not_called()

    def test_historical_entries_do_not_use_up_the_daily_event_limit(self):
        history = [entry(uid=str(i)).replace('20261012', '20201012')
                   for i in range(sources.MAX_CALENDAR_EVENTS + 1)]
        events = sources.parse_ics(calendar(history + [entry(uid='today')]), DAY, ZONE)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].uid.startswith('today:'))

    def test_daily_event_limit_still_applies_to_large_history_feeds(self):
        payload = calendar([entry(uid=str(i)) for i in range(sources.MAX_CALENDAR_EVENTS + 1)])
        with self.assertRaisesRegex(ValueError, 'recurrence expands'):
            sources.parse_ics(payload, DAY, ZONE)

    def test_dense_recurrence_is_rejected(self):
        rule = 'RRULE:FREQ=SECONDLY;COUNT=' + str(sources.MAX_CALENDAR_EVENTS + 1) + '\r\n'
        with self.assertRaisesRegex(ValueError, 'recurrence expands'):
            sources.parse_ics(calendar([entry(rule=rule)]), DAY, ZONE)

    @unittest.skipUnless(hasattr(signal, 'setitimer'), 'Unix signal deadline')
    def test_slow_parser_times_out_and_restores_signal_state(self):
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        handler = lambda signum, frame: None
        try:
            signal.signal(signal.SIGALRM, handler)
            signal.setitimer(signal.ITIMER_REAL, 60)
            with patch.object(sources, 'CALENDAR_PARSE_SECONDS', 0.02), \
                 patch.object(sources.Calendar, 'from_ical', side_effect=lambda value: time.sleep(0.2)):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    sources.parse_ics(b'fake input', DAY, ZONE)
                self.assertLess(time.monotonic() - started, 0.15)
            self.assertIs(signal.getsignal(signal.SIGALRM), handler)
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            self.assertGreater(remaining, 59)
            self.assertLess(remaining, 60)
            self.assertEqual(interval, 0)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)

    def test_long_title_needs_logarithmic_measurements(self):
        class MeasuringFont:
            calls = 0

            def getlength(self, text):
                self.calls += 1
                return len(text)

        measured = MeasuringFont()
        text = 'x' * 100000
        with patch('toronto_postcard.render.font', return_value=measured):
            shortened = ellipsis(text, 20)
        self.assertEqual(shortened, 'x' * 19 + '…')
        self.assertLessEqual(measured.calls, math.ceil(math.log2(len(text))) + 2)


if __name__ == '__main__':
    unittest.main()
