"use client";

import {
	Button,
	Card,
	CardDescription,
	CardTitle,
	Input,
	Switch,
} from "@cap/ui";
import type { Video } from "@cap/web-domain";
import clsx from "clsx";
import { ExternalLink, RefreshCw, Video as VideoIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, useTransition } from "react";
import { toast } from "sonner";
import {
	disconnectMeetBotGoogle,
	type MeetBotActionResult,
	sendMeetBot,
	setMeetBotAutojoin,
	setMeetBotEventEnabled,
	syncMeetBotCalendar,
} from "@/actions/meet-bot";
import {
	type ImageLoadingStatus,
	VideoThumbnail,
} from "@/components/VideoThumbnail";
import {
	eventStatus,
	formatClock,
	formatDate,
	formatDuration,
	groupByDay,
	MEET_BOT_CONNECT_PATH,
	type MeetBotCalendar,
	type MeetBotEvent,
	meetCode,
	meetingStatus,
	type RecordedMeeting,
	type StatusBadge,
	timeRange,
} from "@/lib/meet-bot";
import { GoogleCalendarIcon } from "./GoogleCalendarIcon";

const REFRESH_MS = 30_000;

const TONES: Record<StatusBadge["tone"], string> = {
	neutral: "bg-gray-3 text-gray-11",
	info: "bg-blue-500/10 text-blue-500",
	live: "bg-red-300/15 text-red-400",
	success: "bg-green-500/10 text-green-600",
	warning: "bg-amber-500/10 text-amber-600",
	error: "bg-red-300/15 text-red-400",
};

function Badge({ status }: { status: StatusBadge }) {
	return (
		<span
			className={clsx(
				"inline-flex gap-1.5 items-center px-2 py-0.5 text-xs font-medium whitespace-nowrap rounded-full",
				TONES[status.tone],
			)}
		>
			{status.tone === "live" && (
				<span className="relative flex size-1.5">
					<span className="absolute inline-flex w-full h-full bg-current rounded-full opacity-75 animate-ping" />
					<span className="relative inline-flex rounded-full size-1.5 bg-current" />
				</span>
			)}
			{status.label}
		</span>
	);
}

function useAction() {
	const router = useRouter();
	const [pending, startTransition] = useTransition();
	const run = (
		action: () => Promise<MeetBotActionResult>,
		onDone?: (ok: boolean) => void,
	) =>
		startTransition(async () => {
			const result = await action();
			if (result.ok) toast.success(result.message);
			else toast.error(result.error);
			onDone?.(result.ok);
			router.refresh();
		});
	return [pending, run] as const;
}

export function MeetBot({
	calendar,
	recorded,
	now,
}: {
	calendar: MeetBotCalendar;
	recorded: RecordedMeeting[];
	now: number;
}) {
	const router = useRouter();

	useEffect(() => {
		if (calendar.notice) {
			if (calendar.notice.ok) toast.success(calendar.notice.message);
			else toast.error(calendar.notice.message);
		}
	}, [calendar.notice]);

	useEffect(() => {
		const timer = window.setInterval(() => router.refresh(), REFRESH_MS);
		return () => window.clearInterval(timer);
	}, [router]);

	return (
		<div className="flex flex-col gap-8 pb-10">
			<CalendarHeader calendar={calendar} />
			<UpcomingMeetings calendar={calendar} now={now} />
			<RecordedMeetings recorded={recorded} now={now} />
			<SendBot />
		</div>
	);
}

