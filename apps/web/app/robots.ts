import { buildEnv, serverEnv } from "@cap/env";
import type { MetadataRoute } from "next";
import { connection } from "next/server";

const sitemapUrl = () =>
	new URL("/sitemap.xml", serverEnv().WEB_URL).toString();

export default async function robots(): Promise<MetadataRoute.Robots> {
	if (buildEnv.NEXT_PUBLIC_IS_CAP !== "true") {
		// Without a dynamic API this route is prerendered at build time and
		// would bake the build's WEB_URL instead of the instance's own.
		await connection();
		return {
			rules: [{ userAgent: "*", allow: ["/s/", "/embed/"], disallow: "/" }],
			sitemap: sitemapUrl(),
		};
	}

	return {
		rules: [
			{
				userAgent: "*",
				allow: "/",
				disallow: ["/dashboard", "/login", "/invite", "/onboarding", "/record"],
			},
		],
		sitemap: sitemapUrl(),
	};
}
