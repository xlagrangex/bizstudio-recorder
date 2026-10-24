type Bucket = { tokens: number; updatedAt: number };

export type LocalRateLimiter = {
	take(key: string, now?: number): boolean;
};

// In-process only: limits reset on restart and are not shared between
// replicas, which matches the single-container self-hosted deployment.
export function createLocalRateLimiter({
	limit,
	windowMs,
	maxKeys = 10_000,
}: {
	limit: number;
	windowMs: number;
	maxKeys?: number;
}): LocalRateLimiter {
	const buckets = new Map<string, Bucket>();
	const refillPerMs = limit / windowMs;

	return {
		take(key, now = Date.now()) {
			const previous = buckets.get(key);
			const tokens = previous
				? Math.min(
						limit,
						previous.tokens + (now - previous.updatedAt) * refillPerMs,
					)
				: limit;
			const limited = tokens < 1;

			buckets.delete(key);
			buckets.set(key, {
				tokens: limited ? tokens : tokens - 1,
				updatedAt: now,
			});
			if (buckets.size > maxKeys) {
				const oldest = buckets.keys().next().value;
				if (oldest !== undefined) buckets.delete(oldest);
			}

			return limited;
		},
	};
}

// nginx appends the peer address with $proxy_add_x_forwarded_for, so the last
// hop is the one a client cannot forge.
export function requestClientIp(headers: Headers) {
	const forwarded = headers
		.get("x-forwarded-for")
		?.split(",")
		.map((value) => value.trim())
		.filter(Boolean)
		.at(-1);
	return forwarded || headers.get("x-real-ip")?.trim() || null;
}