function CalendarHeader({ calendar }: { calendar: MeetBotCalendar }) {
	const [pending, run] = useAction();
	const autojoinId = useId();
	const [autojoin, setAutojoin] = useState(calendar.autojoin);
	useEffect(() => setAutojoin(calendar.autojoin), [calendar.autojoin]);

	const synced = calendar.synced_at ? formatClock(calendar.synced_at) : null;

	return (
		<Card className="flex flex-col gap-5">
			<div className="flex flex-col gap-4 justify-between md:flex-row md:items-start">
				<div className="space-y-1">
					<CardTitle>Meet Bot</CardTitle>
					<CardDescription>
						Il bot entra da solo nei Meet del tuo calendario un minuto prima
						dell'inizio, registra e salva il video tra i tuoi, privato.
					</CardDescription>
				</div>
				{calendar.connected && (
					<div className="flex flex-wrap gap-2 items-center md:justify-end">
						<Button
							variant="gray"
							size="sm"
							disabled={pending}
							onClick={() => run(syncMeetBotCalendar)}
							icon={
								<RefreshCw
									className={clsx("size-3.5", pending && "animate-spin")}
								/>
							}
						>
							Sincronizza ora
						</Button>
					</div>
				)}
			</div>

			{calendar.connected ? (
				<div className="flex flex-col gap-4 pt-5 border-t border-gray-3 sm:flex-row sm:items-center sm:justify-between">
					<div className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 items-center min-w-0 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
						<GoogleCalendarIcon className="size-8 shrink-0" />
						<div className="min-w-0">
							<p className="flex flex-col text-sm text-gray-12 sm:flex-row sm:gap-1">
								<span className="shrink-0">Collegato come</span>
								<span
									className="font-medium truncate"
									title={calendar.account ?? undefined}
								>
									{calendar.account ?? "account Google"}
								</span>
							</p>
							<p
								className={clsx(
									"text-xs",
									calendar.sync_error ? "text-red-400" : "text-gray-10",
								)}
							>
								{calendar.sync_error
									? calendar.sync_error
									: synced
										? `Calendario letto alle ${synced}, si aggiorna ogni 5 minuti`
										: "In attesa della prima lettura del calendario"}
							</p>
						</div>
						<Button
							variant="transparent"
							size="xs"
							className="col-start-2 justify-self-start px-2 mt-1 -ml-2 sm:col-start-3 sm:mt-0 sm:ml-2"
							disabled={pending}
							onClick={() => {
								if (
									window.confirm(
										"Scollegare Google Calendar? Il bot smetterà di entrare da solo nelle riunioni.",
									)
								)
									run(disconnectMeetBotGoogle);
							}}
						>
							Scollega
						</Button>
					</div>
					<label
						htmlFor={autojoinId}
						className="flex gap-3 justify-between items-center px-4 py-2.5 rounded-xl border cursor-pointer border-gray-3 bg-gray-2 sm:justify-start"
					>
						<span className="text-sm font-medium text-gray-12">
							Ingresso automatico
						</span>
						<Switch
							id={autojoinId}
							checked={autojoin}
							disabled={pending}
							onCheckedChange={(checked) => {
								setAutojoin(checked);
								run(
									() => setMeetBotAutojoin(checked),
									(ok) => !ok && setAutojoin(!checked),
								);
							}}
						/>
					</label>
				</div>
			) : (
				<div className="flex flex-col gap-3 items-start pt-5 border-t border-gray-3">
					{calendar.sync_error && (
						<p className="text-sm text-red-400">{calendar.sync_error}</p>
					)}
					{calendar.configured ? (
						<a
							href={MEET_BOT_CONNECT_PATH}
							className="inline-flex gap-3 items-center h-11 pr-5 pl-3.5 text-sm font-medium tracking-[0.25px] rounded-full border transition-colors font-[Roboto,'Google_Sans',Arial,sans-serif] bg-gray-1 text-gray-12 border-gray-7 hover:bg-gray-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500"
						>
							<GoogleCalendarIcon className="size-[22px]" />
							Collega il tuo account Google Calendar
						</a>
					) : (
						<p className="text-sm text-gray-10">
							Prima configura le credenziali Google dalla{" "}
							<a href="/meetbot/" className="underline text-gray-12">
								pagina di configurazione del bot
							</a>
							.
						</p>
					)}
				</div>
			)}
		</Card>
	);
}

