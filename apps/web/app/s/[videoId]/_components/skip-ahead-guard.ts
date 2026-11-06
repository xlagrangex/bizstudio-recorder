"use client";

import { type RefObject, useEffect, useRef } from "react";

export interface GuardedMedia {
	currentTime: number;
	readonly seeking: boolean;
	readonly playbackRate: number;
}

// A seek this close past the frontier is the player rounding, not a skip.
const SEEK_TOLERANCE_SECONDS = 1;
// timeupdate fires every 15-250 ms; anything bigger than this between two
// ticks is a jump, so it must not move the frontier.
const MAX_NATURAL_STEP_SECONDS = 2;

export function createSkipAheadGuard(initialFrontier = 0) {
	let frontier = Math.max(0, initialFrontier);
	let lastTime = 0;

	return {
		frontier: () => frontier,
		onTimeUpdate(media: GuardedMedia): boolean {
			const time = media.currentTime;
			const step = time - lastTime;
			const natural =
				!media.seeking &&
				step >= 0 &&
				step <= MAX_NATURAL_STEP_SECONDS * Math.max(1, media.playbackRate) &&
				lastTime <= frontier + SEEK_TOLERANCE_SECONDS;
			lastTime = time;
			if (natural && time > frontier) {
				frontier = time;
				return true;
			}
			return false;
		},
		/** Returns true when the seek went past what was watched and got pulled back. */
		onSeeking(media: GuardedMedia): boolean {
			if (media.currentTime > frontier + SEEK_TOLERANCE_SECONDS) {
				media.currentTime = frontier;
				lastTime = frontier;
				return true;
			}
			lastTime = media.currentTime;
			return false;
		},
	};
}

const storageKey = (videoId: string) => `bizrec:watched:${videoId}`;

function readFrontier(videoId: string): number {
	try {
		const value = Number(localStorage.getItem(storageKey(videoId)));
		return Number.isFinite(value) && value > 0 ? value : 0;
	} catch {
		return 0;
	}
}

function writeFrontier(videoId: string, frontier: number) {
	try {
		localStorage.setItem(storageKey(videoId), String(Math.floor(frontier)));
	} catch {}
}

/**
 * Keeps the viewer from moving past the furthest point they have watched,
 * whichever control asks for the seek (slider, keys, chapters, transcript,
 * timeline). Listening in the capture phase catches media events, which do not
 * bubble, without caring when the player mounts or swaps its <video>.
 */
export function useSkipAheadGuard(
	videoRef: RefObject<HTMLVideoElement | null>,
	{
		enabled,
		videoId,
		onBlocked,
	}: { enabled: boolean; videoId: string; onBlocked?: () => void },
) {
	const onBlockedRef = useRef(onBlocked);
	onBlockedRef.current = onBlocked;

	useEffect(() => {
		if (!enabled) return;
		const guard = createSkipAheadGuard(readFrontier(videoId));
		let savedSecond = Math.floor(guard.frontier());

		// The seek slider reads this to stop at the watched point instead of
		// sitting on a time the guard already pulled the video back from.
		const publish = (video: HTMLVideoElement) => {
			video.dataset.watchedUntil = String(guard.frontier());
		};

		const handleTimeUpdate = (event: Event) => {
			if (event.target !== videoRef.current) return;
			publish(videoRef.current);
			if (!guard.onTimeUpdate(videoRef.current)) return;
			publish(videoRef.current);
			const second = Math.floor(guard.frontier());
			if (second > savedSecond) {
				savedSecond = second;
				writeFrontier(videoId, guard.frontier());
			}
		};
		const handleSeeking = (event: Event) => {
			if (event.target !== videoRef.current) return;
			publish(videoRef.current);
			if (guard.onSeeking(videoRef.current)) onBlockedRef.current?.();
		};
		if (videoRef.current) publish(videoRef.current);

		document.addEventListener("timeupdate", handleTimeUpdate, true);
		document.addEventListener("seeking", handleSeeking, true);
		return () => {
			document.removeEventListener("timeupdate", handleTimeUpdate, true);
			document.removeEventListener("seeking", handleSeeking, true);
			if (videoRef.current) delete videoRef.current.dataset.watchedUntil;
		};
	}, [enabled, videoId, videoRef]);
}
