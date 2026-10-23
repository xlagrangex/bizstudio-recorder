import type { Metadata } from "next";
import { NotificationsSettings } from "./NotificationsSettings";

export const metadata: Metadata = {
	title: "Notification Settings — Bizstudio Recorder",
};

export default function NotificationsSettingsPage() {
	return <NotificationsSettings />;
}
