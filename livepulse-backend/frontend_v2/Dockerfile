# frontend_v2 production image — vite build + nginx static serve.
# Multi-stage so the final image stays minimal (~25 MB based on alpine).

# ---- Stage 1: build ----
FROM node:20-alpine AS builder
WORKDIR /app

# Copy package manifests first so npm ci layer caches when only src changes.
COPY package.json package-lock.json ./
# --legacy-peer-deps: @lobehub/icons/ui 要 antd6,项目锁 antd5,peer 冲突;
# 与本地 dev 安装方式一致,不改依赖版本(改版本风险更大)。
RUN npm ci --legacy-peer-deps --no-audit --no-fund

COPY . .
RUN npm run build

# ---- Stage 2: serve ----
FROM nginx:1.27-alpine

# Minimal SPA fallback config — the OUTER nginx (deploy/prod/nginx) handles
# TLS + reverse proxy. This inner one only serves dist/ and handles client-
# side routing fallback (so /sessions/123 doesn't 404 on hard refresh).
RUN echo 'server { \
    listen 80; \
    server_name _; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { try_files $uri $uri/ /index.html; } \
}' > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
