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
# `output: 'standalone'` already emits a pruned, self-contained tree with only
# the node_modules the server actually loads. Copying that instead of the whole
# builder /workspace is the canonical pattern: the full copy drags in the
# hoisted dev node_modules (~1M files, multiple GB) and was what filled the
# disk and stalled this step. The tree is rooted so its server.js lands at
# apps/web/server.js.
#
# static and public are not in the standalone tree — the server serves them from
# beside itself. Without these the container boots, answers 200, and serves no
# JS or CSS, which is indistinguishable from a CSP/nonce failure.
COPY --chown=nextjs:nodejs --from=builder /workspace/apps/web/.next/standalone ./
COPY --chown=nextjs:nodejs --from=builder /workspace/apps/web/.next/static ./apps/web/.next/static
COPY --chown=nextjs:nodejs --from=builder /workspace/apps/web/public ./apps/web/public
USER nextjs
WORKDIR /workspace/apps/web
EXPOSE 3000
ENV PORT=3000
# `next.config.ts` sets `output: 'standalone'`, and Next refuses to run
# `next start` in that mode: the process exits with
#   "next start" does not work with "output: standalone" configuration
# so the container never served a request under the old CMD.
#
# Nothing in CI caught this because the workflow builds the web image but never
# *starts* it, so a container that cannot boot was invisible to the pipeline.
# Verify any change here with: node scripts/browser-check.mjs /login
ENV HOSTNAME=0.0.0.0
CMD ["node", "server.js"]