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
# chown during the copy rather than as a separate RUN. A trailing
# `RUN chown -R nextjs:nodejs /workspace` walks the whole hoisted
# node_modules tree (~1M files) and, because each RUN commits a new layer,
# writes a second complete copy of /workspace first. On a nearly-full disk
# that duplicate is what pushed the build past the disk and stalled it
# indefinitely. COPY --chown applies ownership as the files are written, so
# there is no extra layer and no second pass.
COPY --chown=nextjs:nodejs --from=builder /workspace /workspace
USER nextjs
WORKDIR /workspace/apps/web
# The standalone server resolves assets from its own tree, not from the source
# .next, so static and public have to be copied alongside it. Without these the
# container boots and answers 200 but serves no JS or CSS, which looks
# identical to the CSP/nonce failure it is not.
COPY --chown=nextjs:nodejs --from=builder /workspace/apps/web/.next/static ./.next/standalone/apps/web/.next/static
COPY --chown=nextjs:nodejs --from=builder /workspace/apps/web/public ./.next/standalone/apps/web/public
EXPOSE 3000
ENV PORT=3000
# `next.config.ts` sets `output: 'standalone'`, and Next refuses to run
# `next start` in that mode: the process exits with
#   "next start" does not work with "output: standalone" configuration
# so the container never served a request. The standalone build emits its own
# server under .next/standalone, and in this workspace layout it lands at
# .next/standalone/apps/web/server.js.
#
# Nothing in CI caught this because the workflow builds the web image but never
# *starts* it, so a container that cannot boot was invisible to the pipeline.
# Verify any change here with: node scripts/browser-check.mjs /login
ENV HOSTNAME=0.0.0.0
CMD ["node", ".next/standalone/apps/web/server.js"]