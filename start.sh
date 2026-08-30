#!/usr/bin/env bash
# ============================================================
#  Стартовый скрипт SPBU Dashboard
#  Данные загружаются автоматически при первом запуске:
#  если pure_data.db пуст/отсутствует, приложение само запустит ETL.
#  Использование:
#     ./start.sh               — запуск дашборда (ETL стартует сам, если данных нет)
#     ./start.sh --force-etl   — принудительно обновить данные из OpenAlex, затем запуск
#     ./start.sh --etl-only    — только обновить данные, без запуска дашборда
#     ./start.sh --no-etl      — запуск без авто-загрузки данных (для отладки)
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

# PY_BIN — путь к интерпретатору для запуска из подкаталога app/:
# относительный PY (по умолчанию .venv/bin/python) дополняем до корня репо
# ($(pwd) после cd "$(dirname "$0")"), абсолютный PYTHON используем как есть.
# Иначе абсолютный путь в "$OLDPWD/$PY" превратился бы в /репо//usr/bin/python.
if [ "${PY#/}" != "$PY" ]; then
    PY_BIN="$PY"
else
    PY_BIN="$(pwd)/$PY"
fi

NO_ETL=0
ETL_ONLY=0
FORCE_ETL=0

for arg in "$@"; do
    case "$arg" in
        --no-etl)     NO_ETL=1 ;;
        --etl-only)   ETL_ONLY=1 ;;
        --force-etl)  FORCE_ETL=1 ;;
        *)
            echo "Неизвестный аргумент: $arg"
            echo "Допустимые: --force-etl | --etl-only | --no-etl"
            exit 1
            ;;
    esac
done

# Принудительный ETL (обновление данных) — только если явно попросили.
if [ "$FORCE_ETL" -eq 1 ]; then
    echo "=== ETL: обновление данных из OpenAlex ==="
    ( cd app && "$PY_BIN" etl.py --once )
    echo "=== ETL завершён ==="
fi

if [ "$ETL_ONLY" -eq 1 ]; then
    echo "=== Только ETL. Завершение. ==="
    exit 0
fi

# Запуск дашборда. ETL запустится сам внутри приложения, если данных нет.
# --no-etl отключает авто-загрузку (приложение стартует с тем, что есть).
echo "=== Запуск дашборда на http://localhost:8050 ==="
if [ "$NO_ETL" -eq 1 ]; then
    ( cd app && AUTO_ETL=0 "$PY_BIN" app.py )
else
    ( cd app && "$PY_BIN" app.py )
fi
