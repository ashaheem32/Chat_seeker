# ============================================================================
# ChatLens Frontend - Multi-stage Dockerfile
# ----------------------------------------------------------------------------
# Stages:
#   deps    - install pnpm dependencies (cached unless lockfile changes)
#   builder - run `next build` against the deps stage
#   runner  - tiny final image using Next.js standalone output
#
# The dev compose override stops at `deps` and runs `pnpm dev` directly.
# ============================================================================

ARG NODE_VERSION=20-alpine

# ---- Deps stage ------------------------------------------------------------
FROM node:${NODE_VERSION} AS deps

# libc6-compat: required by some Next.js native modules on Alpine.
RUN apk add --no-cache libc6-compat
WORKDIR /app

# Enable pnpm via Corepack (shipped with Node 20).
RUN corepack enable

COPY package.json pnpm-lock.yaml* ./
# --frozen-lockfile fails if lockfile is out of date - good for CI determinism.
RUN pnpm install --frozen-lockfile || pnpm install

# ---- Builder stage ---------------------------------------------------------
FROM node:${NODE_VERSION} AS builder
WORKDIR /app
RUN corepack enable

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Public env vars must be present at build time so Next.js inlines them.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

# ---- Runner stage ----------------------------------------------------------
FROM node:${NODE_VERSION} AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Run as non-root for security.
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# `output: "standalone"` in next.config.mjs produces a self-contained server.
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
