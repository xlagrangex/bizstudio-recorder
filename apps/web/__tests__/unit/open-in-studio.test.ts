import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const env = vi.hoisted(() => ({
	buildEnv: { NEXT_PUBLIC_IS_CAP: undefined as string | undefined },
}));
const toast = vi.hoisted(() => vi.fn());

vi.mock("@cap/env", () => env);
vi.mock("sonner", () => ({ toast }));

import {
	isOpenInStudioAvailable,
	openInStudioUrl,
	watchOpenInStudioFallback,
} from "@/lib/open-in-studio";

describe("open in studio", () => {
	let focused = true;
	let blurListener: (() => void) | undefined;

	beforeEach(() => {
		vi.useFakeTimers();
		focused = true;
		blurListener = undefined;
		toast.mockClear();
		vi.stubGlobal("window", {
			setTimeout: globalThis.setTimeout,
			clearTimeout: globalThis.clearTimeout,
			addEventListener: (_: string, listener: () => void) => {
				blurListener = listener;
			},
		});
		vi.stubGlobal("document", {
			visibilityState: "visible",
			hasFocus: () => focused,
		});
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		env.buildEnv.NEXT_PUBLIC_IS_CAP = undefined;
	});

	it("builds the bizrec deep link", () => {
		expect(openInStudioUrl("abc 123")).toBe("bizrec://open?videoId=abc%20123");
	});

	it("is only available on self-hosted builds", () => {
		expect(isOpenInStudioAvailable()).toBe(true);
		env.buildEnv.NEXT_PUBLIC_IS_CAP = "true";
		expect(isOpenInStudioAvailable()).toBe(false);
	});

	it("warns when the page keeps focus after the link fires", () => {
		watchOpenInStudioFallback();
		vi.advanceTimersByTime(1500);
		expect(toast).toHaveBeenCalledWith(
			"Bizstudio Rec not installed on this Mac?",
		);
	});

	it("stays quiet when the app takes focus", () => {
		watchOpenInStudioFallback();
		focused = false;
		blurListener?.();
		vi.advanceTimersByTime(1500);
		expect(toast).not.toHaveBeenCalled();
	});
});
