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
import { ctaStyle } from "./components/cta";
import Footer from "./components/Footer";

export function DownloadLink({ email = "" }: { email: string }) {
	return (
		<Html>
			<Head />
			<Preview>Get started with Bizstudio Recorder</Preview>
			<Tailwind>
				<Body className="mx-auto my-auto bg-gray-1 font-sans">
					<Container className="mx-auto my-10 max-w-[500px] rounded border border-solid border-gray-200 px-10 py-5">
						<BrandHeader />
						<Heading className="mx-0 my-7 p-0 text-center text-xl font-semibold text-black">
							Your Bizstudio Recorder link is here
						</Heading>
						<Text className="text-sm leading-6 text-black">
							Thanks for your interest in Bizstudio Recorder! Use the link below
							to get started:
						</Text>

						<Section className="my-6">
							<Link
								className="block w-full rounded-lg bg-[#a51b80] px-6 py-3 text-center text-[13px] font-semibold text-white no-underline"
								style={ctaStyle}
								href="https://rec.bizstudio.it"
							>
								Open Bizstudio Recorder
							</Link>
						</Section>

						<Text className="text-sm leading-6 text-black mt-4">
							Bizstudio Recorder makes beautiful, shareable screen recordings —
							lightweight, powerful, and privacy-focused.
						</Text>
						<Footer email={email} marketing={true} />
					</Container>
				</Body>
			</Tailwind>
		</Html>
	);
}
