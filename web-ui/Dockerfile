# syntax=docker/dockerfile:1.7
FROM node:20.19.5-bookworm-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./

# Frontend compilation needs devDependencies such as TypeScript and Vite.
# Docker Desktop connections can be slow/unstable on the first dependency
# download, so use bounded npm retries/timeouts and persist the npm download
# cache across BuildKit builds. If npm still fails, print its debug log so the
# underlying network/package error is visible instead of only the generic
# "Exit handler never called" message.
ENV NODE_ENV=development \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_FETCH_RETRIES=6 \
    NPM_CONFIG_FETCH_RETRY_FACTOR=2 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=180000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000 \
    NPM_CONFIG_MAXSOCKETS=5

RUN --mount=type=cache,target=/root/.npm \
    set -eux; \
    node --version; \
    npm --version; \
    if ! npm ci --include=dev; then \
      status=$?; \
      echo "npm ci failed; recent npm debug log follows:"; \
      find /root/.npm/_logs -type f -name '*-debug-0.log' -print -exec tail -n 160 {} \; 2>/dev/null || true; \
      exit "$status"; \
    fi; \
    test -x node_modules/.bin/tsc; \
    test -x node_modules/.bin/vite

COPY . .

ARG VITE_API_BASE_URL=http://localhost:8081
ARG VITE_LLM_HOST_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_LLM_HOST_URL=$VITE_LLM_HOST_URL

RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
