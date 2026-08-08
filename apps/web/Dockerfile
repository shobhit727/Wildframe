FROM node:20-alpine AS builder
WORKDIR /workspace
RUN npm install -g pnpm@10
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml .pnpmfile.cjs ./
COPY apps/web ./apps/web
RUN pnpm install --frozen-lockfile
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter wildframe-web build

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
CMD ["./node_modules/.bin/next", "start"]