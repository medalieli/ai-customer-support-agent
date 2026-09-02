# syntax=docker/dockerfile:1.7
FROM node:22.20.0-alpine3.22 AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22.20.0-alpine3.22 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.20.0-alpine3.22 AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup --system --gid 10001 novacart && adduser --system --uid 10001 --ingroup novacart novacart
COPY --from=builder --chown=novacart:novacart /app/public ./public
COPY --from=builder --chown=novacart:novacart /app/.next/standalone ./
COPY --from=builder --chown=novacart:novacart /app/.next/static ./.next/static
USER novacart
EXPOSE 3000
CMD ["node", "server.js"]
