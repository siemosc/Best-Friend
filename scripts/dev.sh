#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
# dev.sh — запуск дев-стека BestFiend одной командой.
#
# Дев-стек = 2 контейнера (postgres, seaweedfs) + 2 процесса (core, web).
#
# По умолчанию поднимает всё. Флаги отключают части:
#
#   ./scripts/dev.sh                  # всё
#   ./scripts/dev.sh --infra-only     # только Docker-инфра
#   ./scripts/dev.sh --services-only  # только процессы, без Docker
#   ./scripts/dev.sh --no-ui          # без web dev-сервера (порт 5173)
#
# Ctrl+C — остановит процессы, Docker-контейнеры остаются.
# Для остановки Docker: docker compose down
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Цвета ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;90m'
CORE_COLOR='\033[1;35m'
WEB_COLOR='\033[1;37m'
NC='\033[0m'

# ── Парсинг аргументов ───────────────────────────────────────────
INFRA_ONLY=false
SERVICES_ONLY=false
WITH_UI=true

for arg in "$@"; do
    case "$arg" in
        --infra-only)    INFRA_ONLY=true ;;
        --services-only) SERVICES_ONLY=true ;;
        --no-ui)         WITH_UI=false ;;
        --help|-h)
            sed -n '3,15p' "$0"
            exit 0
            ;;
        *)
            echo -e "${RED}Неизвестный аргумент: $arg${NC}" >&2
            exit 1
            ;;
    esac
done

# ── Проверка зависимостей ────────────────────────────────────────
for cmd in docker uv; do
    if ! command -v "$cmd" &>/dev/null; then
        echo -e "${RED}Не найден: $cmd${NC}" >&2
        exit 1
    fi
done

# ── PID-файл для cleanup ─────────────────────────────────────────
LOGS_DIR="$REPO_ROOT/logs/dev"
PIDFILE="$LOGS_DIR/.pids"
mkdir -p "$LOGS_DIR"
: > "$PIDFILE"

cleanup() {
    echo ""
    echo -e "${YELLOW}⏹  Останавливаю процессы...${NC}"
    while IFS= read -r pid; do
        # taskkill есть только в Windows-окружении (Git Bash), там kill не валит дерево процессов
        if command -v taskkill &>/dev/null; then
            taskkill //F //PID "$pid" //T 2>/dev/null || true
        else
            kill "$pid" 2>/dev/null || true
        fi
    done < "$PIDFILE"
    rm -f "$PIDFILE"
    echo -e "${GREEN}✅ Все процессы остановлены.${NC}"
    echo -e "${GRAY}Docker-контейнеры работают. Для остановки: docker compose down${NC}"
}

trap cleanup EXIT INT TERM

# ── Docker-инфра ─────────────────────────────────────────────────
postgres_ready() {
    docker exec bestfiend-postgres pg_isready -U bestfiend &>/dev/null
}

seaweedfs_ready() {
    [ "$(docker inspect -f '{{.State.Health.Status}}' bestfiend-seaweedfs 2>/dev/null)" = "healthy" ]
}

wait_for_container() {
    local label="$1" probe="$2"
    echo -e "${CYAN}⏳ Жду готовности ${label}...${NC}"
    for _ in $(seq 1 30); do
        if "$probe"; then
            echo -e "${GREEN}✅ ${label} готов.${NC}"
            return 0
        fi
        sleep 1
    done
    echo -e "${RED}❌ ${label} не поднялся за 30 секунд.${NC}" >&2
    return 1
}

if ! $SERVICES_ONLY; then
    echo -e "${CYAN}🐳 Поднимаю Docker-инфру...${NC}"
    docker compose up -d
    echo -e "${GREEN}✅ Docker-инфра запущена.${NC}"

    wait_for_container "PostgreSQL" postgres_ready || exit 1
    wait_for_container "SeaweedFS" seaweedfs_ready || exit 1

    if $INFRA_ONLY; then
        echo -e "${GREEN}Режим --infra-only, процессы не запускаются.${NC}"
        echo -e "${GRAY}Для остановки: docker compose down${NC}"
        trap - EXIT INT TERM
        exit 0
    fi
else
    echo -e "${CYAN}⚡ Docker пропущен.${NC}"
fi

# ── Локальные процессы ───────────────────────────────────────────
start_process() {
    local name="$1" dir="$2" cmd="$3" color="$4"
    local logfile="$LOGS_DIR/${name}.log"

    echo -e "${color}▶ ${name}${NC}  ${GRAY}→ ${logfile}${NC}"
    (
        cd "$dir"
        PYTHONUNBUFFERED=1 eval "$cmd" >> "$logfile" 2>&1
    ) &
    echo "$!" >> "$PIDFILE"
}

echo ""
echo -e "${CYAN}🚀 Core-сервис...${NC}"
start_process "core" "$REPO_ROOT/core" "CORE_PORT=8010 uv run python -m bestfiend" "$CORE_COLOR"

WEB_STARTED=false
if $WITH_UI; then
    if ! command -v pnpm &>/dev/null; then
        echo -e "${YELLOW}⚠️  pnpm не найден — web_ui не запущен.${NC}"
        echo -e "${GRAY}   Установи: npm install -g pnpm${NC}"
    elif [ ! -d "$REPO_ROOT/web/node_modules" ]; then
        echo -e "${YELLOW}⚠️  web: node_modules не найдены. Запусти 'pnpm install' в web/.${NC}"
    else
        echo ""
        echo -e "${CYAN}🖥  Web UI (порт 5173)...${NC}"
        start_process "web_ui" "$REPO_ROOT/web" "pnpm dev --host 127.0.0.1 --port 5173" "$WEB_COLOR"
        WEB_STARTED=true
    fi
fi

# ── Health-check ─────────────────────────────────────────────────
CORE_PROBE_URL="http://localhost:8010/health"
WEB_PROBE_URL="http://localhost:5173/"

responds() {
    curl -sf --max-time 1 "$1" > /dev/null 2>&1
}

print_health_row() {
    local name="$1" probe_url="$2" shown_url="$3"
    if responds "$probe_url"; then
        echo -e "  ${GREEN}✅ ${name}${NC}  → ${shown_url}"
    else
        echo -e "  ${RED}❌ ${name}${NC}  → ${shown_url}  ${GRAY}(logs/dev/${name}.log)${NC}"
    fi
}

echo ""
echo -e "${CYAN}⏳ Жду запуска (до 30с)...${NC}"

for _ in $(seq 1 15); do
    all_up=true
    responds "$CORE_PROBE_URL" || all_up=false
    if $WEB_STARTED; then
        responds "$WEB_PROBE_URL" || all_up=false
    fi
    if $all_up; then break; fi
    sleep 2
done

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Core-сервис:${NC}"
print_health_row "core" "$CORE_PROBE_URL" "http://localhost:8010"
if $WEB_STARTED; then
    echo -e "${GREEN}  Web UI:${NC}"
    print_health_row "web_ui" "$WEB_PROBE_URL" "http://localhost:5173"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GRAY}  Логи:  logs/dev/<service>.log${NC}"
echo -e "${GRAY}  Ctrl+C для остановки${NC}"
echo ""

# Блокируем до Ctrl+C
while true; do
    sleep 5
done
