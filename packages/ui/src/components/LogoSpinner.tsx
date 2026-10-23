export const LogoSpinner = ({ className }: { className: string }) => {
	return (
		<svg
			role="img"
			aria-label="Bizstudio Recorder"
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			fill="none"
			viewBox="0 0 40 40"
		>
			<rect
				width="39.5"
				height="39.5"
				x="0.25"
				y="0.25"
				fill="#fff"
				rx="7.75"
			></rect>
			<rect
				width="39.5"
				height="39.5"
				x="0.25"
				y="0.25"
				stroke="#E7EAF0"
				strokeWidth="0.5"
				rx="7.75"
			></rect>
			<image href="/brand/mascotte.png" x="4" y="4" width="32" height="32" />
		</svg>
	);
};
