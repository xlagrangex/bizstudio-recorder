import Image from "next/image";

export const MascotArt = () => (
	<div className="flex justify-center items-center w-full h-full bg-gray-3">
		<Image
			src="/brand/mascotte.png"
			alt=""
			width={96}
			height={96}
			className="object-contain size-24"
		/>
	</div>
);
