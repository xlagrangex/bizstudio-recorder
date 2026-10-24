export const MEET_BOT_TIME_ZONE = "Europe/Rome";
export const MEET_BOT_CONNECT_PATH = "/meetbot/google/connect";

export type MeetBotEvent = {
	id: string;
	title: string;
	start: number;
	end: number;
	meeting_url: string;
	enabled: boolean;
	status: "pending" | "launched" | "missed" | "failed" | "gone";
	bot_id: string | null;
	error: string | null;
	bot_stage: string | null;
	bot_state: string | null;
	video_id: string | null;
};

export type MeetBotCalendar = {
	notice: { message: string; ok: boolean } | null;
	configured: boolean;
	connected: boolean;
	account: string | null;
	autojoin: boolean;
	synced_at: number | null;
	sync_error: string | null;
	events: MeetBotEvent[];
};

export type MeetBotMeeting = {
	bot_id: string;
	title: string | null;
	meeting_url: string;
	stage: "active" | "importing" | "imported" | "failed" | "no_recording";
	attendee_state: string | null;
	video_id: string | null;
	error: string | null;
	label: string;
	created_at: number;
	updated_at: number;
	source: "calendar" | "manual";
	scheduled_start: number | null;
};

export type RecordedVideo = {
	id: string;
	name: string;
	duration: number | null;
	transcriptionStatus: string | null;
};

export type RecordedMeeting = MeetBotMeeting & { video: RecordedVideo | null };

export type Tone =
	| "neutral"
	| "info"
	| "live"
	| "success"
	| "warning"
	| "error";

export type StatusBadge = { label: string; tone: Tone; detail?: string };

const IN_MEETING = new Set([
	"joined_recording",
	"joined_not_recording",
	"joined_recording_paused",
]);

export function eventStatus(
	event: MeetBotEvent,
	autojoin: boolean,
): StatusBadge {
	switch (event.status) {
		case "missed":
			return {
				label: "Saltato",
				tone: "warning",
				detail: event.error ?? undefined,
			};
		case "failed":
			return {
				label: "Fallito",
				tone: "error",
				detail: event.error ?? undefined,
			};
		case "launched": {
			if (event.bot_stage === "failed" || event.bot_stage === "no_recording")
				return {
					label: "Fallito",
					tone: "error",
					detail: event.error ?? undefined,
				};
			if (event.bot_stage === "importing" || event.bot_stage === "imported")
				return { label: "Registrata", tone: "success" };
			if (event.bot_state && IN_MEETING.has(event.bot_state))
				return { label: "In corso", tone: "live" };
			if (event.bot_state === "waiting_room")
				return {
					label: "Bot in arrivo",
					tone: "info",
					detail: "È in sala d'attesa: ammettilo dal Meet",
				};
			return { label: "Bot in arrivo", tone: "info" };
		}
		default:
			if (!autojoin)
				return { label: "Ingresso automatico spento", tone: "neutral" };
			if (!event.enabled) return { label: "Il bot non entra", tone: "neutral" };
			return {
				label: "Programmato",
				tone: "info",
				detail: event.error ?? undefined,
			};
	}
}

export function meetingStatus(meeting: RecordedMeeting): StatusBadge {
	switch (meeting.stage) {
		case "active":
			return IN_MEETING.has(meeting.attendee_state ?? "")
				? { label: "In corso", tone: "live" }
				: { label: "Bot in arrivo", tone: "info", detail: meeting.label };
		case "importing":
			return { label: "In elaborazione", tone: "info" };
		case "no_recording":
			return {
				label: "Nessuna registrazione",
				tone: "neutral",
				detail: meeting.error ?? undefined,
			};
		case "failed":
			return {
				label: "Errore",
				tone: "error",
				detail: meeting.error ?? undefined,
			};
		default:
			break;
	}
	if (!meeting.video) return { label: "Video eliminato", tone: "neutral" };
	switch (meeting.video.transcriptionStatus) {
		case "COMPLETE":
			return { label: "Trascritto", tone: "success" };
		case "ERROR":
			return { label: "Errore trascrizione", tone: "error" };
		case "NO_AUDIO":
			return { label: "Senza audio", tone: "warning" };
		case "SKIPPED":
			return { label: "Non trascritto", tone: "neutral" };
		default:
			return { label: "In elaborazione", tone: "info" };
	}
}

const dayKey = new Intl.DateTimeFormat("en-CA", {
	timeZone: MEET_BOT_TIME_ZONE,
	year: "numeric",
	month: "2-digit",
	day: "2-digit",
});
const dayName = new Intl.DateTimeFormat("it-IT", {
	timeZone: MEET_BOT_TIME_ZONE,
	weekday: "long",
	day: "numeric",
	month: "long",
});
const clock = new Intl.DateTimeFormat("it-IT", {
	timeZone: MEET_BOT_TIME_ZONE,
	hour: "2-digit",
	minute: "2-digit",
});
const shortDate = new Intl.DateTimeFormat("it-IT", {
	timeZone: MEET_BOT_TIME_ZONE,
	day: "numeric",
	month: "short",
	year: "numeric",
});

const capitalize = (text: string) =>
	text.charAt(0).toUpperCase() + text.slice(1);

function dayLabel(seconds: number, nowSeconds: number) {
	const key = dayKey.format(seconds * 1000);
	if (key === dayKey.format(nowSeconds * 1000)) return "Oggi";
	if (key === dayKey.format((nowSeconds + 86400) * 1000)) return "Domani";
	return capitalize(dayName.format(seconds * 1000));
}

export function groupByDay<T extends { start: number }>(
	events: T[],
	nowSeconds: number,
): { key: string; label: string; events: T[] }[] {
	const groups: { key: string; label: string; events: T[] }[] = [];
	for (const event of [...events].sort((a, b) => a.start - b.start)) {
		// A meeting already running belongs to today even if it started yesterday.
		const anchor = Math.max(
			event.start,
			Math.min(nowSeconds, event.start + 86400),
		);
		const key = dayKey.format(anchor * 1000);
		const last = groups.at(-1);
		if (last?.key === key) last.events.push(event);
		else
			groups.push({
				key,
				label: dayLabel(anchor, nowSeconds),
				events: [event],
			});
	}
	return groups;
}

export function timeRange(start: number, end: number) {
	return `${clock.format(start * 1000)} – ${clock.format(end * 1000)}`;
}

export function formatClock(seconds: number) {
	return clock.format(seconds * 1000);
}

export function formatDate(seconds: number, nowSeconds: number) {
	const label = dayLabel(seconds, nowSeconds);
	const time = clock.format(seconds * 1000);
	if (label === "Oggi" || label === "Domani") return `${label}, ${time}`;
	return `${shortDate.format(seconds * 1000)}, ${time}`;
}

export function formatDuration(seconds: number | null | undefined) {
	if (!seconds || seconds < 1) return null;
	const total = Math.round(seconds);
	const hours = Math.floor(total / 3600);
	const minutes = Math.floor((total % 3600) / 60);
	if (hours > 0)
		return minutes > 0 ? `${hours} h ${minutes} min` : `${hours} h`;
	if (minutes > 0) return `${minutes} min`;
	return `${total} s`;
}

export function meetCode(url: string) {
	return url.replace(/^https?:\/\//, "");
}

export function isMeetLink(value: string) {
	return /^https:\/\/meet\.google\.com\/[a-z]{3,4}-[a-z]{4}-[a-z]{3,4}(\?.*)?$/.test(
		value.trim(),
	);
}
