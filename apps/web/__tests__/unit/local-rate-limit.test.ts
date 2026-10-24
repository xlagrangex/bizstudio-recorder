import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	isRateLimited: vi.fn(async () => false),
}));

vi.mock("@cap/env", () => ({ buildEnv: { NEXT_PUBLIC_IS_CAP: undefined } }));
vi.mock("next/headers", () => ({ headers: async () => new Headers() }));
vi.mock("@/lib/rate-limit", () => ({
	isRateLimited: mocks.isRateLimited,
	RATE_LIMIT_IDS: {
		AUTH_OTP_SEND: "rl_auth_otp_send",
		AUTH_OTP_VERIFY: "rl_auth_otp_verify",
	},
}));

import {
	isEmailCodeSendLimited,
	isEmailCodeVerifyLimited,
} from "@/lib/email-code-rate-limit";
import {
	createLocalRateLimiter,
	requestClientIp,
} from "@/lib/local-rate-limit";

describe("createLocalRateLimiter", () => {
	it("allows the limit, rejects the next attempt, and refills over time", () => {
		const limiter = createLocalRateLimiter({ limit: 3, windowMs: 3000 });
		expect([0, 0, 0].map((now) => limiter.take("a", now))).toEqual([
			false,
			false,
			false,
		]);
		expect(limiter.take("a", 0)).toBe(true);
		expect(limiter.take("a", 500)).toBe(true);
		expect(limiter.take("a", 1000)).toBe(false);
		expect(limiter.take("b", 1000)).toBe(false);
	});

	it("evicts the least recently used key past the key cap", () => {
		const limiter = createLocalRateLimiter({
			limit: 1,
			windowMs: 60_000,
			maxKeys: 2,
		});
		limiter.take("a", 0);
		limiter.take("b", 0);
		limiter.take("c", 0);
		expect(limiter.take("b", 0)).toBe(true);
		expect(limiter.take("a", 0)).toBe(false);
	});
});

describe("requestClientIp", () => {
	it("uses the proxy-appended hop, falling back to x-real-ip", () => {
		expect(
			requestClientIp(
				new Headers({ "x-forwarded-for": "6.6.6.6, 203.0.113.9" }),
			),
		).toBe("203.0.113.9");
		expect(requestClientIp(new Headers({ "x-real-ip": "198.51.100.2" }))).toBe(
			"198.51.100.2",
		);
		expect(requestClientIp(new Headers())).toBeNull();
	});
});

describe("self-hosted email code limits", () => {
	it("fails closed after repeated verification attempts for one email", async () => {
		const headers = new Headers({ "x-forwarded-for": "192.0.2.10" });
		const results: boolean[] = [];
		for (let attempt = 0; attempt < 11; attempt++) {
			results.push(
				await isEmailCodeVerifyLimited("victim@example.com", headers),
			);
		}
		expect(results.slice(0, 10)).not.toContain(true);
		expect(results[10]).toBe(true);
		expect(
			await isEmailCodeVerifyLimited(
				"victim@example.com",
				new Headers({ "x-forwarded-for": "192.0.2.11" }),
			),
		).toBe(true);
		expect(mocks.isRateLimited).not.toHaveBeenCalled();
	});

	it("limits code sends per IP across different emails", async () => {
		const headers = new Headers({ "x-forwarded-for": "192.0.2.20" });
		const results: boolean[] = [];
		for (let attempt = 0; attempt < 21; attempt++) {
			results.push(
				await isEmailCodeSendLimited(`person${attempt}@example.com`, headers),
			);
		}
		expect(results.slice(0, 20)).not.toContain(true);
		expect(results[20]).toBe(true);
	});
});
