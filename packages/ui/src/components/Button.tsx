import { classNames } from "@cap/utils/helpers";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { ArrowUpRight } from "lucide-react";
import * as React from "react";
import { LoadingSpinner } from "./LoadingSpinner";

const buttonVariants = cva(
	"flex items-center justify-center transition-colors duration-200 rounded-full disabled:cursor-not-allowed cursor-pointer font-medium px-5 ring-offset-transparent relative gap-1",
	{
		defaultVariants: {
			variant: "primary",
			size: "md",
		},
		variants: {
			variant: {
				primary:
					"bg-biz-grad-vivid text-white shadow-[inset_0_0_7px_#ffffff59,0_16px_24px_#b13ec247] hover:brightness-110 active:bg-biz-grad disabled:bg-none disabled:bg-gray-6 disabled:text-gray-9 disabled:shadow-none disabled:brightness-100",
				blue: "bg-biz-grad-vivid text-white shadow-[inset_0_0_7px_#ffffff59,0_16px_24px_#b13ec247] hover:brightness-110 active:bg-biz-grad disabled:bg-none disabled:bg-gray-7 disabled:text-gray-10 disabled:shadow-none disabled:brightness-100",
				destructive:
					"bg-red-500 text-white border-transparent hover:bg-red-600 disabled:bg-gray-7 disabled:border-gray-8 border disabled:text-gray-10",
				outline:
					"bg-transparent border border-gray-7 hover:bg-gray-3 text-gray-12 disabled:bg-gray-8",
				white:
					"bg-transparent border border-gray-7 hover:bg-gray-3 text-gray-12 disabled:bg-gray-8",
				ghost: "hover:bg-white/20 hover:text-white",
				gray: "bg-gray-5 hover:bg-gray-7 border gray-button-border gray-button-shadow text-gray-12 disabled:border-gray-7 disabled:bg-gray-8 disabled:text-gray-11",
				dark: "bg-gray-12 dark-button-shadow hover:bg-gray-11 border dark-button-border text-gray-1 disabled:cursor-not-allowed disabled:text-gray-10 disabled:bg-gray-7 disabled:border-gray-8",
				darkgradient:
					"bg-linear-to-t button-gradient-border from-[#0f0f0f] to-[#404040] shadow-[0_0_0_1px] hover:brightness-110 shadow-[#383838] text-gray-50 hover:bg-[#383838] disabled:bg-[#383838] border-transparent",
				radialblue:
					"bg-biz-grad-vivid text-white border-0 shadow-[inset_0_0_7px_#ffffff59,0_16px_24px_#b13ec247] hover:brightness-110 active:bg-biz-grad disabled:bg-none disabled:bg-gray-6 disabled:shadow-none",
				transparent:
					"bg-transparent text-gray-10 hover:underline transition-all duration-200 hover:text-gray-12",
			},
			size: {
				xs: "text-xs h-[32px]",
				sm: "text-sm h-[40px]",
				md: "text-sm h-[44px]",
				lg: "text-md h-[48px]",
				icon: "h-9 w-9",
			},
		},
		compoundVariants: [
			{
				variant: ["primary", "blue", "radialblue"],
				size: ["xs", "sm", "icon"],
				className: "shadow-[inset_0_0_5px_#ffffff59,0_6px_12px_#b13ec233]",
			},
		],
	},
);

export interface ButtonProps
	extends React.ButtonHTMLAttributes<HTMLButtonElement>,
		VariantProps<typeof buttonVariants> {
	asChild?: boolean;
	spinner?: boolean;
	href?: string;
	spinnerColor?: string;
	spinnerBorderColor?: `rgba(${number},${number},${number},${number})`;
	spinnerClassName?: string;
	kbd?: string;
	icon?: React.ReactNode;
	target?: string;
	arrow?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
	(
		{
			className,
			variant,
			size,
			asChild = false,
			spinner = false,
			spinnerColor = "white",
			spinnerBorderColor = "rgba(255, 255, 255, 0.2)",
			spinnerClassName,
			href,
			kbd,
			icon,
			target,
			arrow = false,
			...props
		},
		ref,
	) => {
		const Comp = href ? "a" : asChild ? Slot : ("button" as any);
		return (
			<Comp
				className={classNames(
					buttonVariants({ variant, size }),
					arrow && "group gap-3 py-1.5 pr-1.5 pl-5",
					className,
				)}
				ref={ref as any}
				target={target || undefined}
				href={href || undefined}
				{...props}
			>
				{spinner && (
					<LoadingSpinner
						className={classNames("mr-1", spinnerClassName)}
						color={spinnerColor}
						borderColor={spinnerBorderColor}
						size={16}
					/>
				)}
				{icon && icon}
				{props.children}
				{arrow && (
					<span className="flex justify-center items-center bg-white rounded-full size-8 shrink-0 text-[#131313]">
						<ArrowUpRight className="transition-transform duration-200 size-4 group-hover:rotate-45" />
					</span>
				)}
				{kbd && (
					<kbd className="hidden justify-center items-center ml-1 text-xs rounded-full border md:flex size-5 bg-gray-11 border-gray-10">
						{kbd}
					</kbd>
				)}
			</Comp>
		);
	},
);
Button.displayName = "Button";

export { Button, buttonVariants };
