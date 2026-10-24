import { beforeEach, describe, expect, it, vi } from "vitest";

const mockDb = vi.hoisted(() => vi.fn());
const mockTranscribeVideo = vi.hoisted(() => vi.fn());
const mockIsAiGenerationEnabledForUser = vi.hoisted(() => vi.fn());
const env = vi.hoisted(() => ({
	build: { NEXT_PUBLIC_IS_CAP: "true" as string | undefined },
	server: { ASSEMBLY_API_KEY: undefined as string | undefined },
}));

const schemaMocks = vi.hoisted(() => ({
	users: {
		id: "users.id",
		stripeSubscriptionStatus: "users.stripeSubscriptionStatus",
		thirdPartyStripeSubscriptionId: "users.thirdPartyStripeSubscriptionId",
	},
	videos: {
		id: "videos.id",
		isScreenshot: "videos.isScreenshot",
		ownerId: "videos.ownerId",
	},
}));

vi.mock("@cap/database", () => ({ db: mockDb }));
vi.mock("@cap/env", () => ({
	buildEnv: env.build,
	serverEnv: () => env.server,
}));
vi.mock("@cap/database/schema", () => schemaMocks);
vi.mock("drizzle-orm", () => ({
	eq: vi.fn((field, value) => ({ field, value })),
}));
vi.mock("@/lib/ai-generation-entitlement", () => ({
	isAiGenerationEnabledForUser: mockIsAiGenerationEnabledForUser,
}));
vi.mock("@/lib/transcribe", () => ({
	transcribeVideo: mockTranscribeVideo,
}));

import type { Video } from "@cap/web-domain";
import {
	queueVideoTranscription,
	shouldQueueTranscriptionAfterMediaComplete,
	shouldQueueTranscriptionAfterMultipartComplete,
} from "@/lib/queue-video-transcription";

function makeSelectChain(rows: unknown[]) {
	const chain = {
		select: vi.fn(),
		from: vi.fn(),
		innerJoin: vi.fn(),
		where: vi.fn(),
	};
	chain.select.mockReturnValue(chain);
	chain.from.mockReturnValue(chain);
	chain.innerJoin.mockReturnValue(chain);
	chain.where.mockResolvedValue(rows);
	return chain;
}

beforeEach(() => {
	vi.clearAllMocks();
	env.build.NEXT_PUBLIC_IS_CAP = "true";
	env.server.ASSEMBLY_API_KEY = undefined;
	mockIsAiGenerationEnabledForUser.mockReturnValue(true);
	mockTranscribeVideo.mockResolvedValue({
		success: true,
		message: "Transcription workflow started",
	});
});

describe("queueVideoTranscription", () => {
	it("queues transcription with the video owner's AI entitlement", async () => {
		const owner = {
			id: "user-1",
			isScreenshot: false,
			stripeSubscriptionStatus: "active",
			thirdPartyStripeSubscriptionId: null,
		};
		mockDb.mockReturnValue(makeSelectChain([owner]));

		const result = await queueVideoTranscription("video-1" as Video.VideoId);

		expect(mockIsAiGenerationEnabledForUser).toHaveBeenCalledWith(owner);
		expect(mockTranscribeVideo).toHaveBeenCalledWith("video-1", "user-1", true);
		expect(result).toEqual({
			success: true,
			message: "Transcription workflow started",
		});
	});

	it("does not queue transcription for screenshots", async () => {
		mockDb.mockReturnValue(
			makeSelectChain([
				{
					id: "user-1",
					isScreenshot: true,
					stripeSubscriptionStatus: "active",
					thirdPartyStripeSubscriptionId: null,
				},
			]),
		);

		const result = await queueVideoTranscription("video-1" as Video.VideoId);

		expect(mockTranscribeVideo).not.toHaveBeenCalled();
		expect(result).toEqual({
			success: true,
			message: "Screenshot does not need transcription",
		});
	});

	it("skips cleanly on self-host when built-in transcription is not configured", async () => {
		env.build.NEXT_PUBLIC_IS_CAP = undefined;

		const result = await queueVideoTranscription("video-1" as Video.VideoId);

		expect(mockDb).not.toHaveBeenCalled();
		expect(mockTranscribeVideo).not.toHaveBeenCalled();
		expect(result).toEqual({
			success: true,
			message: "Built-in transcription is not configured",
		});
	});

	it("queues on self-host when built-in transcription is configured", async () => {
		env.build.NEXT_PUBLIC_IS_CAP = undefined;
		env.server.ASSEMBLY_API_KEY = "configured";
		mockDb.mockReturnValue(
			makeSelectChain([
				{
					id: "user-1",
					isScreenshot: false,
					stripeSubscriptionStatus: null,
					thirdPartyStripeSubscriptionId: null,
				},
			]),
		);

		await queueVideoTranscription("video-1" as Video.VideoId);

		expect(mockTranscribeVideo).toHaveBeenCalledWith("video-1", "user-1", true);
	});

	it("does not queue transcription when the video owner is missing", async () => {
		mockDb.mockReturnValue(makeSelectChain([]));

		const result = await queueVideoTranscription("video-1" as Video.VideoId);

		expect(mockTranscribeVideo).not.toHaveBeenCalled();
		expect(result).toEqual({
			success: false,
			message: "Video owner does not exist",
		});
	});
});

describe("transcription queue timing", () => {
	it("queues after final media completes, but not for edit jobs", () => {
		expect(shouldQueueTranscriptionAfterMediaComplete("webMP4", false)).toBe(
			true,
		);
		expect(
			shouldQueueTranscriptionAfterMediaComplete("desktopSegments", false),
		).toBe(true);
		expect(shouldQueueTranscriptionAfterMediaComplete("desktopMP4", true)).toBe(
			false,
		);
	});

	it("queues direct final uploads unless media processing is pending", () => {
		expect(
			shouldQueueTranscriptionAfterMultipartComplete("desktopMP4", false),
		).toBe(true);
		expect(
			shouldQueueTranscriptionAfterMultipartComplete("webMP4", false),
		).toBe(true);
		expect(shouldQueueTranscriptionAfterMultipartComplete("webMP4", true)).toBe(
			false,
		);
		expect(
			shouldQueueTranscriptionAfterMultipartComplete("desktopSegments", false),
		).toBe(false);
	});
});
