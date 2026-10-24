"use server";

import { revalidatePath } from "next/cache";
import { isMeetLink } from "@/lib/meet-bot";
import {
	bridgeRequest,
	MeetBotError,
	requireMeetBotOwner,
} from "@/lib/meet-bot-server";

export type MeetBotActionResult =
	| { ok: true; message: string }
	| { ok: false; error: string };

async function run(
	action: () => Promise<string>,
): Promise<MeetBotActionResult> {
	if (!(await requireMeetBotOwner()))
		return { ok: false, error: "Accesso riservato al proprietario." };
	try {
		const message = await action();
		revalidatePath("/dashboard/meet-bot");
		return { ok: true, message };
	} catch (error) {
		return {
			ok: false,
			error:
				error instanceof MeetBotError
					? error.message
					: "Operazione non riuscita, riprova.",
		};
	}
}

export async function setMeetBotEventEnabled(
	eventId: string,
	enabled: boolean,
) {
	return run(async () => {
		if (!/^[0-9a-f]{16}$/.test(eventId))
			throw new MeetBotError("Riunione non trovata.");
		const current = await bridgeRequest<{
			events: { id: string; enabled: boolean }[];
		}>("/calendar");
		const event = current.events.find((item) => item.id === eventId);
		if (!event) throw new MeetBotError("Riunione non trovata.");
		if (event.enabled !== enabled)
			await bridgeRequest(`/calendar/toggle/${eventId}`, { method: "POST" });
		return enabled
			? "Il bot entrerà in questa riunione."
			: "Il bot non entrerà in questa riunione.";
	});
}

export async function setMeetBotAutojoin(enabled: boolean) {
	return run(async () => {
		await bridgeRequest("/calendar/autojoin", {
			method: "POST",
			body: { enabled },
		});
		return enabled
			? "Ingresso automatico acceso."
			: "Ingresso automatico spento: nessun bot partirà dal calendario.";
	});
}

export async function syncMeetBotCalendar() {
	return run(async () => {
		await bridgeRequest("/calendar/sync", { method: "POST" });
		return "Calendario sincronizzato.";
	});
}

export async function disconnectMeetBotGoogle() {
	return run(async () => {
		const result = await bridgeRequest<{ revoked: boolean }>(
			"/google/disconnect",
			{
				method: "POST",
			},
		);
		return result.revoked
			? "Google Calendar scollegato."
			: "Scollegato. Google non ha confermato la revoca: toglila da myaccount.google.com/permissions.";
	});
}

export async function sendMeetBot(meetingUrl: string, title: string) {
	return run(async () => {
		if (!isMeetLink(meetingUrl))
			throw new MeetBotError(
				"Link non valido: incolla il link del Meet (https://meet.google.com/abc-defg-hij).",
			);
		await bridgeRequest("/join", {
			method: "POST",
			body: {
				meeting_url: meetingUrl.trim(),
				title: title.trim().slice(0, 120),
			},
		});
		return "Bot inviato: tra poco chiede di entrare nel Meet, ammettilo.";
	});
}
