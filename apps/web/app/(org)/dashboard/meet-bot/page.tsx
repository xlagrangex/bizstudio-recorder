import type { Metadata } from "next";

export const metadata: Metadata = {
	title: "Meet Bot — Bizstudio Recorder",
};

// The bot console is served by the meet-bot bridge under /meetbot/ on this
// same origin and enforces its own owner-only session check.
export default function MeetBotPage() {
	return (
		<iframe
			src="/meetbot/"
			title="Meet Bot"
			className="w-full min-h-[calc(100vh-140px)] rounded-xl border border-gray-3 bg-gray-1"
		/>
	);
}
