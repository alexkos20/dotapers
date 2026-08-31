import os
import sqlite3
import time
import requests
from datetime import datetime
from contextlib import contextmanager
from utils import (
    MAIN_AUTHORS, BASE_URL, DB_PATH, logger, timeit,
    compute_hash, clear_all_caches
)
import pandas as pd

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """Контекст соединения с БД: commit при успехе, rollback при ошибке, close всегда.

    ВАЖНО: `with sqlite3.connect(...) as conn:` НЕ закрывает соединение (только
    коммитит/откатывает). Здесь соединение закрывается явно — иначе каждое
    открытие (а run_etl открывает соединение на каждую работу!) держало бы
    файловый дескриптор до сборки мусора.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_session() as conn:
        cursor = conn.cursor()
        # Таблица публикаций
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY,
                title TEXT,
                publication_year INTEGER,
                cited_by_count INTEGER,
                topics TEXT,
                journal TEXT,
                updated_date TEXT,
                content_hash TEXT,
                last_loaded TIMESTAMP
            )
        """)
        # Таблица авторов
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authors (
                id TEXT PRIMARY KEY,
                name TEXT,
                orcid TEXT,
                affiliation TEXT,
                last_loaded TIMESTAMP
            )
        """)
        # Таблица связей (автор ↔ публикация)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorship (
                author_id TEXT,
                publication_id TEXT,
                PRIMARY KEY (author_id, publication_id),
                FOREIGN KEY (author_id) REFERENCES authors(id),
                FOREIGN KEY (publication_id) REFERENCES publications(id)
            )
        """)
        # Таблица метаданных ETL
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS etl_metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP
            )
        """)
        conn.commit()
        logger.info("База данных инициализирована")


def get_last_etl_time():
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM etl_metadata WHERE key = 'last_etl_run'")
        row = cursor.fetchone()
    return row[0] if row else None


def update_etl_metadata(total_added=0, total_updated=0, total_failed=0):
    with db_session() as conn:
        cursor = conn.cursor()
        now = datetime.now().isoformat()

        metadata = [
            ('last_etl_run', now),
            ('records_added', str(total_added)),
            ('records_updated', str(total_updated)),
            ('records_failed', str(total_failed)),
        ]

        for key, val in metadata:
            cursor.execute(
                "INSERT OR REPLACE INTO etl_metadata (key, value, updated_at) VALUES (?, ?, ?)",
                (key, val, now)
            )
        conn.commit()
        logger.info(f"Метаданные ETL обновлены: добавлено {total_added}, обновлено {total_updated}")

def _normalize_name(name):
    """Нормализация имени для сравнения: нижний регистр, только буквы/цифры.

    Пробелы, дефисы, точки в инициалах не учитываются: 'E. A. Lejnina' ==
    'EA Lejnina' == 'e a lejnina'.
    """
    if not name:
        return ''
    return ''.join(ch for ch in str(name).lower() if ch.isalnum())


def get_author_id(display_name, retries=3):
    """Возвращает author_id по имени, требуя точного совпадения display_name.

    Поиск OpenAlex возвращает кандидатов по релевантности, и первый из них может
    оказаться однофамильцем. Брать results[0] без проверки — значит молча
    загрузить публикации чужого человека. Поэтому ищем нормализованное точное
    совпадение по всем кандидатам; если совпадения нет — автор пропускается, а
    в лог пишется ERROR со списком кандидатов.

    Транзиентные ошибки сети/API (timeout, 5xx, 429) ретраятся с задержкой —
    раньше одна ошибка молча выкидывала автора из прогона. Среди точных
    совпадений с одинаковым именем (в OpenAlex бывают дубли-профили, например
    два «Alexander Krylatov» с 71 и 1 работой) берётся профиль с наибольшим
    числом работ — с меньшей вероятностью фейковый/неполный.
    """
    url = f"{BASE_URL}/authors"
    query_norm = _normalize_name(display_name)

    for attempt in range(1, retries + 1):
        try:
            params = {
                'search': display_name,
                'select': 'id,display_name,works_count',
                'per-page': 50,  # больше кандидатов — точное имя не теряется за пределами топ-25
            }
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(f"API {resp.status_code} для {display_name}, попытка {attempt}/{retries}")
                if attempt < retries:
                    time.sleep(1.5 * attempt)
                    continue
                break
            if resp.status_code != 200:
                logger.error(f"Ошибка поиска {display_name}: HTTP {resp.status_code}")
                break

            data = resp.json()
            if data['meta']['count'] > 0:
                results = data['results']
                matches = [
                    a for a in results
                    if _normalize_name(a.get('display_name', '')) == query_norm
                ]
                if matches:
                    best = max(matches, key=lambda a: a.get('works_count') or 0)
                    logger.info(
                        f"Найден автор: {best['display_name']} "
                        f"({best['id']}, works={best.get('works_count')})"
                    )
                    return best['id']
                logger.error(
                    f"Автор {display_name} не найден по точному совпадению. "
                    f"Кандидаты: {[a.get('display_name') for a in results[:5]]}"
                )
            break
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            logger.warning(f"Ошибка поиска {display_name} (попытка {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(1.5 * attempt)
            else:
                logger.error(f"Автор {display_name} не найден после {retries} попыток: {e}")
    return None


def get_author_works(author_id, retries=3):
    """Все работы автора с пагинацией. Возвращает (works, complete).

    complete=False означает, что часть страниц не удалось загрузить даже после
    повторов — вызывающий код должен посчитать прогон неудачным, иначе данные
    автора молча обрежутся (а метаданные покажут 0 ошибок).
    """
    works = []
    url = f"{BASE_URL}/works"
    params = {'filter': f'author.id:{author_id}', 'per-page': 200}
    complete = True
    page = 0

    while url:
        page += 1
        fetched = False
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=30)
                if resp.status_code == 429 or resp.status_code >= 500:
                    logger.warning(f"API {resp.status_code} (стр. {page}), попытка {attempt}/{retries}")
                    if attempt < retries:
                        time.sleep(1.5 * attempt)
                        continue
                    break
                if resp.status_code != 200:
                    logger.error(f"Ошибка API: {resp.status_code} (стр. {page})")
                    break
                data = resp.json()
                works.extend(data['results'])
                url = data.get('next')
                params = None
                fetched = True
                break
            except (requests.exceptions.RequestException, ValueError, KeyError) as e:
                logger.warning(f"Ошибка загрузки работ (стр. {page}, попытка {attempt}/{retries}): {e}")
                if attempt < retries:
                    time.sleep(1.5 * attempt)
        if not fetched:
            complete = False
            break

    logger.info(f"Загружено {len(works)} работ (полностью: {complete})")
    return works, complete


def extract_work_info(work):
    primary_loc = work.get('primary_location') or {}
    source = (primary_loc.get('source') or {}).get('display_name', '')

    return {
        'id': work['id'],
        'title': work.get('title', 'Untitled'),
        'publication_year': work.get('publication_year'),
        'cited_by_count': work.get('cited_by_count', 0),
        'journal': source,
        'topics': (work.get('primary_topic') or {}).get('display_name', ''),
        'updated_date': work.get('updated_date', ''),
        'authors': [
            a.get('author', {}).get('display_name', '')
            for a in work.get('authorships', []) if a.get('author')
        ],
        'author_ids': [
            a.get('author', {}).get('id', '')
            for a in work.get('authorships', []) if a.get('author')
        ]
    }


def insert_publication(conn, work_info, content_hash):
    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO publications 
        (id, title, publication_year, cited_by_count, topics, journal, 
         updated_date, content_hash, last_loaded)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        work_info['id'], work_info['title'], work_info['publication_year'],
        work_info['cited_by_count'], work_info['topics'], work_info['journal'],
        work_info['updated_date'], content_hash, datetime.now().isoformat()
    ))

    for author_name, author_id in zip(work_info['authors'], work_info['author_ids']):
        if author_id:
            cursor.execute(
                "INSERT OR IGNORE INTO authors (id, name, last_loaded) VALUES (?, ?, ?)",
                (author_id, author_name, datetime.now().isoformat())
            )
            cursor.execute(
                "INSERT OR IGNORE INTO authorship (author_id, publication_id) VALUES (?, ?)",
                (author_id, work_info['id'])
            )

    conn.commit()


def update_publication(conn, work_info, content_hash):
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE publications
        SET title=?, publication_year=?, cited_by_count=?, topics=?, journal=?,
            updated_date=?, content_hash=?, last_loaded=?
        WHERE id=?
    """, (
        work_info['title'], work_info['publication_year'],
        work_info['cited_by_count'], work_info['topics'], work_info['journal'],
        work_info['updated_date'], content_hash, datetime.now().isoformat(),
        work_info['id']
    ))

    # Состав соавторов мог измениться (content_hash включает authorships):
    # пересоздаём authorship из актуальных данных, иначе связи для этой работы
    # рассинхронизируются (например, останется неполный список после обрыва ETL).
    cursor.execute("DELETE FROM authorship WHERE publication_id = ?", (work_info['id'],))
    for author_name, author_id in zip(work_info['authors'], work_info['author_ids']):
        if author_id:
            cursor.execute(
                "INSERT OR IGNORE INTO authors (id, name, last_loaded) VALUES (?, ?, ?)",
                (author_id, author_name, datetime.now().isoformat())
            )
            cursor.execute(
                "INSERT OR IGNORE INTO authorship (author_id, publication_id) VALUES (?, ?)",
                (author_id, work_info['id'])
            )
    conn.commit()



