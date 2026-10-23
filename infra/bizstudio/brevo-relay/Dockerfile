FROM node:22-alpine
WORKDIR /app
RUN npm install --omit=dev nodemailer@6.10.1
COPY server.mjs ./
EXPOSE 8025
CMD ["node", "server.mjs"]
