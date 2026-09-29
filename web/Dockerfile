# Development image for the dashboard, as `make dev` runs it. docker compose mounts the source
# over /app; node_modules stays in the image. File-change events don't reliably cross Docker
# Desktop's file sharing, so for dashboard work run `npm run dev` on the host instead (it
# proxies /api to the API on localhost:8001), or `docker compose restart web` after edits.
FROM node:24-alpine
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