@timeit
def run_etl():
    logger.info("=" * 50)
    logger.info("ЗАПУСК ETL")

    init_db()
    last_run = get_last_etl_time()
    logger.info(f"Предыдущий ETL: {last_run}")

    total_added = 0
    total_updated = 0
    total_failed = 0

    for author_name in MAIN_AUTHORS:
        logger.info(f"\n--- Обработка: {author_name} ---")
        author_id = get_author_id(author_name)
        if not author_id:
            # Автор не найден (сеть/API/неоднозначность) — считаем неудачей,
            # иначе ETL молча «успешно» пропускает авторов и records_failed=0
            # при неполных данных.
            total_failed += 1
            continue

        works, complete = get_author_works(author_id)
        if not complete:
            # Часть страниц работ не загрузилась — данные автора неполные.
            total_failed += 1

        for work in works:
            work_id = work['id']
            try:
                work_info = extract_work_info(work)
                current_hash = compute_hash(work)

                with db_session() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT content_hash FROM publications WHERE id = ?",
                        (work_id,)
                    )
                    row = cursor.fetchone()

                    if not row:
                        insert_publication(conn, work_info, current_hash)
                        total_added += 1
                    elif row[0] != current_hash:
                        update_publication(conn, work_info, current_hash)
                        total_updated += 1

            except Exception as e:
                logger.error(f"Ошибка {work_id}: {e}")
                total_failed += 1

    update_etl_metadata(total_added, total_updated, total_failed)
    clear_all_caches()

    logger.info(f"\n--- ИТОГИ ---")
    logger.info(f"Добавлено: {total_added}")
    logger.info(f"Обновлено: {total_updated}")
    logger.info(f"Ошибок: {total_failed}")
    logger.info("=" * 50)


