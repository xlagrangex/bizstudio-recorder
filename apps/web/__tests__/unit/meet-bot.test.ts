import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	user: { id: "owner-1" } as { id: string } | null,
	env: {
		MEETBOT_API_URL: "http://meet-bridge:8080/meetbot/",
		MEETBOT_API_TOKEN: "bridge-token",
		MEETBOT_OWNER_ID: "owner-1",
	} as Record<string, string | undefined>,
	query: vi.fn(),
	revalidatePath: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({ revalidatePath: mocks.revalidatePath }));
vi.mock("@cap/env", () => ({ serverEnv: () => mocks.env }));
vi.mock("@cap/database/auth/session", () => ({
	getCurrentUser: async () => mocks.user,
}));
vi.mock("@cap/database", async () => {
	const { drizzle } = await import("drizzle-orm/mysql-proxy");
	const client = drizzle(mocks.query);
	return { db: () => client };
});

import {
	sendMeetBot,
	setMeetBotEventEnabled,
	syncMeetBotCalendar,
} from "@/actions/meet-bot";
import {
	eventStatus,
	formatDuration,
	groupByDay,
	isMeetLink,
	type MeetBotEvent,
	type MeetBotMeeting,
	meetingStatus,
	type RecordedMeeting,
	timeRange,
} from "@/lib/meet-bot";
import {
	loadMeetBotOverview,
	requireMeetBotOwner,
} from "@/lib/meet-bot-server";

// Monday 26 October 2026, 08:00 in Rome (CET, UTC+1).
const NOW = Date.UTC(2026, 9, 26, 7, 0) / 1000;
const at = (day: number, hour: number, minute = 0) =>
	Date.UTC(2026, 9, day, hour - 1, minute) / 1000;

const event = (overrides: Partial<MeetBotEvent> = {}): MeetBotEvent => ({
	id: "0123456789abcdef",
	title: "Call",
	start: at(26, 10),
	end: at(26, 10, 30),
	meeting_url: "https://meet.google.com/abc-defg-hij",
	enabled: true,
	status: "pending",
	bot_id: null,
	error: null,
	bot_stage: null,
	bot_state: null,
	video_id: null,
	...overrides,
});

const meeting = (
	overrides: Partial<RecordedMeeting> = {},
): RecordedMeeting => ({
	bot_id: "bot_1",
	title: "Call",
	meeting_url: "https://meet.google.com/abc-defg-hij",
	stage: "imported",
	attendee_state: "ended",
	video_id: "vid1",
	error: null,
	label: "importato",
	created_at: NOW,
	updated_at: NOW,
	source: "calendar",
	scheduled_start: NOW,
	video: {
		id: "vid1",
		name: "Meet – Call",
		duration: 1800,
		transcriptionStatus: "COMPLETE",
	},
	...overrides,
});

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

