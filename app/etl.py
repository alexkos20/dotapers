import os
import sqlite3
import requests
from datetime import datetime
from utils import (
    MAIN_AUTHORS, BASE_URL, DB_PATH, logger, timeit,
    compute_hash, clear_all_caches
)
import pandas as pd

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_connection() as conn:
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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM etl_metadata WHERE key = 'last_etl_run'")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def update_etl_metadata(total_added=0, total_updated=0, total_failed=0):
    with get_connection() as conn:
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

def get_author_id(display_name):
    url = f"{BASE_URL}/authors"
    params = {'search': display_name, 'select': 'id,display_name'}
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data['meta']['count'] > 0:
                author = data['results'][0]
                logger.info(f"Найден автор: {author['display_name']}")
                return author['id']
        logger.warning(f"Автор {display_name} не найден")
    except Exception as e:
        logger.error(f"Ошибка поиска {display_name}: {e}")
    return None


def get_author_works(author_id):
    works = []
    url = f"{BASE_URL}/works"
    params = {'filter': f'author.id:{author_id}', 'per-page': 200}

    while url:
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                logger.error(f"Ошибка API: {resp.status_code}")
                break
            data = resp.json()
            works.extend(data['results'])
            url = data.get('next')
            params = None
        except Exception as e:
            logger.error(f"Ошибка загрузки работ: {e}")
            break

    logger.info(f"Загружено {len(works)} работ")
    return works


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
            continue

        works = get_author_works(author_id)

        for work in works:
            work_id = work['id']
            try:
                work_info = extract_work_info(work)
                current_hash = compute_hash(work)

                with get_connection() as conn:
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
        with get_connection() as conn:
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
        run_etl()
        return True
    logger.info("Данные уже есть, ETL не требуется")
    return False

# etl.py (дополнить)

def get_author_stats(author_id):
    """Возвращает статистику по автору."""
    with get_connection() as conn:
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
    with get_connection() as conn:
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
    with get_connection() as conn:
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
    with get_connection() as conn:
        df = pd.read_sql("""
            SELECT p.topics
            FROM authorship a 
            JOIN publications p ON a.publication_id = p.id 
            WHERE a.author_id = ? AND p.topics IS NOT NULL AND p.topics != ''
        """, conn, params=[author_id])
        topics = []
        for topics_str in df['topics']:
            for t in topics_str.replace(';', ',').split(','):
                cleaned = t.strip()
                if cleaned:
                    topics.append(cleaned)
        from collections import Counter
        return Counter(topics).most_common(5)

def get_author_name(author_id):
    """Возвращает имя автора по id (или сам id, если не найден)."""
    with get_connection() as conn:
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