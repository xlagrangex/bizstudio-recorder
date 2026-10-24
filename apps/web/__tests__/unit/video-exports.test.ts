import { describe, expect, it } from "vitest";
import {
	attachmentDisposition,
	downloadFilename,
	exportQueueKey,
	isRenderedFormat,
	parseSourcesManifest,
	sourceFileId,
	sourceNameOf,
	vttToSrt,
	vttToText,
} from "@/lib/video-exports";

const VTT = `WEBVTT

1
00:00:01.000 --> 00:00:03.500
Ciao a tutti

2
01:02:03.250 --> 01:02:05.000
Seconda <b>riga</b>
continua
`;

describe("video exports", () => {
	it("converts VTT cues to SRT", () => {
		expect(vttToSrt(VTT)).toBe(
			"1\n00:00:01,000 --> 00:00:03,500\nCiao a tutti\n\n2\n01:02:03,250 --> 01:02:05,000\nSeconda <b>riga</b>\ncontinua\n",
		);
	});

	it("converts VTT cues to plain text", () => {
		expect(vttToText(VTT)).toBe("Ciao a tutti\nSeconda riga continua\n");
	});

	it("names the queue marker the way the worker parses it", () => {
		expect(exportQueueKey("en8dj9y7a6s3ssd", "mp4-720")).toBe(
			"export-queue/en8dj9y7a6s3ssd--mp4-720.json",
		);
		expect(isRenderedFormat("mp3")).toBe(true);
		expect(isRenderedFormat("srt")).toBe(false);
		expect(isRenderedFormat("toString")).toBe(false);
	});

	it("only accepts plain source file names", () => {
		expect(sourceNameOf(sourceFileId("screen-1.mp4"))).toBe("screen-1.mp4");
		expect(sourceNameOf("source:../result.mp4")).toBeNull();
		expect(sourceNameOf("source:manifest.json")).toBeNull();
		expect(sourceNameOf("mp3")).toBeNull();
	});

	it("drops manifest entries it can't serve", () => {
		const manifest = parseSourcesManifest(
			JSON.stringify({
				version: 1,
				files: [
					{ name: "camera.mp4", kind: "camera", label: "Camera", size: 10 },
					{ name: "../x", kind: "screen", label: "Screen", size: 1 },
				],
			}),
		);
		expect(manifest?.files.map((file) => file.name)).toEqual(["camera.mp4"]);
		expect(parseSourcesManifest("nope")).toBeNull();
	});

	it("keeps accents in the download name with an ASCII fallback", () => {
		const name = downloadFilename("Riunione: perché/così", ".mp3");
		expect(name).toBe("Riunione perché così.mp3");
		expect(attachmentDisposition(name)).toBe(
			`attachment; filename="Riunione perche cosi.mp3"; filename*=UTF-8''Riunione%20perch%C3%A9%20cos%C3%AC.mp3`,
		);
	});
});
