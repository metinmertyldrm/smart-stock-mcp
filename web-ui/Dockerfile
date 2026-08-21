FROM node:20-alpine AS build

WORKDIR /app
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci

COPY web-ui/ ./

ARG VITE_API_BASE_URL=http://localhost:8081
ARG VITE_LLM_HOST_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_LLM_HOST_URL=${VITE_LLM_HOST_URL}

RUN npm run build

FROM nginx:1.27-alpine

COPY web-ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
