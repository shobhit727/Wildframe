FROM node:20-alpine AS builder
WORKDIR /workspace
COPY package.json package-lock.json ./
COPY apps/web ./apps/web
COPY packages ./packages
RUN npm ci --legacy-peer-deps --no-audit --no-fund
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build --workspace=wildframe-web

FROM node:20-alpine AS runner
WORKDIR /workspace
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup -S nodejs && adduser -S nextjs -G nodejs
COPY --from=builder /workspace /workspace
RUN chown -R nextjs:nodejs /workspace
USER nextjs
WORKDIR /workspace/apps/web
EXPOSE 3000
ENV PORT=3000
# Run the workspace's own start script rather than a relative path to the binary.
# This is an npm workspace monorepo, so `npm ci` at /workspace hoists `next` into
# /workspace/node_modules/.bin. The old CMD resolved ./node_modules/.bin/next
# against WORKDIR /workspace/apps/web, where no node_modules exists, so the
# container died at startup with MODULE_NOT_FOUND on its own entrypoint:
#   Error: Cannot find module '/workspace/apps/web/node_modules/.bin/next'
# `npm run --workspace` resolves the binary the same way the scripts do, so it
# keeps working regardless of where npm decides to hoist.
CMD ["npm", "run", "start", "--workspace=wildframe-web"]