def needs_etl():
    """True, если данных для дашборда нет: база отсутствует или пустая.

    Вызывается на старте приложения: если данных нет — запускаем ETL,
    чтобы «просто запустить код» и получить наполненный граф.
    """
    if not os.path.exists(DB_PATH):
        return True
    try:
        with db_session() as conn:
            pubs = pd.read_sql("SELECT COUNT(*) FROM publications", conn).iloc[0, 0]
            auth = pd.read_sql("SELECT COUNT(*) FROM authorship", conn).iloc[0, 0]
        return pubs == 0 or auth == 0
    except Exception:
        # БД есть, но повреждена/не инициализирована — пересоздадим через ETL
        return True


def ensure_data(force=False):
    """Запускает ETL, если данных нет (или force=True). Возвращает True, если прогон был."""
    if force or needs_etl():
        logger.info("Данных нет — запускаю ETL (нужен интернет для OpenAlex)...")
        try:
            run_etl()
        except sqlite3.DatabaseError as e:
            # pure_data.db повреждена (например, обрыв записи) — валидные таблицы
            # из неё не прочитать, приложение падало бы на всех страницах.
            # Удаляем битый файл и пересоздаём базу с нуля.
            logger.error(f"База данных повреждена ({e}) — удаляю файл и пересоздаю")
            try:
                os.remove(DB_PATH)
            except OSError as remove_err:
                logger.error(f"Не удалось удалить битую БД {DB_PATH}: {remove_err}")
                raise
            run_etl()
        return True
    logger.info("Данные уже есть, ETL не требуется")
    return False


