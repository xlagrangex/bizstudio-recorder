import {
	Body,
	Container,
	Head,
	Heading,
	Html,
	Link,
	Preview,
	Section,
	Tailwind,
	Text,
} from "@react-email/components";
import BrandHeader from "./components/BrandHeader";
import Footer from "./components/Footer";

export function VideoViewerInvite({
	email,
	videoName,
	url,
}: {
	email: string;
	videoName: string;
	url: string;
}) {
	return (
		<Html>
			<Head />
			<Preview>
				You've been invited to watch {videoName} on Bizstudio Recorder
			</Preview>
			<Tailwind>
				<Body className="mx-auto my-auto bg-gray-1 font-sans">
					<Container className="mx-auto my-10 max-w-[500px] rounded border border-solid border-gray-200 px-10 py-5">
						<BrandHeader />
						<Heading className="mx-0 my-7 p-0 text-center text-xl font-semibold text-black">
							You're invited to watch {videoName}
						</Heading>
						<Text className="text-sm leading-6 text-black">
							Open the recording using a Bizstudio Recorder account with this
							email address:
							{` ${email}`}. You can sign up if you don't have an account yet.
						</Text>
						<Section className="my-8 text-center">
							<Link
								className="rounded-full bg-black px-6 py-3 text-center text-[12px] font-semibold text-white no-underline"
								href={url}
							>
								Watch recording
							</Link>
						</Section>
						<Text className="text-sm leading-6 text-black">
							You don't need to join the owner's organization to watch it.
						</Text>
						<Text className="max-w-sm flex-wrap break-words font-medium text-purple-600 no-underline">
							{url.replace(/^https?:\/\//, "")}
						</Text>
						<Footer email={email} />
					</Container>
				</Body>
			</Tailwind>
		</Html>
	);
}
