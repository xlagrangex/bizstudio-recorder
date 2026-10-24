export type ExportGroup = "video" | "audio" | "transcript";

export type RenderedFormat =
	| "mp4-1080"
	| "mp4-720"
	| "webm"
	| "gif"
	| "mp3"
	| "wav";
export type TranscriptFormat = "srt" | "vtt" | "txt";

type FormatSpec = {
	id: string;
	group: ExportGroup;
	label: string;
	detail: string;
	extension: string;
	contentType: string;
};

/** Rendered by the worker from result.mp4, under `<owner>/<video>/exports/`. */
export const RENDERED_FORMATS: Record<
	RenderedFormat,
	FormatSpec & { file: string }
> = {
	"mp4-1080": {
		id: "mp4-1080",
		group: "video",
		label: "MP4 1080p",
		detail: "Full HD, for YouTube and presentations",
		extension: "mp4",
		contentType: "video/mp4",
		file: "video-1080p.mp4",
	},
	"mp4-720": {
		id: "mp4-720",
		group: "video",
		label: "MP4 720p",
		detail: "Lighter, for email and chat",
		extension: "mp4",
		contentType: "video/mp4",
		file: "video-720p.mp4",
	},
	webm: {
		id: "webm",
		group: "video",
		label: "WebM",
		detail: "For websites",
		extension: "webm",
		contentType: "video/webm",
		file: "video.webm",
	},
	gif: {
		id: "gif",
		group: "video",
		label: "GIF",
		detail: "The first 30 seconds, no sound",
		extension: "gif",
		contentType: "image/gif",
		file: "clip.gif",
	},
	mp3: {
		id: "mp3",
		group: "audio",
		label: "MP3",
		detail: "Audio only, compressed",
		extension: "mp3",
		contentType: "audio/mpeg",
		file: "audio.mp3",
	},
	wav: {
		id: "wav",
		group: "audio",
		label: "WAV",
		detail: "Audio only, uncompressed",
		extension: "wav",
		contentType: "audio/wav",
		file: "audio.wav",
	},
};

export const TRANSCRIPT_FORMATS: Record<TranscriptFormat, FormatSpec> = {
	srt: {
		id: "srt",
		group: "transcript",
		label: "SRT",
		detail: "Subtitles for Premiere, DaVinci, YouTube",
		extension: "srt",
		contentType: "application/x-subrip; charset=utf-8",
	},
	vtt: {
		id: "vtt",
		group: "transcript",
		label: "VTT",
		detail: "Subtitles for the web",
		extension: "vtt",
		contentType: "text/vtt; charset=utf-8",
	},
	txt: {
		id: "txt",
		group: "transcript",
		label: "Text",
		detail: "Plain transcript, no timestamps",
		extension: "txt",
		contentType: "text/plain; charset=utf-8",
	},
};

export const ORIGINAL_FILE = "original";
const SOURCE_PREFIX = "source:";

export type ExportState = "ready" | "pending" | "missing" | "failed";

export type ExportItem = {
	file: string;
	group: ExportGroup | "sources";
	label: string;
	detail: string;
	state: ExportState;
	size?: number;
	error?: string;
	/** Only rendered formats can be requested; the rest are ready or absent. */
	renderable: boolean;
};

export type SourcesManifest = {
	version: 1;
	files: Array<{ name: string; kind: string; label: string; size: number }>;
};

export const isRenderedFormat = (file: string): file is RenderedFormat =>
	Object.hasOwn(RENDERED_FORMATS, file);

export const isTranscriptFormat = (file: string): file is TranscriptFormat =>
	Object.hasOwn(TRANSCRIPT_FORMATS, file);

export const sourceFileId = (name: string) => `${SOURCE_PREFIX}${name}`;

const SOURCE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

export const sourceNameOf = (file: string) => {
	if (!file.startsWith(SOURCE_PREFIX)) return null;
	const name = file.slice(SOURCE_PREFIX.length);
	return SOURCE_NAME.test(name) && name !== "manifest.json" ? name : null;
};

export const parseSourcesManifest = (raw: string): SourcesManifest | null => {
	try {
		const parsed = JSON.parse(raw) as Partial<SourcesManifest>;
		if (parsed.version !== 1 || !Array.isArray(parsed.files)) return null;
		const files = parsed.files.filter(
			(entry): entry is SourcesManifest["files"][number] =>
				typeof entry?.name === "string" &&
				SOURCE_NAME.test(entry.name) &&
				entry.name !== "manifest.json" &&
				typeof entry.label === "string" &&
				typeof entry.size === "number",
		);
		return { version: 1, files };
	} catch {
		return null;
	}
};

export const exportQueueKey = (videoId: string, format: RenderedFormat) =>
	`export-queue/${videoId}--${format}.json`;

/** `Content-Disposition` with an ASCII fallback, so accents survive in modern browsers. */
export const attachmentDisposition = (filename: string) => {
	const ascii = filename
		.normalize("NFKD")
		.replace(/[^\x20-\x7e]/g, "")
		.replace(/["\\/]/g, "")
		.trim();
	return `attachment; filename="${ascii || "download"}"; filename*=UTF-8''${encodeURIComponent(filename)}`;
};

export const downloadFilename = (videoName: string, suffix: string) => {
	const base =
		videoName
			.replace(/[\\/:*?"<>|]+/g, " ")
			.replace(/\s+/g, " ")
			.trim() || "video";
	return `${base}${suffix}`;
};

type Cue = { start: string; end: string; text: string };

const TIMING =
	/^((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})/;

const parseVtt = (vtt: string): Cue[] => {
	const cues: Cue[] = [];
	for (const block of vtt.replace(/\r/g, "").split(/\n{2,}/)) {
		const lines = block.split("\n");
		const timingIndex = lines.findIndex((line) => TIMING.test(line));
		if (timingIndex === -1) continue;
		const match = lines[timingIndex]?.match(TIMING);
		const text = lines
			.slice(timingIndex + 1)
			.join("\n")
			.trim();
		if (match?.[1] && match[2] && text)
			cues.push({ start: match[1], end: match[2], text });
	}
	return cues;
};

const srtTime = (time: string) => {
	const parts = time.replace(",", ".").split(":");
	const hours = parts.length === 3 ? parts[0] : "0";
	const [minutes, seconds] = parts.slice(-2);
	return `${hours?.padStart(2, "0")}:${minutes?.padStart(2, "0")}:${seconds?.replace(".", ",")}`;
};

export const vttToSrt = (vtt: string) =>
	parseVtt(vtt)
		.map(
			(cue, index) =>
				`${index + 1}\n${srtTime(cue.start)} --> ${srtTime(cue.end)}\n${cue.text}\n`,
		)
		.join("\n");

export const vttToText = (vtt: string) =>
	`${parseVtt(vtt)
		.map((cue) => cue.text.replace(/<[^>]+>/g, "").replace(/\n/g, " "))
		.join("\n")}\n`;
