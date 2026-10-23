import { CAP_LOGO_URL } from "@cap/utils";
import { Img, Section, Text } from "@react-email/components";

export default function BrandHeader() {
	return (
		<Section className="mt-8">
			<Text className="m-0 text-center text-lg font-semibold text-black">
				<Img
					src={CAP_LOGO_URL}
					width="40"
					height="40"
					alt=""
					className="mr-2 inline-block align-middle"
				/>
				<span className="align-middle">Bizstudio Recorder</span>
			</Text>
		</Section>
	);
}
