FROM node:20-bookworm-slim AS build
WORKDIR /app

COPY package.json package-lock.json ./
# Building the production bundle requires devDependencies such as TypeScript
# and Vite. Use the Debian-based Node image for a more stable npm install on
# Docker Desktop and explicitly verify the expected build executables exist.
ENV NODE_ENV=development
RUN node --version \
    && npm --version \
    && npm ci --include=dev \
    && test -x node_modules/.bin/tsc \
    && test -x node_modules/.bin/vite

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
