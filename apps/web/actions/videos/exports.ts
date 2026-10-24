"use server";

import type { Video } from "@cap/web-domain";
import type { ExportItem } from "@/lib/video-exports";
import { isRenderedFormat } from "@/lib/video-exports";
import {
	getOwnedVideo,
	listVideoExports,
	queueVideoExport,
} from "@/lib/video-exports-server";

type Result<T> =
	| { success: true; value: T }
	| { success: false; error: string };

export async function getVideoExports(
	videoId: Video.VideoId,
): Promise<Result<ExportItem[]>> {
	const video = await getOwnedVideo(videoId);
	if (!video) return { success: false, error: "Video not found" };
	try {
		return { success: true, value: await listVideoExports(video) };
	} catch (error) {
		console.error("Failed to list video exports", error);
		return { success: false, error: "Couldn't load the download options" };
	}
}

export async function requestVideoExport(
	videoId: Video.VideoId,
	format: string,
): Promise<Result<null>> {
	if (!isRenderedFormat(format))
		return { success: false, error: "Unknown format" };
	const video = await getOwnedVideo(videoId);
	if (!video) return { success: false, error: "Video not found" };
	try {
		await queueVideoExport(video, format);
		return { success: true, value: null };
	} catch (error) {
		console.error("Failed to queue video export", error);
		return { success: false, error: "Couldn't start the conversion" };
	}
}
