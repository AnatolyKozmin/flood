#!/bin/bash
# Стянуть живую базу с сервера в локальную database/bot.db.
# Живую не трогаем: на сервере делаем консистентный снапшот через
# sqlite backup API (бот в этот момент продолжает писать).
set -e
cd "$(dirname "$0")/.."
if [ -f database/bot.db ]; then
  cp database/bot.db "database/bot.db.local.bak_$(date +%F_%H%M)"
fi
ssh root@82.22.38.111 'python3 -c '"'"'import sqlite3; s=sqlite3.connect("/root/flood/database/bot.db"); d=sqlite3.connect("/tmp/bot.copy.db"); s.backup(d); d.close(); s.close(); print("snapshot ok")'"'"
scp root@82.22.38.111:/tmp/bot.copy.db database/bot.db
echo "database/bot.db обновлена с сервера"
