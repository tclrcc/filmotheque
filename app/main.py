from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import csv
import io

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

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
    plateforme: str | None = None,
    duree_max: int | None = None,
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
    if plateforme:
        query += " AND plateforme LIKE ?"
        params.append(f"%{plateforme}%")
    if duree_max:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes <= ?"
        params.append(duree_max)
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
                poster_url, statut, note, commentaire, date_ajout, date_vu,
                duree_minutes, synopsis, note_tmdb)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (film.titre, film.tmdb_id, film.annee, film.genres, film.acteurs,
             film.realisateur, film.plateforme, film.poster_url, film.statut,
             film.note, film.commentaire, now, date_vu,
             film.duree_minutes, film.synopsis, film.note_tmdb),
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
def random_film(genre: str | None = None, plateforme: str | None = None, duree_max: int | None = None):
    query = "SELECT * FROM films WHERE statut = 'avoir'"
    params: list = []
    if genre:
        query += " AND genres LIKE ?"
        params.append(f"%{genre}%")
    if plateforme:
        query += " AND plateforme LIKE ?"
        params.append(f"%{plateforme}%")
    if duree_max:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes <= ?"
        params.append(duree_max)
    query += " ORDER BY RANDOM() LIMIT 1"

    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        raise HTTPException(404, "Aucun film a voir dans la liste (avec ce filtre)")
    return _row_to_dict(row)


@app.get("/api/stats")
def get_stats():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM films").fetchall()

    films_all = [_row_to_dict(r) for r in rows]
    vus = [f for f in films_all if f["statut"] == "vu"]
    a_voir = [f for f in films_all if f["statut"] == "avoir"]

    notes = [f["note"] for f in vus if f["note"] is not None]
    note_moyenne = round(sum(notes) / len(notes), 2) if notes else None

    genre_counter = Counter()
    for f in vus:
        for g in [x.strip() for x in (f["genres"] or "").split(",") if x.strip()]:
            genre_counter[g] += 1

    realisateur_counter = Counter()
    for f in vus:
        r = (f["realisateur"] or "").strip()
        if r:
            realisateur_counter[r] += 1

    acteur_counter = Counter()
    for f in vus:
        for a in [x.strip() for x in (f["acteurs"] or "").split(",") if x.strip()]:
            acteur_counter[a] += 1

    mois_counter = Counter()
    for f in vus:
        if f["date_vu"]:
            mois_counter[f["date_vu"][:7]] += 1
    vus_par_mois = sorted(mois_counter.items())[-12:]

    return {
        "total_films": len(films_all),
        "total_vus": len(vus),
        "total_a_voir": len(a_voir),
        "note_moyenne": note_moyenne,
        "top_genres": genre_counter.most_common(5),
        "top_realisateurs": realisateur_counter.most_common(5),
        "top_acteurs": acteur_counter.most_common(5),
        "vus_par_mois": vus_par_mois,
    }


@app.get("/api/films/export.csv")
def export_csv():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM films ORDER BY date_ajout DESC").fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "titre", "annee", "genres", "acteurs", "realisateur", "plateforme",
        "duree_minutes", "note_tmdb", "statut", "note", "commentaire",
        "synopsis", "date_ajout", "date_vu",
    ])
    for r in rows:
        f = _row_to_dict(r)
        writer.writerow([
            f["titre"], f["annee"], f["genres"], f["acteurs"], f["realisateur"],
            f["plateforme"], f.get("duree_minutes"), f.get("note_tmdb"),
            f["statut"], f["note"], f["commentaire"], f.get("synopsis", ""),
            f["date_ajout"], f["date_vu"],
        ])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=filmotheque.csv"},
    )


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


@app.get("/api/meta/plateformes")
def list_plateformes():
    with get_conn() as conn:
        rows = conn.execute("SELECT plateforme FROM films WHERE plateforme != ''").fetchall()
    plateformes = set()
    for row in rows:
        for p in row["plateforme"].split(","):
            p = p.strip()
            if p:
                plateformes.add(p)
    return sorted(plateformes)


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
