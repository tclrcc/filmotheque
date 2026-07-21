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
    genre: list[str] | None = Query(default=None),
    acteur: str | None = None,
    realisateur: str | None = None,
    plateforme: list[str] | None = Query(default=None),
    pays: str | None = None,
    duree_min: int | None = None,
    duree_max: int | None = None,
    annee_min: int | None = None,
    annee_max: int | None = None,
    search: str | None = None,
    sort: str = Query(default="recent", pattern="^(recent|note|titre)$"),
):
    query = "SELECT * FROM films WHERE 1=1"
    params: list = []

    if statut:
        query += " AND statut = ?"
        params.append(statut)
    if genre:
        query += " AND (" + " OR ".join(["genres LIKE ?"] * len(genre)) + ")"
        params.extend(f"%{g}%" for g in genre)
    if acteur:
        query += " AND acteurs LIKE ?"
        params.append(f"%{acteur}%")
    if realisateur:
        query += " AND realisateur LIKE ?"
        params.append(f"%{realisateur}%")
    if plateforme:
        query += " AND (" + " OR ".join(["plateforme LIKE ?"] * len(plateforme)) + ")"
        params.extend(f"%{p}%" for p in plateforme)
    if pays:
        query += " AND pays LIKE ?"
        params.append(f"%{pays}%")
    if duree_min:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes >= ?"
        params.append(duree_min)
    if duree_max:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes <= ?"
        params.append(duree_max)
    if annee_min:
        query += " AND annee IS NOT NULL AND annee >= ?"
        params.append(annee_min)
    if annee_max:
        query += " AND annee IS NOT NULL AND annee <= ?"
        params.append(annee_max)
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
        if film.tmdb_id:
            existing = conn.execute(
                "SELECT id, titre, statut FROM films WHERE tmdb_id = ?", (film.tmdb_id,)
            ).fetchone()
            if existing:
                raise HTTPException(
                    409,
                    f"\"{existing['titre']}\" est deja dans ta liste (statut actuel : {existing['statut']}).",
                )
        cur = conn.execute(
            """INSERT INTO films
               (titre, tmdb_id, annee, genres, acteurs, realisateur, plateforme,
                poster_url, statut, note, commentaire, date_ajout, date_vu,
                duree_minutes, synopsis, note_tmdb, pays)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (film.titre, film.tmdb_id, film.annee, film.genres, film.acteurs,
             film.realisateur, film.plateforme, film.poster_url, film.statut,
             film.note, film.commentaire, now, date_vu,
             film.duree_minutes, film.synopsis, film.note_tmdb, film.pays),
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
def random_film(
    genre: list[str] | None = Query(default=None),
    plateforme: list[str] | None = Query(default=None),
    duree_min: int | None = None,
    duree_max: int | None = None,
    pays: str | None = None,
    realisateur: str | None = None,
    annee_min: int | None = None,
    annee_max: int | None = None,
):
    query = "SELECT * FROM films WHERE statut = 'avoir'"
    params: list = []
    if genre:
        query += " AND (" + " OR ".join(["genres LIKE ?"] * len(genre)) + ")"
        params.extend(f"%{g}%" for g in genre)
    if plateforme:
        query += " AND (" + " OR ".join(["plateforme LIKE ?"] * len(plateforme)) + ")"
        params.extend(f"%{p}%" for p in plateforme)
    if duree_min:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes >= ?"
        params.append(duree_min)
    if duree_max:
        query += " AND duree_minutes IS NOT NULL AND duree_minutes <= ?"
        params.append(duree_max)
    if pays:
        query += " AND pays LIKE ?"
        params.append(f"%{pays}%")
    if realisateur:
        query += " AND realisateur LIKE ?"
        params.append(f"%{realisateur}%")
    if annee_min:
        query += " AND annee IS NOT NULL AND annee >= ?"
        params.append(annee_min)
    if annee_max:
        query += " AND annee IS NOT NULL AND annee <= ?"
        params.append(annee_max)
    query += " ORDER BY RANDOM() LIMIT 1"

    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        raise HTTPException(404, "Aucun film a voir dans la liste (avec ce filtre)")
    return _row_to_dict(row)


def _compute_genre_weights(vus_notes: list[dict]) -> Counter:
    """Score d'affinite par genre : +/- (note - 5.5) pour chaque film vu et note.
    Un genre presente dans des films bien notes ressort positif, mal notes negatif."""
    weights = Counter()
    for f in vus_notes:
        delta = f["note"] - 5.5
        for g in [x.strip() for x in (f["genres"] or "").split(",") if x.strip()]:
            weights[g] += delta
    return weights


def _compute_realisateur_weights(vus_notes: list[dict]) -> Counter:
    weights = Counter()
    for f in vus_notes:
        delta = f["note"] - 5.5
        r = (f["realisateur"] or "").strip()
        if r:
            weights[r] += delta
    return weights


@app.get("/api/suggestions/backlog")
def suggestions_backlog(limit: int = 10):
    """Reordonne ta liste 'a voir' selon ce que tes notes disent de tes gouts."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM films").fetchall()
    films_all = [_row_to_dict(r) for r in rows]
    vus_notes = [f for f in films_all if f["statut"] == "vu" and f["note"] is not None]
    a_voir = [f for f in films_all if f["statut"] == "avoir"]

    if not vus_notes:
        return {"suggestions": [], "message": "Note au moins quelques films vus pour activer les suggestions personnalisees."}
    if not a_voir:
        return {"suggestions": [], "message": "Ta liste 'a voir' est vide."}

    genre_w = _compute_genre_weights(vus_notes)
    real_w = _compute_realisateur_weights(vus_notes)

    scored = []
    for f in a_voir:
        genres = [x.strip() for x in (f["genres"] or "").split(",") if x.strip()]
        score = sum(genre_w.get(g, 0) for g in genres)
        score += real_w.get((f["realisateur"] or "").strip(), 0) * 1.5
        if f.get("note_tmdb"):
            score += f["note_tmdb"] * 0.2
        matched = [g for g in genres if genre_w.get(g, 0) > 0]
        scored.append({**f, "score": round(score, 2), "genres_matches": matched})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"suggestions": scored[:limit], "message": None}


