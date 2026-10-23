export const Logo = ({
	className,
	showVersion,
	showBeta,
	white,
	hideLogoName,
	squaredMark,
	viewBoxDimensions = "0 0 120 40",
	style,
}: {
	className?: string;
	showVersion?: boolean;
	showBeta?: boolean;
	white?: boolean;
	hideLogoName?: boolean;
	squaredMark?: boolean;
	style?: React.CSSProperties;
	viewBoxDimensions?: `${string} ${string} ${string} ${string}`;
}) => {
	return (
		<div className="flex items-center">
			<svg
				viewBox={viewBoxDimensions}
				xmlns="http://www.w3.org/2000/svg"
				preserveAspectRatio="xMidYMid meet"
				fill="none"
				style={style}
				role="img"
				aria-label="Bizstudio Recorder"
				className={className}
			>
				{squaredMark && (
					<>
						<rect
							width="39.5"
							height="39.5"
							x="0.25"
							y="0.25"
							fill="#fff"
							rx="7.75"
						/>
						<rect
							width="39.5"
							height="39.5"
							x="0.25"
							y="0.25"
							stroke="#E7EAF0"
							strokeWidth="0.5"
							rx="7.75"
						/>
					</>
				)}
				<image href="/brand/mascotte.png" x="4" y="4" width="32" height="32" />
				{!hideLogoName && (
					<text
						className={`${white ? "fill-white" : "fill-gray-12"}`}
						fill={white ? "#ffffff" : "#12161F"}
						fontFamily="Inter, system-ui, sans-serif"
					>
						<tspan x="46" y="19" fontSize="13" fontWeight="700">
							Bizstudio
						</tspan>
						<tspan x="46" y="33" fontSize="11" fontWeight="500" opacity="0.6">
							Recorder
						</tspan>
					</text>
				)}
			</svg>
			{showVersion && (
				<span
					className={`text-[10px] font-medium ${
						white ? "text-white" : "text-gray-1"
					}`}
				>
					v{process.env.appVersion}
				</span>
			)}
			{showBeta && (
				<span
					className={`text-[10px] font-medium min-w-[52px] ${
						white ? "text-white" : "text-gray-1"
					}`}
				>
					Beta v{process.env.appVersion}
				</span>
			)}
		</div>
	);
};