function SectionTitle({
	title,
	description,
	count,
}: {
	title: string;
	description?: string;
	count?: number;
}) {
	return (
		<div className="mb-3 space-y-1">
			<h2 className="flex gap-2 items-center text-lg font-medium text-gray-12">
				{title}
				{count !== undefined && count > 0 && (
					<span className="px-2 py-0.5 text-xs font-medium rounded-full bg-gray-3 text-gray-11">
						{count}
					</span>
				)}
			</h2>
			{description && <p className="text-sm text-gray-10">{description}</p>}
		</div>
	);
}

function UpcomingMeetings({
	calendar,
	now,
}: {
	calendar: MeetBotCalendar;
	now: number;
}) {
	if (!calendar.connected) return null;
	const groups = groupByDay(calendar.events, now);
	return (
		<section>
			<SectionTitle
				title="Prossime riunioni"
				description="I Meet in agenda nei prossimi 7 giorni. Il bot salta quelli rifiutati, quelli tutto il giorno e quelli con #nobot nel titolo o nella descrizione."
				count={calendar.events.length}
			/>
			{groups.length === 0 ? (
				<Card className="py-10 text-center">
					<CardDescription>Nessun Meet nei prossimi 7 giorni.</CardDescription>
				</Card>
			) : (
				<div className="flex flex-col gap-5">
					{groups.map((group) => (
						<div key={group.key}>
							<p className="mb-2 text-xs font-medium tracking-wide uppercase text-gray-10">
								{group.label}
							</p>
							<Card className="p-0 divide-y divide-gray-3">
								{group.events.map((event) => (
									<UpcomingRow
										key={event.id}
										event={event}
										autojoin={calendar.autojoin}
									/>
								))}
							</Card>
						</div>
					))}
				</div>
			)}
		</section>
	);
}

function UpcomingRow({
	event,
	autojoin,
}: {
	event: MeetBotEvent;
	autojoin: boolean;
}) {
	const [pending, run] = useAction();
	const [enabled, setEnabled] = useState(event.enabled);
	useEffect(() => setEnabled(event.enabled), [event.enabled]);
	const status = eventStatus({ ...event, enabled }, autojoin);
	const canToggle = event.status === "pending";

	return (
		<div className="flex gap-4 items-center px-4 py-4 sm:px-5">
			<div className="flex-1 min-w-0 sm:flex sm:gap-4 sm:items-center">
				<div className="mb-1 text-xs font-medium whitespace-nowrap tabular-nums text-gray-11 sm:mb-0 sm:w-[108px] sm:shrink-0 sm:text-sm sm:font-normal sm:text-gray-12">
					{timeRange(event.start, event.end)}
				</div>
				<div className="flex-1 min-w-0">
					<p className="text-sm font-medium truncate text-gray-12">
						{event.title}
					</p>
					<div className="flex flex-wrap gap-x-3 gap-y-1 items-center mt-1">
						<Badge status={status} />
						<a
							href={event.meeting_url}
							target="_blank"
							rel="noreferrer"
							className="inline-flex gap-1 items-center text-xs truncate text-gray-10 hover:text-gray-12"
						>
							{meetCode(event.meeting_url)}
							<ExternalLink className="size-3" />
						</a>
					</div>
					{status.detail && (
						<p
							className={clsx(
								"mt-1 text-xs",
								status.tone === "error" || status.tone === "warning"
									? "text-red-400"
									: "text-gray-10",
							)}
						>
							{status.detail}
						</p>
					)}
				</div>
			</div>
			{canToggle && (
				<Switch
					checked={enabled}
					disabled={pending}
					aria-label={`Bot per ${event.title}`}
					onCheckedChange={(checked) => {
						setEnabled(checked);
						run(
							() => setMeetBotEventEnabled(event.id, checked),
							(ok) => !ok && setEnabled(!checked),
						);
					}}
				/>
			)}
		</div>
	);
}

function RecordedMeetings({
	recorded,
	now,
}: {
	recorded: RecordedMeeting[];
	now: number;
}) {
	return (
		<section>
			<SectionTitle
				title="Riunioni registrate"
				description="Le riunioni in cui è entrato il bot, dalla più recente. I video sono privati finché non li condividi."
				count={recorded.length}
			/>
			{recorded.length === 0 ? (
				<Card className="py-10 text-center">
					<CardDescription>
						Il bot non ha ancora registrato riunioni.
					</CardDescription>
				</Card>
			) : (
				<div className="grid grid-cols-1 gap-4 sm:gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
					{recorded.map((meeting) => (
						<RecordedCard key={meeting.bot_id} meeting={meeting} now={now} />
					))}
				</div>
			)}
		</section>
	);
}

