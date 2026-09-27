#!/bin/bash
# Подключение нового чата разом: бэкап -> пересборка статистики из выгрузки
# -> добивка тегов через API -> сводка. Повторяемо для любого чата.
# Использование на сервере из корня проекта:
#   ./scripts/chat_bootstrap.sh /tmp/result.json [--chat-id -100123]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "Использование: $0 /путь/к/result.json [--chat-id -100123]" >&2
  exit 1
fi
EXPORT="$1"
shift || true

STAMP=$(date +%F_%H%M)
cp database/bot.db "/tmp/bot.before_bootstrap_${STAMP}.db"
echo "бэкап: /tmp/bot.before_bootstrap_${STAMP}.db"

cp "$EXPORT" database/result.json
docker compose exec bot python scripts/rebuild_stats.py /app/database/result.json "$@"
rm database/result.json

docker compose exec bot python scripts/resolve_usernames.py

python3 - <<'EOF'
import sqlite3
con = sqlite3.connect('database/bot.db')
print("--- всего по чатам ---")
for r in con.execute("SELECT chat_id, SUM(count) FROM message_stats GROUP BY chat_id ORDER BY 2 DESC"):
    print(f"  {r[0]}: {r[1]}")
print("--- без тега ---")
rows = con.execute("SELECT COUNT(*) FROM stats_users WHERE username IS NULL").fetchone()
print(f"  осталось: {rows[0]}")
con.close()
EOF
