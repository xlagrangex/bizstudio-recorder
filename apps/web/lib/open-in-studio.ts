import { buildEnv } from "@cap/env";
import { toast } from "sonner";

export const OPEN_IN_STUDIO_LABEL = "Open in Studio";
export const OPEN_IN_STUDIO_TOOLTIP =
	"Opens this video in the Bizstudio Rec editor on the Mac where you recorded it";

const FALLBACK_DELAY_MS = 1500;

export const isOpenInStudioAvailable = () =>
	buildEnv.NEXT_PUBLIC_IS_CAP !== "true";

export const openInStudioUrl = (videoId: string) =>
	`bizrec://open?videoId=${encodeURIComponent(videoId)}`;

// A handled custom scheme hands focus to the app; if the page still has it, nothing caught the link.
export const watchOpenInStudioFallback = () => {
	const timer = window.setTimeout(() => {
		if (document.visibilityState === "visible" && document.hasFocus())
			toast("Bizstudio Rec not installed on this Mac?");
	}, FALLBACK_DELAY_MS);
	window.addEventListener("blur", () => window.clearTimeout(timer), {
		once: true,
	});
};