function RecordedCard({
	meeting,
	now,
}: {
	meeting: RecordedMeeting;
	now: number;
}) {
	const [imageStatus, setImageStatus] = useState<ImageLoadingStatus>("loading");
	const status = meetingStatus(meeting);
	const title =
		meeting.video?.name ?? meeting.title ?? meetCode(meeting.meeting_url);
	const duration = formatDuration(meeting.video?.duration);
	const href = meeting.video ? `/s/${meeting.video.id}` : null;

	const thumbnail = meeting.video ? (
		<div className="relative">
			<VideoThumbnail
				videoId={meeting.video.id as Video.VideoId}
				alt={title}
				imageStatus={imageStatus}
				setImageStatus={setImageStatus}
				showPreview={false}
			/>
			{duration && (
				<span className="absolute right-2 bottom-2 z-20 px-1.5 py-0.5 text-[11px] font-medium text-white rounded-md bg-black/70">
					{duration}
				</span>
			)}
		</div>
	) : (
		<div className="flex justify-center items-center w-full rounded-t-xl border-b aspect-video bg-gray-3 border-gray-3">
			<VideoIcon className="size-7 text-gray-8" />
		</div>
	);

	const body = (
		<>
			{thumbnail}
			<div className="flex flex-col flex-1 gap-2 p-4">
				<p className="text-sm font-medium leading-snug line-clamp-2 text-gray-12">
					{title}
				</p>
				<p className="text-xs text-gray-10">
					{formatDate(meeting.scheduled_start ?? meeting.created_at, now)}
					{meeting.source === "calendar" ? " · dal calendario" : " · manuale"}
				</p>
				<div className="mt-auto">
					<Badge status={status} />
					{status.detail && (
						<p className="mt-1.5 text-xs text-gray-10 line-clamp-2">
							{status.detail}
						</p>
					)}
				</div>
			</div>
		</>
	);

	const className =
		"flex overflow-hidden flex-col rounded-xl border bg-gray-1 border-gray-3 transition-colors";
	return href ? (
		<Link href={href} className={clsx(className, "hover:border-gray-6")}>
			{body}
		</Link>
	) : (
		<div className={className}>{body}</div>
	);
}

function SendBot() {
	const [pending, run] = useAction();
	const [url, setUrl] = useState("");
	const [title, setTitle] = useState("");

	return (
		<Card className="flex flex-col gap-4">
			<div className="space-y-1">
				<CardTitle>Invia bot a una riunione</CardTitle>
				<CardDescription>
					Per un Meet che non è in calendario. Il bot entra come ospite:
					ammettilo dal Meet quando chiede di partecipare.
				</CardDescription>
			</div>
			<form
				className="flex flex-col gap-3 md:flex-row"
				onSubmit={(e) => {
					e.preventDefault();
					run(
						() => sendMeetBot(url, title),
						(ok) => {
							if (ok) {
								setUrl("");
								setTitle("");
							}
						},
					);
				}}
			>
				<Input
					type="url"
					required
					value={url}
					onChange={(e) => setUrl(e.target.value)}
					placeholder="https://meet.google.com/abc-defg-hij"
					aria-label="Link del Meet"
					autoComplete="off"
					className="md:flex-[2]"
				/>
				<Input
					value={title}
					onChange={(e) => setTitle(e.target.value)}
					placeholder="Titolo (facoltativo)"
					aria-label="Titolo"
					maxLength={120}
					className="md:flex-1"
				/>
				<Button
					type="submit"
					variant="primary"
					disabled={pending || !url.trim()}
					spinner={pending}
					className="shrink-0"
				>
					Manda il bot
				</Button>
			</form>
		</Card>
	);
}
