import { Card, CardDescription, CardTitle } from "@cap/ui";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import {
	loadMeetBotOverview,
	MeetBotError,
	meetBotConfigured,
	requireMeetBotOwner,
} from "@/lib/meet-bot-server";
import { MeetBot } from "./MeetBot";

export const metadata: Metadata = {
	title: "Meet Bot — Bizstudio Recorder",
};

function Unavailable({ message }: { message: string }) {
	return (
		<Card className="space-y-1">
			<CardTitle>Meet Bot non disponibile</CardTitle>
			<CardDescription>{message}</CardDescription>
		</Card>
	);
}

export default async function MeetBotPage() {
	const user = await requireMeetBotOwner();
	if (!user) notFound();
	if (!meetBotConfigured())
		return (
			<Unavailable message="Il Meet bot non è configurato su questo server." />
		);

	let overview: Awaited<ReturnType<typeof loadMeetBotOverview>>;
	try {
		overview = await loadMeetBotOverview(user.id);
	} catch (error) {
		return (
			<Unavailable
				message={
					error instanceof MeetBotError
						? error.message
						: "Impossibile leggere lo stato del Meet bot, riprova tra poco."
				}
			/>
		);
	}

	return (
		<MeetBot
			calendar={overview.calendar}
			recorded={overview.recorded}
			now={Math.floor(Date.now() / 1000)}
		/>
	);
}
