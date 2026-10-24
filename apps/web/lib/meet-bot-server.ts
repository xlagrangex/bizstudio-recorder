import "server-only";

import { db } from "@cap/database";
import { getCurrentUser } from "@cap/database/auth/session";
import { videos } from "@cap/database/schema";
import { serverEnv } from "@cap/env";
import { User, type Video } from "@cap/web-domain";
import { and, eq, inArray } from "drizzle-orm";
import type {
	MeetBotCalendar,
	MeetBotMeeting,
	RecordedMeeting,
} from "./meet-bot";

export class MeetBotError extends Error {}

export async function requireMeetBotOwner() {
	const user = await getCurrentUser();
	const ownerId = serverEnv().MEETBOT_OWNER_ID;
	if (!user || !ownerId || user.id !== ownerId) return null;
	return user;
}

export function meetBotConfigured() {
	const env = serverEnv();
	return Boolean(env.MEETBOT_API_URL && env.MEETBOT_API_TOKEN);
}

// The bridge token grants full control of the bot, so it is only ever sent from
// the server and the bridge is reached over the internal docker network.
export async function bridgeRequest<T>(
	path: string,
	init: { method?: "GET" | "POST"; body?: unknown } = {},
): Promise<T> {
	const env = serverEnv();
	if (!env.MEETBOT_API_URL || !env.MEETBOT_API_TOKEN)
		throw new MeetBotError("Meet bot non configurato su questo server.");
	let response: Response;
	try {
		response = await fetch(
			`${env.MEETBOT_API_URL.replace(/\/$/, "")}/api${path}`,
			{
				method: init.method ?? "GET",
				headers: {
					Authorization: `Bearer ${env.MEETBOT_API_TOKEN}`,
					"Content-Type": "application/json",
				},
				body:
					init.method === "POST" ? JSON.stringify(init.body ?? {}) : undefined,
				cache: "no-store",
				signal: AbortSignal.timeout(45_000),
			},
		);
	} catch {
		throw new MeetBotError("Il Meet bot non risponde, riprova tra poco.");
	}
	const data = (await response.json().catch(() => null)) as
		| (T & { error?: string })
		| null;
	if (!response.ok || data === null)
		throw new MeetBotError(
			typeof data?.error === "string"
				? data.error
				: `Il Meet bot ha risposto con un errore (${response.status}).`,
		);
	return data;
}

export async function loadMeetBotOverview(ownerId: string) {
	const [calendar, meetings] = await Promise.all([
		bridgeRequest<MeetBotCalendar>("/calendar?notice=1"),
		bridgeRequest<MeetBotMeeting[]>("/meetings"),
	]);
	const videoIds = meetings
		.map((meeting) => meeting.video_id)
		.filter((id): id is string => Boolean(id));
	const rows =
		videoIds.length === 0
			? []
			: await db()
					.select({
						id: videos.id,
						name: videos.name,
						duration: videos.duration,
						transcriptionStatus: videos.transcriptionStatus,
					})
					.from(videos)
					.where(
						and(
							inArray(videos.id, videoIds as Video.VideoId[]),
							eq(videos.ownerId, User.UserId.make(ownerId)),
						),
					);
	const byId = new Map(rows.map((row) => [row.id as string, row]));
	const recorded: RecordedMeeting[] = meetings.map((meeting) => {
		const video = meeting.video_id ? byId.get(meeting.video_id) : undefined;
		return {
			...meeting,
			video: video
				? {
						id: video.id,
						name: video.name,
						duration: video.duration,
						transcriptionStatus: video.transcriptionStatus,
					}
				: null,
		};
	});
	return { calendar, recorded };
}
