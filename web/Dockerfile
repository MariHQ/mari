# Mari web — Vite build, served by nginx with API proxying.
#
# Build context is the REPOSITORY ROOT, not web/, because the app compiles
# against the component library in the vendor/mari-design submodule:
#
#   docker build -f web/Dockerfile .
#
# Clone with --recurse-submodules, or the build fails on a missing vendor dir.
#
# Both base images are pinned by tag only. To pin by digest, resolve each with
#   docker buildx imagetools inspect <image:tag> --format '{{.Manifest.Digest}}'
# and write FROM <image:tag>@sha256:<digest>.
FROM node:22-alpine AS build
WORKDIR /app

COPY web/package.json web/package-lock.json ./
RUN npm ci

# The library first: it changes far less often than the app, so this layer
# survives most rebuilds. Only components/ is compiled against. The path must
# be /vendor/... — web/lib-path.mjs looks one level above the app directory,
# exactly as it does in a checkout.
COPY vendor/mari-design/components/ /vendor/mari-design/components/
COPY web/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