@app.get("/api/suggestions/discover")
async def suggestions_discover(limit: int = 12):
    """Propose des films hors watchlist dans ton genre le mieux note, via TMDb."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM films").fetchall()
    films_all = [_row_to_dict(r) for r in rows]
    vus_notes = [f for f in films_all if f["statut"] == "vu" and f["note"] is not None]

    if not vus_notes:
        return {"results": [], "genre_utilise": None, "message": "Note au moins quelques films vus pour activer les suggestions personnalisees."}

    genre_w = _compute_genre_weights(vus_notes)
    positive_genres = [g for g, w in genre_w.items() if w > 0]
    if not positive_genres:
        return {"results": [], "genre_utilise": None, "message": "Pas encore assez de notes positives pour deduire tes gouts."}
    top_genre_name = max(positive_genres, key=lambda g: genre_w[g])

    try:
        tmdb_genres = await tmdb.get_genre_list()
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    genre_id = next((g["id"] for g in tmdb_genres if g["name"] == top_genre_name), None)
    if genre_id is None:
        return {"results": [], "genre_utilise": top_genre_name, "message": f"Genre prefere detecte ({top_genre_name}) mais introuvable cote TMDb."}

    existing_ids = {f["tmdb_id"] for f in films_all if f["tmdb_id"]}
    try:
        data = await tmdb.discover_movies(genre_id=genre_id, note_min=7, sort_by="vote_average.desc", page=1)
    except RuntimeError as e:
        raise HTTPException(400, str(e))

    filtered = [r for r in data["results"] if r["tmdb_id"] not in existing_ids][:limit]
    return {"results": filtered, "genre_utilise": top_genre_name, "message": None}


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


@app.get("/api/meta/pays")
def list_pays():
    with get_conn() as conn:
        rows = conn.execute("SELECT pays FROM films WHERE pays != ''").fetchall()
    pays_set = set()
    for row in rows:
        for p in row["pays"].split(","):
            p = p.strip()
            if p:
                pays_set.add(p)
    return sorted(pays_set)


@app.get("/api/meta/tmdb-ids")
def list_tmdb_ids():
    """Tous les tmdb_id deja presents dans la watchlist (avoir ou vu), pour les exclure de Decouvrir."""
    with get_conn() as conn:
        rows = conn.execute("SELECT tmdb_id FROM films WHERE tmdb_id IS NOT NULL").fetchall()
    return [row["tmdb_id"] for row in rows]


@app.get("/api/films/duplicates")
def find_duplicates():
    """Groupes de films en double (meme tmdb_id, ou meme titre exact si tmdb_id absent)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM films ORDER BY date_ajout ASC").fetchall()
    films_all = [_row_to_dict(r) for r in rows]

    groups: dict = {}
    for f in films_all:
        key = ("tmdb", f["tmdb_id"]) if f["tmdb_id"] else ("titre", f["titre"].strip().lower())
        groups.setdefault(key, []).append(f)

    duplicate_groups = [g for g in groups.values() if len(g) > 1]
    return {"groups": duplicate_groups, "total_doublons": sum(len(g) - 1 for g in duplicate_groups)}


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


@app.get("/api/tmdb/genres")
async def tmdb_genres():
    try:
        return await tmdb.get_genre_list()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/watch-providers")
async def tmdb_watch_providers():
    try:
        return await tmdb.get_watch_provider_list()
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/trailer/{tmdb_id}")
async def tmdb_trailer(tmdb_id: int):
    try:
        url = await tmdb.get_trailer_url(tmdb_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")
    if not url:
        raise HTTPException(404, "Aucune bande-annonce trouvee sur TMDb")
    return {"youtube_url": url}


@app.get("/api/tmdb/discover")
async def tmdb_discover(
    genre_id: str | None = None,
    provider_id: str | None = None,
    duree_min: int | None = None,
    duree_max: int | None = None,
    note_min: float | None = None,
    origin_country: str | None = None,
    annee_min: int | None = None,
    annee_max: int | None = None,
    sort_by: str = "popularity.desc",
    page: int = 1,
):
    try:
        return await tmdb.discover_movies(
            genre_id=genre_id, provider_id=provider_id, duree_min=duree_min,
            duree_max=duree_max, note_min=note_min, origin_country=origin_country,
            annee_min=annee_min, annee_max=annee_max, sort_by=sort_by, page=page,
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/similar/{tmdb_id}")
async def tmdb_similar(tmdb_id: int, page: int = 1):
    try:
        return await tmdb.get_similar_movies(tmdb_id, page=page)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/search-person")
async def tmdb_search_person(q: str = Query(min_length=2)):
    try:
        return await tmdb.search_person(q)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/director/{person_id}")
async def tmdb_director(person_id: int):
    try:
        return await tmdb.get_director_filmography(person_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/api/tmdb/actor/{person_id}")
async def tmdb_actor(person_id: int):
    try:
        return await tmdb.get_actor_filmography(person_id)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Erreur TMDb: {e}")


@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
