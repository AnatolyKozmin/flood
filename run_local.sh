#!/usr/bin/env bash
# Локальный запуск БотИКа: венв, зависимости, токен, старт.
# Использование:  ./run_local.sh
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "→ Создаю виртуальное окружение (.venv)"
  "$PYTHON" -m venv .venv
fi

echo "→ Ставлю зависимости"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ] || ! grep -q '^TOKEN=' .env; then
  echo
  echo "Нужен токен бота от @BotFather."
  read -rp "Вставь токен: " BOT_TOKEN
  if [ -z "$BOT_TOKEN" ]; then
    echo "Пустой токен — выхожу." >&2
    exit 1
  fi
  echo "TOKEN=$BOT_TOKEN" >> .env
  echo "→ Записал в .env (он в .gitignore, в репозиторий не уедет)"
fi

echo
echo "→ Запускаю бота. Остановить — Ctrl+C"
echo
exec ./.venv/bin/python main.py
