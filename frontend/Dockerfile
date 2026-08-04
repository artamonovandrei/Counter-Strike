# path: frontend/Dockerfile
# Builds the client and bakes it into a Caddy image that also reverse-proxies the API.
# Build context is the repository root, because the client's types come from shared/.
#   docker build -f frontend/Dockerfile -t webstrike-web .

# ─── build ────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS build

WORKDIR /build

# Install dependencies against the lockfile alone so this layer is cached independently
# of source changes. `npm ci` fails loudly if package.json and the lockfile disagree,
# which is what you want in a build.
COPY frontend/package.json frontend/package-lock.json ./frontend/
WORKDIR /build/frontend
RUN npm ci --no-audit --no-fund

WORKDIR /build
COPY shared ./shared
COPY frontend ./frontend

WORKDIR /build/frontend
RUN npm run build

# ─── serve ────────────────────────────────────────────────────────────────────
FROM caddy:2-alpine

COPY --from=build /build/frontend/dist /srv/www
COPY infra/Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=4s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/healthz || exit 1
