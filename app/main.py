from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import init_db, get_conn
from app.schemas import FilmIn, FilmUpdate, FilmOut
from app import tmdb

app = FastAPI(title="Filmotheque")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.on_event("startup")
def _startup():
    init_db()


def _row_to_dict(row) -> dict:
    return dict(row)


@app.get("/api/films", response_model=list[FilmOut])
def list_films(
    statut: str | None = None,
    genre: str | None = None,
    acteur: str | None = None,
    search: str | None = None,
    sort: str = Query(default="recent", pattern="^(recent|note|titre)$"),
):
    query = "SELECT * FROM films WHERE 1=1"
    params: list = []

    if statut:
        query += " AND statut = ?"
        params.append(statut)
    if genre:
        query += " AND genres LIKE ?"
        params.append(f"%{genre}%")
    if acteur:
        query += " AND acteurs LIKE ?"
        params.append(f"%{acteur}%")
    if search:
        query += " AND (titre LIKE ? OR acteurs LIKE ? OR realisateur LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like])

    if sort == "note":
        query += " ORDER BY note IS NULL, note DESC"
    elif sort == "titre":
        query += " ORDER BY titre COLLATE NOCASE ASC"
    else:
        query += " ORDER BY date_ajout DESC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


@app.post("/api/films", response_model=FilmOut, status_code=201)
def create_film(film: FilmIn):
    now = datetime.now(timezone.utc).isoformat()
    date_vu = now if film.statut == "vu" else None
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO films
               (titre, tmdb_id, annee, genres, acteurs, realisateur, plateforme,
                poster_url, statut, note, commentaire, date_ajout, date_vu)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (film.titre, film.tmdb_id, film.annee, film.genres, film.acteurs,
             film.realisateur, film.plateforme, film.poster_url, film.statut,
             film.note, film.commentaire, now, date_vu),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM films WHERE id = ?", (new_id,)).fetchone()
    return _row_to_dict(row)


@app.put("/api/films/{film_id}", response_model=FilmOut)
def update_film(film_id: int, update: FilmUpdate):
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Film introuvable")

        fields = update.model_dump(exclude_unset=True)
        if not fields:
            return _row_to_dict(existing)

        # Passage automatique de la date de visionnage au moment ou statut -> vu
        if fields.get("statut") == "vu" and existing["statut"] != "vu":
            fields["date_vu"] = datetime.now(timezone.utc).isoformat()
        elif fields.get("statut") == "avoir":
            fields["date_vu"] = None

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [film_id]
        conn.execute(f"UPDATE films SET {set_clause} WHERE id = ?", params)
        row = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
    return _row_to_dict(row)


@app.delete("/api/films/{film_id}", status_code=204)
def delete_film(film_id: int):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM films WHERE id = ?", (film_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Film introuvable")
        conn.execute("DELETE FROM films WHERE id = ?", (film_id,))
    return None


@app.get("/api/films/random", response_model=FilmOut)
def random_film(genre: str | None = None):
    query = "SELECT * FROM films WHERE statut = 'avoir'"
    params: list = []
    if genre:
        query += " AND genres LIKE ?"
        params.append(f"%{genre}%")
    query += " ORDER BY RANDOM() LIMIT 1"

    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        raise HTTPException(404, "Aucun film a voir dans la liste (avec ce filtre)")
    return _row_to_dict(row)


@app.get("/api/meta/genres")
def list_genres():
    with get_conn() as conn:
        rows = conn.execute("SELECT genres FROM films WHERE genres != ''").fetchall()
    genres = set()
    for row in rows:
        for g in row["genres"].split(","):
            g = g.strip()
            if g:
                genres.add(g)
    return sorted(genres)


@app.get("/api/tmdb/search")
async def tmdb_search(q: str = Query(min_length=2)):
    try:
        return await tmdb.search_movies(q)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/movie/{tmdb_id}")
async def tmdb_movie(tmdb_id: int):
    try:
        return await tmdb.get_movie_details(tmdb_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/providers/{tmdb_id}")
async def tmdb_providers(tmdb_id: int):
    try:
        return await tmdb.get_watch_providers_fr(tmdb_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
