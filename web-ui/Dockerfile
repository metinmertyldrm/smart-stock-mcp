FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
# The production image needs a production bundle, but building that bundle
# requires devDependencies such as TypeScript and Vite. Explicitly include
# them so local npm/Docker configuration cannot omit `tsc` or `vite`.
ENV NODE_ENV=development
RUN npm ci --include=dev

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
