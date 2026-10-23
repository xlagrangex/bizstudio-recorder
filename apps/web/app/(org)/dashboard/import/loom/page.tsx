import type { Metadata } from "next";
import {
	loomImportDestinationFromSearchParams,
	loomImportPageHref,
} from "@/lib/loom-import-destination";
import { ImportLoomPage } from "./ImportLoomPage";

export const metadata: Metadata = {
	title: "Import from Loom — Bizstudio Recorder",
};

export default async function Page({
	searchParams,
}: {
	searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
	const destination = loomImportDestinationFromSearchParams(await searchParams);
	return (
		<ImportLoomPage
			key={loomImportPageHref(destination)}
			initialDestination={destination}
		/>
	);
}
