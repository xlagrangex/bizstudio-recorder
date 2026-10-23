// Speaks the slice of the Resend HTTP API that Cap's SDK calls, so cap-web can
// send through Brevo SMTP via RESEND_BASE_URL without patching the upstream image.
import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import nodemailer from "nodemailer";

const {
	BREVO_SMTP_HOST,
	BREVO_SMTP_PORT = "587",
	BREVO_SMTP_LOGIN,
	BREVO_SMTP_KEY,
	RELAY_TOKEN,
	MAIL_FROM = "Bizstudio Recorder <crm@bizstudio.it>",
	PORT = "8025",
} = process.env;

const transport = nodemailer.createTransport({
	host: BREVO_SMTP_HOST,
	port: Number(BREVO_SMTP_PORT),
	auth: { user: BREVO_SMTP_LOGIN, pass: BREVO_SMTP_KEY },
});

const list = (value) =>
	value == null ? undefined : Array.isArray(value) ? value : [value];

const toMessage = (body) => ({
	from: MAIL_FROM,
	to: list(body.to),
	cc: list(body.cc),
	bcc: list(body.bcc),
	replyTo: body.reply_to ?? body.replyTo,
	subject: body.subject,
	html: body.html,
	text: body.text,
	attachments: body.attachments?.map((a) => ({
		filename: a.filename,
		content: Array.isArray(a.content)
			? Buffer.from(a.content)
			: Buffer.from(a.content ?? "", "base64"),
		contentType: a.content_type ?? a.contentType,
	})),
});

const reply = (res, status, payload) => {
	res.writeHead(status, { "content-type": "application/json" });
	res.end(JSON.stringify(payload));
};

const readJson = async (req) => {
	const chunks = [];
	for await (const chunk of req) chunks.push(chunk);
	return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
};

createServer(async (req, res) => {
	if (req.method === "GET" && req.url === "/health") return reply(res, 200, {});
	if (req.headers.authorization !== `Bearer ${RELAY_TOKEN}`)
		return reply(res, 401, { name: "unauthorized", message: "bad token" });

	try {
		const body = await readJson(req);
		if (req.method === "POST" && req.url === "/emails") {
			await transport.sendMail(toMessage(body));
			return reply(res, 200, { id: randomUUID() });
		}
		if (req.method === "POST" && req.url === "/emails/batch") {
			const data = [];
			for (const item of body) {
				await transport.sendMail(toMessage(item));
				data.push({ id: randomUUID() });
			}
			return reply(res, 200, { data });
		}
		return reply(res, 404, { name: "not_found", message: req.url });
	} catch (error) {
		console.error("send failed", error);
		return reply(res, 500, {
			name: "application_error",
			message: String(error?.message ?? error),
		});
	}
}).listen(Number(PORT), () => console.log(`brevo-relay on :${PORT}`));
