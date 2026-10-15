ARG NODE_IMAGE
ARG NGINX_IMAGE
FROM ${NODE_IMAGE} AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit
COPY frontend/ .
RUN npm run build
FROM ${NGINX_IMAGE}
ARG SOURCE_SHA=development
LABEL org.opencontainers.image.revision=$SOURCE_SHA
COPY --from=build /web/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
