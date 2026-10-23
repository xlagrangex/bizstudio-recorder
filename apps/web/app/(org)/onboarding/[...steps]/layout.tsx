import { getCurrentUser } from "@cap/database/auth/session";
import { buildEnv } from "@cap/env";
import { redirect } from "next/navigation";

export default async function OnboardingStepLayout({
	children,
	params,
}: {
	children: React.ReactNode;
	params: Promise<{ steps: string[] }>;
}) {
	const user = await getCurrentUser();

	if (!user) {
		redirect("/login");
	}

	const steps = user.onboardingSteps || {};
	const currentStep = (await params).steps?.[0] ?? "welcome";

	const allSteps = [
		"welcome",
		"organization-setup",
		"custom-domain",
		"invite-team",
		"download",
	] as const;
	const ordered = buildEnv.NEXT_PUBLIC_IS_CAP
		? allSteps
		: allSteps.filter((s) => s === "welcome" || s === "organization-setup");
	const isComplete = (s: (typeof allSteps)[number]) =>
		s === "welcome"
			? Boolean(steps.welcome && user.name)
			: s === "organization-setup"
				? Boolean(steps.organizationSetup)
				: s === "custom-domain"
					? Boolean(steps.customDomain)
					: s === "invite-team"
						? Boolean(steps.inviteTeam)
						: Boolean(steps.download);

	const firstIncomplete =
		ordered.find((s) => !isComplete(s)) ??
		(buildEnv.NEXT_PUBLIC_IS_CAP ? "download" : undefined);

	if (!firstIncomplete) redirect("/dashboard/caps");

	if (currentStep !== firstIncomplete) {
		redirect(`/onboarding/${firstIncomplete}`);
	}

	return children;
}
