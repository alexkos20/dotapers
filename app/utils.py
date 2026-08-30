import hashlib
import json
import logging
import time
from logging.handlers import RotatingFileHandler
from functools import wraps

MAIN_AUTHORS = [
    'Oleg I. Drivotin', 'Ivan S. Blekanov', 'Aleksandra B. Vakaeva',
    'Sergey A. Kostyrko', 'Mikhail A. Grekov', 'E. A. Lejnina',
    'Alexander Krylatov', 'Natalia Kizhaeva',
]
BASE_URL = "https://api.openalex.org"
DB_PATH = "pure_data.db"
LOG_PATH = "app.log"
CACHE_SIZE = 128

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=5)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logger.addHandler(console)

    return logger

logger = setup_logging()

def timeit(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"{func.__name__} — {elapsed_ms:.0f} мс")
        return result
    return wrapper

def compute_hash(work):
    fields = {
        'title': work.get('title'),
        'cited_by_count': work.get('cited_by_count'),
        'publication_year': work.get('publication_year'),
        'authorships': work.get('authorships'),
        'primary_topic': work.get('primary_topic'),
    }
    json_str = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.md5(json_str.encode()).hexdigest()

def clear_all_caches():
    try:
        from pages.home import clear_graph_cache
        clear_graph_cache()
        logger.info("Все кэши очищены")
    except Exception:
        # вне контекста приложения (например, отдельный процесс ETL)
        logger.warning("Не удалось очистить кэш (возможно, app ещё не загружен)")