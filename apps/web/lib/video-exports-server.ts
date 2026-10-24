import "server-only";

import { db } from "@cap/database";
import { getCurrentUser } from "@cap/database/auth/session";
import { videos } from "@cap/database/schema";
import { Storage } from "@cap/web-backend";
import type { Video } from "@cap/web-domain";
import { eq } from "drizzle-orm";
import { Effect, Option } from "effect";
import { runPromise } from "@/lib/server";
import {
	type ExportItem,
	exportQueueKey,
	ORIGINAL_FILE,
	parseSourcesManifest,
	RENDERED_FORMATS,
	type RenderedFormat,
	sourceFileId,
	TRANSCRIPT_FORMATS,
} from "@/lib/video-exports";
import { decodeStorageVideo } from "@/lib/video-storage";

type DbVideo = typeof videos.$inferSelect;

/** Extra formats and raw sources are the owner's, not every viewer's. */
export async function getOwnedVideo(
	videoId: Video.VideoId,
): Promise<DbVideo | null> {
	const user = await getCurrentUser();
	if (!user) return null;
	const [video] = await db()
		.select()
		.from(videos)
		.where(eq(videos.id, videoId));
	return video && video.ownerId === user.id ? video : null;
}

export const videoPrefix = (video: DbVideo) => `${video.ownerId}/${video.id}`;

const bucketFor = (video: DbVideo) =>
	Storage.getAccessForVideo(decodeStorageVideo(video)).pipe(
		Effect.map(([bucket]) => bucket),
	);

const head = (video: DbVideo, key: string) =>
	bucketFor(video).pipe(
		Effect.flatMap((bucket) => bucket.headObject(key)),
		Effect.option,
	);

const text = (video: DbVideo, key: string) =>
	bucketFor(video).pipe(
		Effect.flatMap((bucket) => bucket.getObject(key)),
		Effect.map(Option.getOrNull),
		Effect.catchAll(() => Effect.succeed(null)),
	);

export async function listVideoExports(video: DbVideo): Promise<ExportItem[]> {
	const prefix = videoPrefix(video);
	return Effect.gen(function* () {
		const source = yield* head(video, `${prefix}/result.mp4`);
		const sourceEtag = Option.map(source, (s) => s.ETag?.replaceAll('"', ""));

		const rendered = yield* Effect.forEach(
			Object.values(RENDERED_FORMATS),
			(spec) =>
				Effect.gen(function* () {
					const id = spec.id as RenderedFormat;
					const [output, queued, error] = yield* Effect.all(
						[
							head(video, `${prefix}/exports/${spec.file}`),
							head(video, exportQueueKey(video.id, id)),
							text(video, `${prefix}/exports/${id}.error`),
						],
						{ concurrency: "unbounded" },
					);
					const fresh = Option.exists(
						output,
						(o) =>
							Option.isSome(sourceEtag) &&
							o.Metadata?.["source-etag"] === sourceEtag.value,
					);
					const state: ExportItem["state"] = Option.isSome(queued)
						? "pending"
						: fresh
							? "ready"
							: error
								? "failed"
								: "missing";
					return {
						file: id,
						group: spec.group,
						label: spec.label,
						detail: spec.detail,
						state,
						size: fresh
							? Option.getOrUndefined(output)?.ContentLength
							: undefined,
						error: state === "failed" ? (error ?? undefined) : undefined,
						renderable: Option.isSome(source),
					} satisfies ExportItem;
				}),
			{ concurrency: "unbounded" },
		);

		const transcript = yield* head(video, `${prefix}/transcription.vtt`);
		const manifest = yield* text(video, `${prefix}/sources/manifest.json`);
		const sources = manifest ? parseSourcesManifest(manifest) : null;

		const items: ExportItem[] = [
			{
				file: ORIGINAL_FILE,
				group: "video",
				label: "MP4 full quality",
				detail: "The video as it plays on the link",
				state: Option.isSome(source) ? "ready" : "missing",
				size: Option.getOrUndefined(source)?.ContentLength,
				renderable: false,
			},
			...rendered,
			...Object.values(TRANSCRIPT_FORMATS).map(
				(spec): ExportItem => ({
					file: spec.id,
					group: "transcript",
					label: spec.label,
					detail: spec.detail,
					state: Option.isSome(transcript) ? "ready" : "missing",
					renderable: false,
				}),
			),
			...(sources?.files ?? []).map(
				(entry): ExportItem => ({
					file: sourceFileId(entry.name),
					group: "sources",
					label: entry.label,
					detail: entry.name,
					state: "ready",
					size: entry.size,
					renderable: false,
				}),
			),
		];
		return items;
	}).pipe(runPromise);
}

export async function queueVideoExport(
	video: DbVideo,
	format: RenderedFormat,
): Promise<void> {
	await bucketFor(video)
		.pipe(
			Effect.flatMap((bucket) =>
				bucket.putObject(
					exportQueueKey(video.id, format),
					JSON.stringify({ videoId: video.id, ownerId: video.ownerId, format }),
					{ contentType: "application/json" },
				),
			),
		)
		.pipe(runPromise);
}

export async function fetchVideoObject(
	video: DbVideo,
	key: string,
): Promise<Response | null> {
	const url = await bucketFor(video)
		.pipe(
			Effect.flatMap((bucket) => bucket.getInternalSignedObjectUrl(key)),
			Effect.option,
		)
		.pipe(runPromise);
	if (Option.isNone(url)) return null;
	const response = await fetch(url.value);
	return response.ok ? response : null;
}
