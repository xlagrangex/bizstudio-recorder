import type { Metadata } from "next";
import { UsageClient } from "./UsageClient";

export const metadata: Metadata = {
	title: "Developer Usage — Bizstudio Recorder",
};

export default async function UsagePage() {
	return <UsageClient />;
}
