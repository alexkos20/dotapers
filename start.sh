#!/usr/bin/env bash
# ============================================================
#  Стартовый скрипт SPBU Dashboard
#  1) выполняет ETL (загружает/обновляет данные из OpenAlex)
#  2) запускает дашборд
#  Использование:
#     ./start.sh                 — ETL (один прогон) + запуск дашборда
#     ./start.sh --no-etl        — только запуск дашборда (без ETL)
#     ./start.sh --etl-only      — только ETL, без запуска дашборда
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"
PY="${PYTHON:-.venv/bin/python}"

# Проверяем наличие venv
if [ ! -x "$PY" ]; then
    echo "Ошибка: venv не найден (ожидается $PY)."
    echo "Создайте venv:  python3 -m venv .venv"
    echo "Установите зависимости:  .venv/bin/pip install -r requirements.txt"
    exit 1
fi

NO_ETL=0
ETL_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --no-etl)   NO_ETL=1 ;;
        --etl-only) ETL_ONLY=1 ;;
        *)
            echo "Неизвестный аргумент: $arg"
            echo "Допустимые: --no-etl | --etl-only"
            exit 1
            ;;
    esac
done

# Шаг 1: ETL (если нужен)
if [ "$NO_ETL" -eq 0 ]; then
    echo "=== [1/2] Запуск ETL (загрузка данных из OpenAlex) ==="
    ( cd app && "$OLDPWD/$PY" etl.py --once )
    echo "=== ETL завершён ==="
fi

if [ "$ETL_ONLY" -eq 1 ]; then
    echo "=== Только ETL. Завершение. ==="
    exit 0
fi

# Шаг 2: запуск дашборда
echo "=== [2/2] Запуск дашборда на http://localhost:8050 ==="
( cd app && "$OLDPWD/$PY" app.py )
