"use client";

import {
	Button,
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
} from "@cap/ui";
import type { Video } from "@cap/web-domain";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { getVideoExports, requestVideoExport } from "@/actions/videos/exports";
import type { ExportItem } from "@/lib/video-exports";

const GROUPS: Array<{ id: ExportItem["group"]; title: string }> = [
	{ id: "video", title: "Video" },
	{ id: "audio", title: "Audio" },
	{ id: "transcript", title: "Transcript" },
	{ id: "sources", title: "Separate sources" },
];

const formatSize = (bytes?: number) => {
	if (!bytes) return null;
	const units = ["B", "KB", "MB", "GB"];
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
};

export const downloadHref = (videoId: string, file: string) =>
	`/api/videos/${encodeURIComponent(videoId)}/download?file=${encodeURIComponent(file)}`;

export function DownloadDialog({
	videoId,
	open,
	onOpenChange,
}: {
	videoId: Video.VideoId;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const queryClient = useQueryClient();
	const queryKey = ["video-exports", videoId];
	const exports = useQuery({
		queryKey,
		enabled: open,
		queryFn: async () => {
			const result = await getVideoExports(videoId);
			if (!result.success) throw new Error(result.error);
			return result.value;
		},
		refetchInterval: (query) =>
			query.state.data?.some((item) => item.state === "pending") ? 4000 : false,
	});

	const convert = useMutation({
		mutationFn: async (file: string) => {
			const result = await requestVideoExport(videoId, file);
			if (!result.success) throw new Error(result.error);
		},
		onMutate: (file) => {
			queryClient.setQueryData<ExportItem[]>(queryKey, (items) =>
				items?.map((item) =>
					item.file === file ? { ...item, state: "pending" } : item,
				),
			);
		},
		onError: (error) => {
			toast.error(error.message);
		},
		onSettled: () => queryClient.invalidateQueries({ queryKey }),
	});

	const items = exports.data ?? [];

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="p-0 w-full max-w-lg rounded-xl border bg-gray-2 border-gray-4">
				<DialogHeader
					icon={<Download className="size-4" aria-hidden />}
					description="Pick a format. New formats are prepared on the server the first time, then stay ready."
				>
					<DialogTitle>Download</DialogTitle>
				</DialogHeader>
				<div className="overflow-y-auto p-5 space-y-5 max-h-[65vh]">
					{exports.isPending && (
						<div className="flex gap-2 items-center text-sm text-gray-10">
							<Loader2 className="animate-spin size-4" aria-hidden />
							Loading options...
						</div>
					)}
					{exports.isError && (
						<p className="text-sm text-red-500">{exports.error.message}</p>
					)}
					{exports.isSuccess &&
						GROUPS.map((group) => {
							const rows = items.filter((item) => item.group === group.id);
							return (
								<section key={group.id} className="space-y-2">
									<h3 className="text-xs font-medium tracking-wide uppercase text-gray-10">
										{group.title}
									</h3>
									{rows.length === 0 ? (
										<p className="text-sm text-gray-10">
											Not available for this video. The Mac app uploads screen,
											camera and audio tracks separately for Studio recordings;
											browser and Instant recordings only have the final video.
										</p>
									) : (
										<ul className="rounded-lg border divide-y border-gray-4 divide-gray-4">
											{rows.map((item) => (
												<ExportRow
													key={item.file}
													item={item}
													videoId={videoId}
													onConvert={() => convert.mutate(item.file)}
												/>
											))}
										</ul>
									)}
								</section>
							);
						})}
				</div>
			</DialogContent>
		</Dialog>
	);
}

function ExportRow({
	item,
	videoId,
	onConvert,
}: {
	item: ExportItem;
	videoId: string;
	onConvert: () => void;
}) {
	const size = formatSize(item.size);
	return (
		<li className="flex gap-3 justify-between items-center py-2.5 px-3">
			<div className="min-w-0">
				<p className="text-sm font-medium text-gray-12">{item.label}</p>
				<p className="text-xs truncate text-gray-10" title={item.detail}>
					{item.detail}
					{size ? ` · ${size}` : ""}
				</p>
				{item.state === "failed" && item.error && (
					<p className="flex gap-1 items-center mt-0.5 text-xs text-red-500">
						<AlertCircle className="size-3 shrink-0" aria-hidden />
						{item.error}
					</p>
				)}
			</div>
			<div className="shrink-0">
				{item.state === "ready" ? (
					<Button
						size="xs"
						variant="gray"
						href={downloadHref(videoId, item.file)}
					>
						<Download className="mr-1 size-3.5" aria-hidden />
						Download
					</Button>
				) : item.state === "pending" ? (
					<span className="flex gap-1.5 items-center text-xs text-gray-10">
						<Loader2 className="animate-spin size-3.5" aria-hidden />
						Converting...
					</span>
				) : item.renderable ? (
					<Button size="xs" variant="white" onClick={onConvert}>
						{item.state === "failed" ? "Retry" : "Prepare"}
					</Button>
				) : (
					<span className="text-xs text-gray-9">Not available</span>
				)}
			</div>
		</li>
	);
}
