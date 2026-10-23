import {
	Body,
	Container,
	Head,
	Heading,
	Html,
	Preview,
	Section,
	Tailwind,
	Text,
} from "@react-email/components";
import BrandHeader from "./components/BrandHeader";

export function MessengerSupportEmail({
	userEmail,
	userName,
	conversationId,
	message,
}: {
	userEmail: string;
	userName?: string | null;
	conversationId: string;
	message: string;
}) {
	return (
		<Html>
			<Head />
			<Preview>Messenger support request from {userEmail}</Preview>
			<Tailwind>
				<Body className="mx-auto my-auto bg-gray-1 font-sans">
					<Container className="mx-auto my-10 max-w-[500px] rounded border border-solid border-gray-200 px-10 py-5">
						<BrandHeader />
						<Heading className="mx-0 my-7 p-0 text-center text-xl font-semibold text-black">
							Messenger Support Request
						</Heading>
						<Text className="text-sm leading-6 text-black">
							<strong>From:</strong> {userName ? `${userName} ` : ""}
							&lt;{userEmail}&gt;
						</Text>
						<Text className="text-sm leading-6 text-black">
							<strong>Conversation:</strong> {conversationId}
						</Text>
						<Section className="my-4 rounded-lg bg-gray-50 p-4">
							<Text className="whitespace-pre-wrap text-sm leading-6 text-gray-700">
								{message}
							</Text>
						</Section>
						<Text className="text-sm leading-6 text-gray-500">
							Reply to this email to respond directly to the user.
						</Text>
					</Container>
				</Body>
			</Tailwind>
		</Html>
	);
}
