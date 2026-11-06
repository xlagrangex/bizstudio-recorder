import { describe, expect, it } from "vitest";
import { createSkipAheadGuard } from "@/app/s/[videoId]/_components/skip-ahead-guard";

function media(currentTime = 0, playbackRate = 1) {
	return { currentTime, seeking: false, playbackRate };
}

function play(
	guard: ReturnType<typeof createSkipAheadGuard>,
	m: ReturnType<typeof media>,
	until: number,
) {
	while (m.currentTime < until) {
		m.currentTime = Math.min(until, m.currentTime + 0.25 * m.playbackRate);
		guard.onTimeUpdate(m);
	}
}

describe("createSkipAheadGuard", () => {
	it("moves the frontier while the video plays", () => {
		const guard = createSkipAheadGuard();
		const m = media();
		play(guard, m, 30);
		expect(guard.frontier()).toBe(30);
	});

	it("pulls a forward seek back to the furthest watched point", () => {
		const guard = createSkipAheadGuard();
		const m = media();
		play(guard, m, 30);
		m.currentTime = 600;
		expect(guard.onSeeking(m)).toBe(true);
		expect(m.currentTime).toBe(30);
		expect(guard.frontier()).toBe(30);
	});

	it("lets the viewer go back and forward inside what they watched", () => {
		const guard = createSkipAheadGuard();
		const m = media();
		play(guard, m, 120);
		m.currentTime = 10;
		expect(guard.onSeeking(m)).toBe(false);
		m.currentTime = 110;
		expect(guard.onSeeking(m)).toBe(false);
		expect(m.currentTime).toBe(110);
	});

	it("does not count a jump that skipped the seeking event", () => {
		const guard = createSkipAheadGuard();
		const m = media();
		play(guard, m, 20);
		m.currentTime = 500;
		guard.onTimeUpdate(m);
		expect(guard.frontier()).toBe(20);
	});

	it("keeps up with faster playback speeds", () => {
		const guard = createSkipAheadGuard();
		const m = media(0, 2);
		play(guard, m, 60);
		expect(guard.frontier()).toBe(60);
	});

	it("resumes from a stored frontier", () => {
		const guard = createSkipAheadGuard(300);
		const m = media();
		m.currentTime = 290;
		expect(guard.onSeeking(m)).toBe(false);
		m.currentTime = 900;
		expect(guard.onSeeking(m)).toBe(true);
		expect(m.currentTime).toBe(300);
	});
});
