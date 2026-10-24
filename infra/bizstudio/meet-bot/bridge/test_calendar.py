"""Calendar auto-join tests with fake Calendar API events: python test_calendar.py"""

import sqlite3
import threading
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import meetcal

ROME = ZoneInfo("Europe/Rome")
MEET = "https://meet.google.com/abc-defg-hij"


def at(day: int, hour: int, minute: int = 0, tz: ZoneInfo = ROME) -> float:
    return datetime(2026, 10, day, hour, minute, tzinfo=tz).timestamp()


def h(hour: int, minute: int = 0) -> float:
    return at(26, hour, minute)


def event(id: str, start: str, end: str = "", summary: str = "Call", **extra) -> dict:
    """A Calendar API event as events.list returns it with singleEvents=true."""
    item = {
        "kind": "calendar#event", "id": id, "status": "confirmed", "summary": summary,
        "start": {"dateTime": start, "timeZone": "Europe/Rome"},
        "end": {"dateTime": end or start.replace("T10:", "T11:"), "timeZone": "Europe/Rome"},
        "hangoutLink": MEET,
    }
    item.update(extra)
    return {key: value for key, value in item.items() if value is not None}


def parse(items: list[dict]) -> list[meetcal.Occurrence]:
    return meetcal.parse_events(items, ROME)


class Parsing(unittest.TestCase):
    def test_event_with_hangout_link(self):
        [occ] = parse([event("a1", "2026-10-26T10:00:00+01:00", "2026-10-26T10:30:00+01:00", "Kickoff")])
        self.assertEqual((occ.key, occ.uid, occ.title, occ.meeting_url), ("a1", "a1", "Kickoff", MEET))
        self.assertEqual((occ.start, occ.end), (h(10), h(10, 30)))

    def test_link_from_conference_data_location_or_description(self):
        items = [
            event("conf", "2026-10-26T10:00:00+01:00", hangoutLink=None, conferenceData={"entryPoints": [
                {"entryPointType": "phone", "uri": "tel:+39-02-1234"},
                {"entryPointType": "video", "uri": "https://meet.google.com/aaa-bbbb-ccc", "label": "meet.google.com/aaa-bbbb-ccc"},
            ]}),
            event("loc", "2026-10-26T11:00:00+01:00", "2026-10-26T12:00:00+01:00", hangoutLink=None,
                  location="https://meet.google.com/ddd-eeee-fff"),
            event("desc", "2026-10-26T12:00:00+01:00", "2026-10-26T13:00:00+01:00", hangoutLink=None,
                  description='Entra: <a href="https://meet.google.com/ggg-hhhh-iii?authuser=0">qui</a>'),
            event("zoom", "2026-10-26T13:00:00+01:00", "2026-10-26T14:00:00+01:00", hangoutLink=None,
                  conferenceData={"entryPoints": [{"entryPointType": "video", "uri": "https://zoom.us/j/1"}]}),
            event("office", "2026-10-26T14:00:00+01:00", "2026-10-26T15:00:00+01:00", hangoutLink=None, location="Via Roma 1"),
        ]
        self.assertEqual([o.meeting_url for o in parse(items)], [
            "https://meet.google.com/aaa-bbbb-ccc", "https://meet.google.com/ddd-eeee-fff", "https://meet.google.com/ggg-hhhh-iii"])

    def test_recurring_instances_and_moved_instance(self):
        items = [
            event("daily_20261026T090000Z", "2026-10-26T10:00:00+01:00", recurringEventId="daily",
                  originalStartTime={"dateTime": "2026-10-26T10:00:00+01:00"}),
            # Moved to the afternoon: Google keeps the instance id built from the original slot.
            event("daily_20261027T090000Z", "2026-10-27T15:00:00+01:00", "2026-10-27T15:30:00+01:00", "Daily (spostata)",
                  recurringEventId="daily", originalStartTime={"dateTime": "2026-10-27T10:00:00+01:00"}),
        ]
        found = parse(items)
        self.assertEqual([(o.key, o.uid, o.start) for o in found],
                         [("daily_20261026T090000Z", "daily", h(10)), ("daily_20261027T090000Z", "daily", at(27, 15))])

    def test_declined_by_me_is_skipped(self):
        items = [
            event("no", "2026-10-26T10:00:00+01:00", attendees=[
                {"email": "vincenzopetronebiz@gmail.com", "self": True, "responseStatus": "declined"},
                {"email": "cliente@example.com", "responseStatus": "accepted"}]),
            event("yes", "2026-10-26T12:00:00+01:00", "2026-10-26T13:00:00+01:00", attendees=[
                {"email": "vincenzopetronebiz@gmail.com", "self": True, "responseStatus": "accepted"},
                {"email": "altro@example.com", "responseStatus": "declined"}]),
            event("maybe", "2026-10-26T14:00:00+01:00", "2026-10-26T15:00:00+01:00", attendees=[
                {"email": "vincenzopetronebiz@gmail.com", "self": True, "responseStatus": "needsAction"}]),
            event("organizer", "2026-10-26T16:00:00+01:00", "2026-10-26T17:00:00+01:00"),
        ]
        self.assertEqual([o.key for o in parse(items)], ["yes", "maybe", "organizer"])

    def test_cancelled_all_day_and_nobot(self):
        items = [
            event("cancelled", "2026-10-26T10:00:00+01:00", status="cancelled"),
            {"id": "cancelled-instance", "status": "cancelled", "recurringEventId": "x"},
            {"id": "allday", "status": "confirmed", "summary": "Ferie", "start": {"date": "2026-10-26"},
             "end": {"date": "2026-10-27"}, "hangoutLink": MEET},
            event("tag-title", "2026-10-26T11:00:00+01:00", "2026-10-26T12:00:00+01:00", "Colloquio #NoBot"),
            event("tag-desc", "2026-10-26T12:00:00+01:00", "2026-10-26T13:00:00+01:00", description="privata #nobot"),
            event("ok", "2026-10-26T14:00:00+01:00", "2026-10-26T15:00:00+01:00", "Valida"),
            event("untitled", "2026-10-26T16:00:00+01:00", "2026-10-26T17:00:00+01:00", summary=None),
        ]
        self.assertEqual([o.title for o in parse(items)], ["Valida", "Riunione senza titolo"])

    def test_timezones(self):
        items = [
            event("utc", "2026-10-26T13:00:00Z", "2026-10-26T14:00:00Z"),
            event("ny", "2026-10-26T09:00:00-04:00", "2026-10-26T10:00:00-04:00"),
            {"id": "named", "status": "confirmed", "hangoutLink": MEET,
             "start": {"dateTime": "2026-10-26T17:00:00", "timeZone": "America/New_York"},
             "end": {"dateTime": "2026-10-26T17:45:00", "timeZone": "America/New_York"}},
            {"id": "noend", "status": "confirmed", "hangoutLink": MEET, "start": {"dateTime": "2026-10-26T18:00:00+01:00"}},
        ]
        found = {o.key: o for o in parse(items)}
        self.assertEqual(found["utc"].start, h(14))
        # New York is still on EDT (UTC-4) on 26 October while Rome is already on CET.
        self.assertEqual(found["ny"].start, h(14))
        self.assertEqual((found["named"].start, found["named"].end), (h(22), h(22, 45)))
        self.assertEqual(found["noend"].end, h(19))

    def test_duplicates_collapse_and_result_is_sorted(self):
        items = [event("b", "2026-10-26T12:00:00+01:00", "2026-10-26T13:00:00+01:00"),
                 event("a", "2026-10-26T10:00:00+01:00"), event("a", "2026-10-26T10:00:00+01:00")]
        self.assertEqual([o.key for o in parse(items)], ["a", "b"])


