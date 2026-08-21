# syntax=docker/dockerfile:1.7
FROM node:20.19.5-bookworm-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./

# Frontend compilation needs devDependencies such as TypeScript and Vite.
# Docker Desktop connections can be slow/unstable on the first dependency
# download, so use bounded npm retries/timeouts and persist the npm download
# cache across BuildKit builds. npm has also been observed to print an internal
# error while returning a zero status, so validate the installed executables
# independently and print the debug log whenever the install is incomplete.
ENV NODE_ENV=development \
    NPM_CONFIG_REGISTRY=https://registry.npmjs.org/ \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_PREFER_OFFLINE=true \
    NPM_CONFIG_FETCH_RETRIES=6 \
    NPM_CONFIG_FETCH_RETRY_FACTOR=2 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=180000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000 \
    NPM_CONFIG_MAXSOCKETS=5

RUN --mount=type=cache,target=/root/.npm \
    set -u; \
    node --version; \
    npm --version; \
    npm config get registry; \
    npm ci --include=dev; \
    npm_status=$?; \
    if [ "$npm_status" -ne 0 ] || [ ! -x node_modules/.bin/tsc ] || [ ! -x node_modules/.bin/vite ]; then \
      echo "npm install incomplete (status=$npm_status); recent npm debug log follows:"; \
      find /root/.npm/_logs -type f -name '*-debug-0.log' -print -exec tail -n 200 {} \; 2>/dev/null || true; \
      exit 1; \
    fi

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