describe("meet bot view helpers", () => {
	it("groups events by Rome day with Oggi and Domani", () => {
		const groups = groupByDay(
			[
				event({ id: "c", start: at(28, 9), end: at(28, 10) }),
				event({ id: "a", start: at(26, 10), end: at(26, 11) }),
				event({ id: "b", start: at(27, 0, 30), end: at(27, 1) }),
				event({ id: "z", start: at(25, 23), end: at(26, 9) }),
			],
			NOW,
		);
		expect(groups.map((g) => [g.label, g.events.map((e) => e.id)])).toEqual([
			["Oggi", ["z", "a"]],
			["Domani", ["b"]],
			["Mercoledì 28 ottobre", ["c"]],
		]);
		expect(timeRange(at(26, 10), at(26, 10, 30))).toBe("10:00 – 10:30");
	});

	it("maps calendar states to Italian badges", () => {
		expect(eventStatus(event(), true).label).toBe("Programmato");
		expect(eventStatus(event({ enabled: false }), true).label).toBe(
			"Il bot non entra",
		);
		expect(eventStatus(event(), false).label).toBe(
			"Ingresso automatico spento",
		);
		expect(
			eventStatus(
				event({
					status: "launched",
					bot_stage: "active",
					bot_state: "joining",
				}),
				true,
			).label,
		).toBe("Bot in arrivo");
		expect(
			eventStatus(
				event({
					status: "launched",
					bot_stage: "active",
					bot_state: "waiting_room",
				}),
				true,
			).detail,
		).toContain("ammettilo");
		expect(
			eventStatus(
				event({
					status: "launched",
					bot_stage: "active",
					bot_state: "joined_recording",
				}),
				true,
			),
		).toMatchObject({ label: "In corso", tone: "live" });
		expect(
			eventStatus(event({ status: "launched", bot_stage: "imported" }), true)
				.label,
		).toBe("Registrata");
		expect(
			eventStatus(event({ status: "missed", error: "disattivato" }), true),
		).toMatchObject({ label: "Saltato", detail: "disattivato" });
		expect(eventStatus(event({ status: "failed" }), true).label).toBe(
			"Fallito",
		);
	});

	it("maps recorded meetings to processing states", () => {
		expect(meetingStatus(meeting()).label).toBe("Trascritto");
		expect(
			meetingStatus(
				meeting({
					video: { id: "v", name: "x", duration: 1, transcriptionStatus: null },
				}),
			).label,
		).toBe("In elaborazione");
		expect(meetingStatus(meeting({ stage: "importing" })).label).toBe(
			"In elaborazione",
		);
		expect(
			meetingStatus(
				meeting({ stage: "no_recording", error: "registrazione vuota" }),
			),
		).toMatchObject({
			label: "Nessuna registrazione",
			detail: "registrazione vuota",
		});
		expect(meetingStatus(meeting({ stage: "failed" })).label).toBe("Errore");
		expect(meetingStatus(meeting({ video: null })).label).toBe(
			"Video eliminato",
		);
		expect(
			meetingStatus(
				meeting({ stage: "active", attendee_state: "joined_recording" }),
			).label,
		).toBe("In corso");
	});

	it("formats durations and validates Meet links", () => {
		expect(formatDuration(3725)).toBe("1 h 2 min");
		expect(formatDuration(1800)).toBe("30 min");
		expect(formatDuration(42)).toBe("42 s");
		expect(formatDuration(null)).toBeNull();
		expect(isMeetLink("https://meet.google.com/abc-defg-hij")).toBe(true);
		expect(isMeetLink("https://meet.google.com/abc-defg-hij?authuser=0")).toBe(
			true,
		);
		expect(isMeetLink("https://evil.example/abc-defg-hij")).toBe(false);
	});
});

