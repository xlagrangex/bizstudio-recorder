export const LogoBadge = ({ className }: { className: string }) => {
	return (
		<svg
			role="img"
			aria-label="Bizstudio Recorder"
			xmlns="http://www.w3.org/2000/svg"
			className={className}
			fill="none"
			viewBox="0 0 40 40"
			preserveAspectRatio="xMidYMid meet"
			style={{
				aspectRatio: "1 / 1",
			}}
		>
			<rect width="40" height="40" fill="#fff" rx="8"></rect>
			<image href="/brand/mascotte.png" x="4" y="4" width="32" height="32" />
		</svg>
	);
};
