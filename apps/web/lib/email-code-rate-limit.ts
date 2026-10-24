import crypto from "node:crypto";
import { buildEnv } from "@cap/env";
import { headers as nextHeaders } from "next/headers";
import {
	createLocalRateLimiter,
	requestClientIp,
} from "@/lib/local-rate-limit";
import { isRateLimited, RATE_LIMIT_IDS } from "@/lib/rate-limit";

export function normalizeSignInEmail(value: unknown) {
	if (typeof value !== "string") return null;
	const trimmed = value.trim();
	if (trimmed.split("@").length !== 2 || trimmed.includes('"')) return null;
	const [local, domain] = trimmed.toLowerCase().split("@");
	const host = domain?.split(",")[0];
	if (!local || !host || !host.includes(".")) return null;
	return `${local}@${host}`;
}

// Verification rows are looked up under MySQL's accent-insensitive default
// collation, so accented spellings of an address share one bucket.
const emailCodeRateLimitKey = (scope: string, email: string) =>
	`${scope}:${crypto
		.createHash("sha256")
		.update(email.normalize("NFKD").replace(/\p{M}/gu, ""))
		.digest("hex")}`;

const TEN_MINUTES_MS = 10 * 60 * 1000;

const localEmailCodeLimiters = {
	send: {
		email: createLocalRateLimiter({ limit: 5, windowMs: TEN_MINUTES_MS }),
		ip: createLocalRateLimiter({ limit: 20, windowMs: TEN_MINUTES_MS }),
	},
	verify: {
		email: createLocalRateLimiter({ limit: 10, windowMs: TEN_MINUTES_MS }),
		ip: createLocalRateLimiter({ limit: 30, windowMs: TEN_MINUTES_MS }),
	},
};

async function isLocallyLimited(
	kind: keyof typeof localEmailCodeLimiters,
	key: string,
	headers?: Headers,
) {
	const limiters = localEmailCodeLimiters[kind];
	const emailLimited = limiters.email.take(key);
	const ip = requestClientIp(headers ?? (await nextHeaders()));
	const ipLimited = ip ? limiters.ip.take(`${kind}:${ip}`) : false;
	return emailLimited || ipLimited;
}

const isEmailCodeLimited = (
	kind: keyof typeof localEmailCodeLimiters,
	ruleId: string,
	email: string,
	headers?: Headers,
) => {
	const key = emailCodeRateLimitKey(`email-code-${kind}`, email);
	if (buildEnv.NEXT_PUBLIC_IS_CAP !== "true") {
		return isLocallyLimited(kind, key, headers);
	}
	return isRateLimited(ruleId, { key, headers });
};

export const isEmailCodeSendLimited = (email: string, headers?: Headers) =>
	isEmailCodeLimited("send", RATE_LIMIT_IDS.AUTH_OTP_SEND, email, headers);

export const isEmailCodeVerifyLimited = (email: string, headers?: Headers) =>
	isEmailCodeLimited("verify", RATE_LIMIT_IDS.AUTH_OTP_VERIFY, email, headers);
