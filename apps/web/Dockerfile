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
CMD ["./node_modules/.bin/next", "start"]