describe("meet bot server access", () => {
	const fetchMock = vi.fn<typeof fetch>();

	beforeEach(() => {
		mocks.user = { id: "owner-1" };
		mocks.env.MEETBOT_OWNER_ID = "owner-1";
		mocks.query.mockReset();
		mocks.revalidatePath.mockReset();
		fetchMock.mockReset();
		vi.stubGlobal("fetch", fetchMock);
	});

	afterEach(() => vi.unstubAllGlobals());

	it("lets only the configured owner in", async () => {
		expect(await requireMeetBotOwner()).toEqual({ id: "owner-1" });
		mocks.user = { id: "someone-else" };
		expect(await requireMeetBotOwner()).toBeNull();
		mocks.user = null;
		expect(await requireMeetBotOwner()).toBeNull();
		mocks.user = { id: "owner-1" };
		mocks.env.MEETBOT_OWNER_ID = undefined;
		expect(await requireMeetBotOwner()).toBeNull();
	});

	it("loads calendar and meetings server side with the bearer token and joins Cap videos", async () => {
		const meetings: MeetBotMeeting[] = [
			{ ...meeting(), video: undefined } as unknown as MeetBotMeeting,
			{
				...meeting({ bot_id: "bot_2", video_id: null, stage: "failed" }),
				video: undefined,
			} as unknown as MeetBotMeeting,
		];
		fetchMock.mockImplementation(async (input) =>
			String(input).includes("/api/calendar")
				? json({ connected: true, events: [], autojoin: true, notice: null })
				: json(meetings),
		);
		mocks.query.mockResolvedValue({
			rows: [["vid1", "Meet – Call", 1800, "COMPLETE"]],
		});

		const overview = await loadMeetBotOverview("owner-1");

		const urls = fetchMock.mock.calls.map(([url]) => String(url)).sort();
		expect(urls).toEqual([
			"http://meet-bridge:8080/meetbot/api/calendar?notice=1",
			"http://meet-bridge:8080/meetbot/api/meetings",
		]);
		for (const [, init] of fetchMock.mock.calls)
			expect(new Headers(init?.headers).get("Authorization")).toBe(
				"Bearer bridge-token",
			);
		const [sql, params] = mocks.query.mock.calls[0] as [string, unknown[]];
		expect(sql).toContain("`ownerId` = ?");
		expect(params).toEqual(["vid1", "owner-1"]);
		expect(overview.recorded.map((m) => m.video?.name ?? null)).toEqual([
			"Meet – Call",
			null,
		]);
	});

	it("refuses actions from anyone but the owner without calling the bridge", async () => {
		mocks.user = { id: "someone-else" };
		expect(await syncMeetBotCalendar()).toEqual({
			ok: false,
			error: "Accesso riservato al proprietario.",
		});
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it("reports bridge errors in Italian and never the token", async () => {
		fetchMock.mockResolvedValue(
			json({ ok: false, error: "Google Calendar ha risposto HTTP 500." }, 400),
		);
		const result = await syncMeetBotCalendar();
		expect(result).toEqual({
			ok: false,
			error: "Google Calendar ha risposto HTTP 500.",
		});
		fetchMock.mockRejectedValue(new Error("connect ECONNREFUSED bridge-token"));
		const offline = await syncMeetBotCalendar();
		expect(offline).toEqual({
			ok: false,
			error: "Il Meet bot non risponde, riprova tra poco.",
		});
	});

	it("toggles an event only when its state differs", async () => {
		fetchMock.mockImplementation(async (input) =>
			String(input).endsWith("/api/calendar")
				? json({ events: [{ id: "0123456789abcdef", enabled: true }] })
				: json({ ok: true, enabled: false }),
		);
		expect((await setMeetBotEventEnabled("0123456789abcdef", true)).ok).toBe(
			true,
		);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect((await setMeetBotEventEnabled("0123456789abcdef", false)).ok).toBe(
			true,
		);
		expect(String(fetchMock.mock.calls.at(-1)?.[0])).toBe(
			"http://meet-bridge:8080/meetbot/api/calendar/toggle/0123456789abcdef",
		);
		expect(mocks.revalidatePath).toHaveBeenCalledWith("/dashboard/meet-bot");
		expect(
			(await setMeetBotEventEnabled("../google/disconnect", false)).ok,
		).toBe(false);
	});

	it("validates the Meet link before sending a bot", async () => {
		expect((await sendMeetBot("https://zoom.us/j/1", "")).ok).toBe(false);
		expect(fetchMock).not.toHaveBeenCalled();
		fetchMock.mockResolvedValue(json({ ok: true, bot_id: "bot_9" }));
		expect(
			await sendMeetBot(" https://meet.google.com/abc-defg-hij ", " Demo "),
		).toEqual({
			ok: true,
			message: "Bot inviato: tra poco chiede di entrare nel Meet, ammettilo.",
		});
		const [url, init] = fetchMock.mock.calls[0] ?? [];
		expect(String(url)).toBe("http://meet-bridge:8080/meetbot/api/join");
		expect(JSON.parse(String(init?.body))).toEqual({
			meeting_url: "https://meet.google.com/abc-defg-hij",
			title: "Demo",
		});
	});
});
