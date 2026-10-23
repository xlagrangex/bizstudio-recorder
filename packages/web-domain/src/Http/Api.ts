import { HttpApi, OpenApi } from "@effect/platform";
import { EXTENSION_HTTP_PREFIX, ExtensionHttpApi } from "../Extension.ts";

export class ApiContract extends HttpApi.make("cap-web-api")
	.add(ExtensionHttpApi.prefix(EXTENSION_HTTP_PREFIX))
	.annotateContext(
		OpenApi.annotations({
			title: "Bizstudio Recorder HTTP API",
			description: "Internal API used by the desktop app and external services",
		}),
	)
	.prefix("/api") {}