class Scheduling(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        meetcal.init_db(self.conn)
        self.launched: list[tuple[str, str]] = []
        self.busy = False

    def launch(self, url: str, title: str) -> str:
        if self.busy:
            raise meetcal.Busy("C'è già un bot in una riunione")
        self.launched.append((url, title))
        return f"bot_{len(self.launched)}"

    def occ(self, key="ev1", start=None, title="Call", url="https://meet.google.com/abc-defg-hij"):
        start = at(26, 10) if start is None else start
        return meetcal.Occurrence(key, key.split("|")[0], start, start + 1800, title, url)

    def sync(self, occs, now):
        meetcal.reconcile(self.conn, self.lock, occs, now)

    def tick(self, now, autojoin=True):
        meetcal.run_due(self.conn, self.lock, now, autojoin, self.launch)

    def row(self, key="ev1"):
        return self.conn.execute("SELECT * FROM calendar_events WHERE key = ?", (key,)).fetchone()

    def test_one_bot_per_occurrence(self):
        for now in (h(9, 0), h(9, 30), h(9, 58)):
            self.sync([self.occ()], now)
            self.tick(now)
        self.assertEqual(self.launched, [])
        for now in (h(9, 59), h(9, 59) + 30, h(10, 0), h(10, 5)):
            self.sync([self.occ()], now)
            self.tick(now)
        self.assertEqual(self.launched, [("https://meet.google.com/abc-defg-hij", "Call")])
        self.assertEqual(self.row()["status"], "launched")
        self.assertEqual(self.row()["bot_id"], "bot_1")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0], 1)

    def test_occurrences_of_a_series_are_separate(self):
        occs = [self.occ(f"daily|{at(26, 10):.0f}", at(26, 10)), self.occ(f"daily|{at(27, 10):.0f}", at(27, 10))]
        self.sync(occs, h(9))
        self.tick(h(10))
        self.tick(h(10, 1))
        self.assertEqual(len(self.launched), 1)
        self.tick(at(27, 10) - 30)
        self.assertEqual(len(self.launched), 2)

    def test_moved_event_follows_new_time_and_keeps_toggle(self):
        self.sync([self.occ()], h(8))
        event = self.row()["id"]
        self.assertFalse(meetcal.toggle(self.conn, self.lock, event))
        self.sync([self.occ(start=h(15), title="Call spostata")], h(9))
        row = self.row()
        self.assertEqual((row["start"], row["title"], row["enabled"]), (h(15), "Call spostata", 0))
        self.tick(h(10))
        self.tick(h(15))
        self.assertEqual(self.launched, [])
        self.assertTrue(meetcal.toggle(self.conn, self.lock, event))
        self.tick(h(15))
        self.assertEqual(self.launched, [("https://meet.google.com/abc-defg-hij", "Call spostata")])

    def test_deleted_or_declined_event_is_not_joined(self):
        self.sync([self.occ()], h(8))
        self.sync([], h(9))
        self.assertEqual(self.row()["status"], "gone")
        self.tick(h(10))
        self.assertEqual(self.launched, [])
        self.assertEqual(meetcal.upcoming(self.conn, self.lock, h(9)), [])
        self.sync([self.occ()], h(9, 30))
        self.tick(h(10))
        self.assertEqual(len(self.launched), 1)

    def test_busy_retries_until_free(self):
        self.sync([self.occ()], h(8))
        self.busy = True
        self.tick(h(10))
        self.tick(h(10, 5))
        self.assertEqual(self.row()["status"], "pending")
        self.assertIn("già un bot", self.row()["error"])
        self.busy = False
        self.tick(h(10, 10))
        self.assertEqual(self.row()["status"], "launched")
        self.assertIsNone(self.row()["error"])

    def test_busy_until_window_closes_is_missed(self):
        self.sync([self.occ()], h(8))
        self.busy = True
        self.tick(h(10))
        self.tick(h(10, 30))
        self.assertEqual(self.row()["status"], "missed")
        self.assertEqual(self.launched, [])

    def test_errors_give_up_after_max_attempts(self):
        def broken(url, title):
            raise RuntimeError("Attendee giù")
        self.sync([self.occ()], h(8))
        for second in range(meetcal.MAX_ATTEMPTS + 3):
            meetcal.run_due(self.conn, self.lock, h(10) + second * 20, True, broken)
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(self.row()["attempts"], meetcal.MAX_ATTEMPTS)

    def test_autojoin_off_and_disabled_are_missed_not_launched(self):
        self.sync([self.occ(), self.occ("ev2", h(10, 5))], h(8))
        meetcal.toggle(self.conn, self.lock, self.row("ev2")["id"])
        self.tick(h(10), autojoin=False)
        self.tick(h(10, 6), autojoin=True)
        self.assertEqual(self.launched, [("https://meet.google.com/abc-defg-hij", "Call")])
        self.tick(h(11))
        self.assertEqual((self.row("ev2")["status"], self.row("ev2")["error"]), ("missed", "disattivato"))

    def test_late_start_after_restart_joins_within_window(self):
        self.sync([self.occ()], h(10, 20))
        self.tick(h(10, 20))
        self.assertEqual(len(self.launched), 1)

    def test_launched_event_moved_later_gets_a_new_bot(self):
        self.sync([self.occ()], h(8))
        self.tick(h(10))
        self.sync([self.occ(start=h(10, 5))], h(10, 2))
        self.assertEqual(self.row()["status"], "launched")
        self.sync([self.occ(start=h(16))], h(11))
        self.assertEqual((self.row()["status"], self.row()["bot_id"]), ("pending", None))
        self.tick(h(16))
        self.assertEqual(len(self.launched), 2)

    def test_old_rows_are_pruned(self):
        self.sync([self.occ()], h(8))
        self.tick(h(10))
        self.sync([], at(29, 12))
        self.assertIsNone(self.row())


if __name__ == "__main__":
    unittest.main(verbosity=2)
