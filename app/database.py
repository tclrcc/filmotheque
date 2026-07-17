import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "filmotheque.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS films (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titre TEXT NOT NULL,
    tmdb_id INTEGER,
    annee INTEGER,
    genres TEXT DEFAULT '',
    acteurs TEXT DEFAULT '',
    realisateur TEXT DEFAULT '',
    plateforme TEXT DEFAULT '',
    poster_url TEXT,
    statut TEXT NOT NULL DEFAULT 'avoir',
    note REAL,
    commentaire TEXT DEFAULT '',
    date_ajout TEXT NOT NULL,
    date_vu TEXT
);
CREATE INDEX IF NOT EXISTS idx_films_statut ON films(statut);
"""

# Colonnes ajoutees apres la version initiale : (nom, definition SQL)
MIGRATIONS = [
    ("duree_minutes", "INTEGER"),
    ("synopsis", "TEXT DEFAULT ''"),
    ("note_tmdb", "REAL"),
    ("pays", "TEXT DEFAULT ''"),
]


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(films)")}
        for col_name, col_def in MIGRATIONS:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE films ADD COLUMN {col_name} {col_def}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
