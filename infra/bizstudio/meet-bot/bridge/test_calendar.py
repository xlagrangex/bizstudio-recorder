"""Calendar auto-join tests with fake iCal fixtures: python test_calendar.py"""

import sqlite3
import threading
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import meetcal

ROME = ZoneInfo("Europe/Rome")
OWNER = "vincenzopetronebiz@gmail.com"
# Monday 26 October 2026, 08:00 Rome (CET, the day after the DST switch).
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=ROME).timestamp()


def at(day: int, hour: int, minute: int = 0, tz: ZoneInfo = ROME) -> float:
    return datetime(2026, 10, day, hour, minute, tzinfo=tz).timestamp()


def h(hour: int, minute: int = 0) -> float:
    return at(26, hour, minute)


def calendar(*events: str, calname: str = OWNER) -> bytes:
    body = "\r\n".join(event.strip().replace("\n", "\r\n") for event in events)
    return f"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Google Inc//Google Calendar 70.9054//EN\r
X-WR-CALNAME:{calname}\r
X-WR-TIMEZONE:Europe/Rome\r
BEGIN:VTIMEZONE\r
TZID:Europe/Rome\r
BEGIN:DAYLIGHT\r
TZOFFSETFROM:+0100\r
TZOFFSETTO:+0200\r
TZNAME:CEST\r
DTSTART:19700329T020000\r
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r
END:DAYLIGHT\r
BEGIN:STANDARD\r
TZOFFSETFROM:+0200\r
TZOFFSETTO:+0100\r
TZNAME:CET\r
DTSTART:19701025T030000\r
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r
END:STANDARD\r
END:VTIMEZONE\r
{body}\r
END:VCALENDAR\r
""".encode()


def event(uid: str, start: str, end: str, summary: str = "Call", extra: str = "",
          meet: str = "X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij") -> str:
    return f"""BEGIN:VEVENT
UID:{uid}
DTSTART;TZID=Europe/Rome:{start}
DTEND;TZID=Europe/Rome:{end}
SUMMARY:{summary}
{meet}
{extra}
END:VEVENT""".replace("\n\n", "\n")


def parse(ics: bytes, now: float = NOW) -> list[meetcal.Occurrence]:
    return meetcal.parse(ics, now, owners=[OWNER], local_tz=ROME)


class Parsing(unittest.TestCase):
    def test_single_event_with_meet_link(self):
        [occ] = parse(calendar(event("a@google.com", "20261026T100000", "20261026T103000", "Kickoff")))
        self.assertEqual(occ.title, "Kickoff")
        self.assertEqual(occ.meeting_url, "https://meet.google.com/abc-defg-hij")
        self.assertEqual(occ.start, at(26, 10))
        self.assertEqual(occ.end, at(26, 10, 30))
        self.assertEqual(occ.key, "a@google.com")

    def test_link_found_in_location_or_description(self):
        ics = calendar(
            event("loc", "20261026T100000", "20261026T110000", meet="LOCATION:https://meet.google.com/aaa-bbbb-ccc"),
            event("desc", "20261026T120000", "20261026T130000",
                  meet="DESCRIPTION:Entra: https://meet.google.com/ddd-eeee-fff?authuser=0\\nGrazie"),
            event("none", "20261026T140000", "20261026T150000", meet="LOCATION:Ufficio, Via Roma 1"),
            event("zoom", "20261026T160000", "20261026T170000", meet="LOCATION:https://zoom.us/j/123"),
        )
        self.assertEqual([o.meeting_url for o in parse(ics)],
                         ["https://meet.google.com/aaa-bbbb-ccc", "https://meet.google.com/ddd-eeee-fff"])

    def test_recurring_with_exdate_moved_and_cancelled_occurrence(self):
        ics = calendar(
            event("daily", "20261026T100000", "20261026T103000", "Daily",
                  "RRULE:FREQ=DAILY;COUNT=10\nEXDATE;TZID=Europe/Rome:20261026T150000"),
            event("weekly", "20261026T090000", "20261026T093000", "Standup",
                  "RRULE:FREQ=WEEKLY;BYDAY=MO,TU\nEXDATE;TZID=Europe/Rome:20261026T090000"),
            # The 27th of the daily series is moved to the afternoon.
            event("daily", "20261027T150000", "20261027T153000", "Daily (spostata)",
                  "RECURRENCE-ID;TZID=Europe/Rome:20261027T100000"),
            # Google exports a cancelled single occurrence as a CANCELLED override.
            event("weekly", "20261027T090000", "20261027T093000", "Standup",
                  "RECURRENCE-ID;TZID=Europe/Rome:20261027T090000\nSTATUS:CANCELLED"),
        )
        found = parse(ics, now=at(26, 8))
        self.assertEqual([(o.title, o.start) for o in found], [("Daily", at(26, 10))])
        found = parse(ics, now=at(26, 20))
        # 24h window from 20:00 on the 26th: the moved daily of the 27th; standup 26 excluded, 27 cancelled.
        self.assertEqual([(o.title, o.start) for o in found], [("Daily (spostata)", at(27, 15))])
        moved = found[0]
        self.assertEqual(moved.key, f"daily|{at(27, 10):.0f}")

    def test_declined_by_owner_is_skipped(self):
        ics = calendar(
            event("declined", "20261026T100000", "20261026T110000", "No grazie",
                  f"ATTENDEE;CN=Vincenzo;PARTSTAT=DECLINED:mailto:{OWNER.upper()}\n"
                  "ATTENDEE;PARTSTAT=ACCEPTED:mailto:cliente@example.com"),
            event("accepted", "20261026T120000", "20261026T130000", "Sì",
                  f"ATTENDEE;PARTSTAT=ACCEPTED:mailto:{OWNER}\nATTENDEE;PARTSTAT=DECLINED:mailto:altro@example.com"),
            event("pending", "20261026T140000", "20261026T150000", "Da rispondere",
                  f"ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:{OWNER}"),
        )
        self.assertEqual([o.title for o in parse(ics)], ["Sì", "Da rispondere"])

    def test_owner_is_taken_from_calendar_name(self):
        ics = calendar(event("x", "20261026T100000", "20261026T110000", "Altro account",
                             "ATTENDEE;PARTSTAT=DECLINED:mailto:socio@bizstudio.it"), calname="socio@bizstudio.it")
        self.assertEqual(parse(ics), [])

    def test_declined_single_occurrence_of_series(self):
        ics = calendar(
            event("series", "20261026T100000", "20261026T110000", "Serie", "RRULE:FREQ=DAILY;COUNT=3"),
            event("series", "20261026T100000", "20261026T110000", "Serie",
                  f"RECURRENCE-ID;TZID=Europe/Rome:20261026T100000\nATTENDEE;PARTSTAT=DECLINED:mailto:{OWNER}"),
        )
        self.assertEqual(parse(ics, now=at(26, 8)), [])
        self.assertEqual(len(parse(ics, now=at(26, 20))), 1)

    def test_nobot_all_day_and_cancelled(self):
        ics = calendar(
            event("tag-title", "20261026T100000", "20261026T110000", "Colloquio #NoBot"),
            event("tag-desc", "20261026T110000", "20261026T120000", "Privata", "DESCRIPTION:riservata #nobot"),
            event("cancelled", "20261026T120000", "20261026T130000", "Annullata", "STATUS:CANCELLED"),
            """BEGIN:VEVENT
