FROM node:20.18-alpine AS builder  # pinned to 20.18.0
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/web ./apps/web
COPY packages ./packages
RUN npm ci --legacy-peer-deps --no-audit --no-fund
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace=wildframe-web

FROM node:20.18-alpine AS runner   # pinned to 20.18.0
WORKDIR /workspace
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=builder /workspace /workspace
# No chown: keep /workspace read-only; /tmp is writable for Next.js
USER nextjs
WORKDIR /workspace/apps/web
EXPOSE 3000
ENV PORT=3000
CMD ["./node_modules/.bin/next", "start"]