def get_author_stats(author_id):
    """Возвращает статистику по автору."""
    with db_session() as conn:
        # Число публикаций
        pub_count = pd.read_sql(
            "SELECT COUNT(*) FROM authorship WHERE author_id = ?", conn, params=[author_id]
        ).iloc[0, 0]
        # Сумма цитирований
        citations = pd.read_sql("""
            SELECT SUM(p.cited_by_count) 
            FROM authorship a 
            JOIN publications p ON a.publication_id = p.id 
            WHERE a.author_id = ?
        """, conn, params=[author_id]).iloc[0, 0] or 0
        # Кол-во соавторов
        coauthors = pd.read_sql("""
            SELECT COUNT(DISTINCT a2.author_id) 
            FROM authorship a1
            JOIN authorship a2 ON a1.publication_id = a2.publication_id 
            WHERE a1.author_id = ? AND a2.author_id != ?
        """, conn, params=[author_id, author_id]).iloc[0, 0]
        return pub_count, citations, coauthors

def get_author_publications(author_id):
    """Список публикаций автора."""
    with db_session() as conn:
        df = pd.read_sql("""
            SELECT p.id, p.title, p.publication_year, p.cited_by_count, p.journal
            FROM authorship a 
            JOIN publications p ON a.publication_id = p.id 
            WHERE a.author_id = ?
            ORDER BY p.publication_year DESC, p.cited_by_count DESC
        """, conn, params=[author_id])
        return df.to_dict('records')

def get_author_coauthors(author_id):
    """Список соавторов с числом совместных работ."""
    with db_session() as conn:
        df = pd.read_sql("""
            SELECT a2_info.id, a2_info.name, COUNT(*) as joint_works
            FROM authorship a1
            JOIN authorship a2 ON a1.publication_id = a2.publication_id
            JOIN authors a2_info ON a2.author_id = a2_info.id
            WHERE a1.author_id = ? AND a2.author_id != ?
            GROUP BY a2.author_id
            ORDER BY joint_works DESC
        """, conn, params=[author_id, author_id])
        return df.to_dict('records')

def get_author_topics(author_id):
    """Топ-5 тем автора."""
    with db_session() as conn:
        df = pd.read_sql("""
            SELECT p.topics
            FROM authorship a 
            JOIN publications p ON a.publication_id = p.id 
            WHERE a.author_id = ? AND p.topics IS NOT NULL AND p.topics != ''
        """, conn, params=[author_id])
        topics = []
        for topics_str in df['topics']:
            # Темы хранятся по одной на публикацию; имя темы может содержать
            # запятую ('Language, Discourse, Communication Strategies') — режем
            # только по ';' (в именах тем не встречается), иначе одна тема
            # раскалывается на фейковые части.
            for t in topics_str.split(';'):
                cleaned = t.strip()
                if cleaned:
                    topics.append(cleaned)
        from collections import Counter
        return Counter(topics).most_common(5)

def get_author_name(author_id):
    """Возвращает имя автора по id (или сам id, если не найден)."""
    with db_session() as conn:
        df = pd.read_sql("SELECT name FROM authors WHERE id = ?", conn, params=[author_id])
        return df.iloc[0]['name'] if not df.empty else author_id

if __name__ == '__main__':
    import sys
    import schedule
    import time

    # --once: выполнить один прогон ETL и выйти (для стартового скрипта)
    once = '--once' in sys.argv

    run_etl()

    if once:
        logger.info("ETL выполнен (режим --once). Завершение.")
        sys.exit(0)

    schedule.every().day.at("08:00").do(run_etl)
    logger.info("Планировщик запущен. ETL будет выполняться ежедневно в 8:00")

    while True:
        schedule.run_pending()
        time.sleep(60)