UID:allday
DTSTART;VALUE=DATE:20261026
DTEND;VALUE=DATE:20261027
SUMMARY:Ferie
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij
END:VEVENT""",
            event("ok", "20261026T140000", "20261026T150000", "Valida"),
        )
        self.assertEqual([o.title for o in parse(ics)], ["Valida"])

    def test_timezones_utc_other_zone_and_floating(self):
        ics = calendar(
            """BEGIN:VEVENT
UID:utc
DTSTART:20261026T130000Z
DTEND:20261026T140000Z
SUMMARY:UTC
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij
END:VEVENT""",
            """BEGIN:VEVENT
UID:ny
DTSTART;TZID=America/New_York:20261026T090000
DTEND;TZID=America/New_York:20261026T100000
SUMMARY:New York
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij
END:VEVENT""",
            """BEGIN:VEVENT
UID:floating
DTSTART:20261026T170000
DURATION:PT45M
SUMMARY:Floating
X-GOOGLE-CONFERENCE:https://meet.google.com/abc-defg-hij
END:VEVENT""",
        )
        found = {o.title: o for o in parse(ics)}
        self.assertEqual(found["UTC"].start, at(26, 14))
        # New York is still on EDT (UTC-4) on 26 October while Rome is already on CET.
        self.assertEqual(found["New York"].start, datetime(2026, 10, 26, 13, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(found["Floating"].start, at(26, 17))
        self.assertEqual(found["Floating"].end, at(26, 17, 45))

    def test_window_is_next_24_hours(self):
        ics = calendar(
            event("past", "20261026T050000", "20261026T060000", "Finita"),
            event("ongoing", "20261026T073000", "20261026T090000", "In corso"),
            event("tomorrow", "20261027T073000", "20261027T080000", "Domani presto"),
            event("later", "20261027T090000", "20261027T100000", "Fuori finestra"),
        )
        self.assertEqual([o.title for o in parse(ics)], ["In corso", "Domani presto"])


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
