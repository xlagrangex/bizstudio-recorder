import type { Video } from "@cap/web-domain";
import {
	attachmentDisposition,
	downloadFilename,
	isRenderedFormat,
	isTranscriptFormat,
	ORIGINAL_FILE,
	RENDERED_FORMATS,
	sourceNameOf,
	TRANSCRIPT_FORMATS,
	vttToSrt,
	vttToText,
} from "@/lib/video-exports";
import {
	fetchVideoObject,
	getOwnedVideo,
	videoPrefix,
} from "@/lib/video-exports-server";

export async function GET(
	request: Request,
	props: RouteContext<"/api/videos/[videoId]/download">,
) {
	const { videoId } = (await props.params) as { videoId: Video.VideoId };
	const file = new URL(request.url).searchParams.get("file") ?? ORIGINAL_FILE;
	const video = await getOwnedVideo(videoId);
	if (!video) return Response.json({ error: "Not found" }, { status: 404 });

	const prefix = videoPrefix(video);

	if (isTranscriptFormat(file)) {
		const vtt = await fetchVideoObject(video, `${prefix}/transcription.vtt`);
		if (!vtt) return Response.json({ error: "No transcript" }, { status: 404 });
		const body = await vtt.text();
		const spec = TRANSCRIPT_FORMATS[file];
		const content =
			file === "srt" ? vttToSrt(body) : file === "txt" ? vttToText(body) : body;
		return new Response(content, {
			headers: {
				"Content-Type": spec.contentType,
				"Content-Disposition": attachmentDisposition(
					downloadFilename(video.name, `.${spec.extension}`),
				),
				"Cache-Control": "private, no-store",
			},
		});
	}

	let key: string;
	let filename: string;
	if (file === ORIGINAL_FILE) {
		key = `${prefix}/result.mp4`;
		filename = downloadFilename(video.name, ".mp4");
	} else if (isRenderedFormat(file)) {
		const spec = RENDERED_FORMATS[file];
		key = `${prefix}/exports/${spec.file}`;
		filename = downloadFilename(
			video.name,
			spec.group === "audio" || file === "webm" || file === "gif"
				? `.${spec.extension}`
				: ` (${spec.label.replace("MP4 ", "")}).${spec.extension}`,
		);
	} else {
		const name = sourceNameOf(file);
		if (!name) return Response.json({ error: "Unknown file" }, { status: 400 });
		key = `${prefix}/sources/${name}`;
		filename = downloadFilename(video.name, ` - ${name}`);
	}

	const upstream = await fetchVideoObject(video, key);
	if (!upstream?.body)
		return Response.json({ error: "File not available" }, { status: 404 });

	const headers = new Headers({
		"Content-Type":
			upstream.headers.get("content-type") ?? "application/octet-stream",
		"Content-Disposition": attachmentDisposition(filename),
		"Cache-Control": "private, no-store",
	});
	const length = upstream.headers.get("content-length");
	if (length) headers.set("Content-Length", length);
	return new Response(upstream.body, { headers });
}
