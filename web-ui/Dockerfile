# syntax=docker/dockerfile:1.7
FROM node:20.19.5-bookworm-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
COPY .docker-certs/ /tmp/npm-extra-certs/

# Frontend compilation needs devDependencies such as TypeScript and Vite.
# Local HTTPS-inspection products may re-sign registry.npmjs.org with a private
# CA trusted by the host but not by Linux containers. Any PEM/CRT files placed
# in web-ui/.docker-certs are concatenated and exposed only to Node processes in
# this build stage through NODE_EXTRA_CA_CERTS. They are not copied to the final
# nginx image. npm retry/timeouts also tolerate slow first-time Docker downloads.
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
    extra_ca=/tmp/npm-extra-ca.pem; \
    find /tmp/npm-extra-certs -type f \( -name '*.pem' -o -name '*.crt' \) -exec cat {} \; > "$extra_ca"; \
    if [ -s "$extra_ca" ]; then \
      export NODE_EXTRA_CA_CERTS="$extra_ca"; \
      echo "Using local extra CA certificates for npm TLS verification."; \
    fi; \
    node --version; \
    npm --version; \
    npm ci --include=dev; \
    install_status=$?; \
    if [ "$install_status" -ne 0 ] || [ ! -x node_modules/.bin/tsc ] || [ ! -x node_modules/.bin/vite ]; then \
      echo "npm install incomplete; recent npm debug log follows:"; \
      find /root/.npm/_logs -type f -name '*-debug-0.log' -print -exec tail -n 180 {} \; 2>/dev/null || true; \
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
