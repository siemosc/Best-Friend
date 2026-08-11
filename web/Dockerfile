# syntax=docker/dockerfile:1

# --- Стадия сборки: собираем SvelteKit SPA через pnpm ---
FROM node:24-alpine AS build
WORKDIR /src

# pnpm ставим детерминированно — мажор берём из lockfileVersion 9.0 (pnpm-lock.yaml)
RUN npm install -g pnpm@9

# Сначала манифесты — слой установки зависимостей кешируется отдельно от исходников.
# .npmrc содержит engine-strict, тащим его вместе с манифестами.
COPY package.json pnpm-lock.yaml .npmrc ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir=/pnpm-store

# Затем исходники и сборка. adapter-static в SPA-режиме кладёт результат в build/
COPY . .
RUN pnpm build

# --- Стадия рантайма: раздаём статику через Caddy ---
FROM caddy:2-alpine AS runtime

# Non-root: caddy бинарю даём cap_net_bind_service, чтобы держать :80 без root.
# Каталоги состояния (/config, /data) и статики (/srv) отдаём во владение caddy-пользователю.
RUN apk add --no-cache libcap \
 && setcap 'cap_net_bind_service=+ep' /usr/bin/caddy \
 && addgroup -g 10001 -S caddy \
 && adduser -u 10001 -S -D -H -G caddy caddy \
 && mkdir -p /srv \
 && chown -R caddy:caddy /srv /config /data

# Наш конфиг вместо дефолтного Caddyfile
COPY Caddyfile /etc/caddy/Caddyfile

# Собранная статика из build-стадии
COPY --from=build --chown=caddy:caddy /src/build /srv

# Проба через busybox wget — есть в alpine; корень отдаётся SPA-fallback'ом (index.html, 200)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "--spider", "http://127.0.0.1:80/"]

USER caddy
EXPOSE 80

# ENTRYPOINT/CMD наследуем от базового образа: caddy run --config /etc/caddy/Caddyfile
