import os
import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w342"


def _api_key() -> str:
    key = os.environ.get("TMDB_API_KEY", "")
    if not key:
        raise RuntimeError(
            "TMDB_API_KEY manquante. Ajoute-la dans le fichier .env "
            "(cle gratuite sur https://www.themoviedb.org/settings/api)"
        )
    return key


async def search_movies(query: str, limit: int = 6) -> list[dict]:
    """Recherche de films par titre, pour l'auto-completion."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/search/movie",
            params={
                "api_key": _api_key(),
                "query": query,
                "language": "fr-FR",
                "include_adult": "false",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in data.get("results", [])[:limit]:
        results.append({
            "tmdb_id": r["id"],
            "titre": r.get("title") or r.get("original_title"),
            "annee": (r.get("release_date") or "")[:4] or None,
            "poster_url": f"{TMDB_IMG_BASE}{r['poster_path']}" if r.get("poster_path") else None,
        })
    return results


async def get_movie_details(tmdb_id: int) -> dict:
    """Details complets d'un film : genres, acteurs, realisateur, affiche."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        detail_resp = await client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}",
            params={"api_key": _api_key(), "language": "fr-FR"},
        )
        credits_resp = await client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/credits",
            params={"api_key": _api_key()},
        )
        detail_resp.raise_for_status()
        credits_resp.raise_for_status()
        detail = detail_resp.json()
        credits = credits_resp.json()

    genres = ", ".join(g["name"] for g in detail.get("genres", []))
    acteurs = ", ".join(c["name"] for c in credits.get("cast", [])[:5])
    realisateur = next(
        (c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"),
        "",
    )

    try:
        plateformes_fr = await get_watch_providers_fr(tmdb_id)
    except Exception:
        plateformes_fr = []

    vote_average = detail.get("vote_average")
    pays = ", ".join(c["name"] for c in detail.get("production_countries", [])[:2])

    return {
        "tmdb_id": tmdb_id,
        "titre": detail.get("title"),
        "annee": (detail.get("release_date") or "")[:4] or None,
        "genres": genres,
        "acteurs": acteurs,
        "realisateur": realisateur,
        "poster_url": f"{TMDB_IMG_BASE}{detail['poster_path']}" if detail.get("poster_path") else None,
        "plateformes_fr": plateformes_fr,
        "duree_minutes": detail.get("runtime") or None,
        "synopsis": detail.get("overview") or "",
        "note_tmdb": round(vote_average, 1) if vote_average else None,
        "pays": pays,
    }


async def get_trailer_url(tmdb_id: int) -> str | None:
    """URL YouTube de la bande-annonce officielle, si disponible."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/videos",
            params={"api_key": _api_key(), "language": "fr-FR"},
        )
        resp.raise_for_status()
        videos = resp.json().get("results", [])

        # Si rien en francais, on retente en anglais (souvent plus complet)
        if not videos:
            resp_en = await client.get(
                f"{TMDB_BASE}/movie/{tmdb_id}/videos",
                params={"api_key": _api_key(), "language": "en-US"},
            )
            resp_en.raise_for_status()
            videos = resp_en.json().get("results", [])

    def score(v):
        return (
            v.get("site") == "YouTube",
            v.get("type") == "Trailer",
            v.get("official", False),
        )

    youtube_videos = [v for v in videos if v.get("site") == "YouTube"]
    if not youtube_videos:
        return None
    best = max(youtube_videos, key=score)
    return f"https://www.youtube.com/watch?v={best['key']}"


async def get_watch_providers_fr(tmdb_id: int) -> list[str]:
    """Plateformes de streaming/location/achat disponibles en France pour ce film."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/watch/providers",
            params={"api_key": _api_key()},
        )
        resp.raise_for_status()
        data = resp.json()

    fr = data.get("results", {}).get("FR", {})
    names = []
    # Priorite aux offres par abonnement (flatrate), puis gratuit, puis location/achat
    for category in ("flatrate", "free", "ads", "rent", "buy"):
        for provider in fr.get(category, []):
            name = provider.get("provider_name")
            if name and name not in names:
                names.append(name)
        if names and category in ("flatrate", "free"):
            break
    return names


def _movie_summary(r: dict) -> dict:
    """Format compact utilise par discover/similar (pas de credits/runtime, cf. limites API)."""
    vote_average = r.get("vote_average")
    return {
        "tmdb_id": r["id"],
        "titre": r.get("title") or r.get("original_title"),
        "annee": (r.get("release_date") or "")[:4] or None,
        "poster_url": f"{TMDB_IMG_BASE}{r['poster_path']}" if r.get("poster_path") else None,
        "synopsis": r.get("overview") or "",
        "note_tmdb": round(vote_average, 1) if vote_average else None,
        "genre_ids": r.get("genre_ids", []),
    }


async def get_genre_list() -> list[dict]:
    """Liste des genres TMDb (id + nom), pour peupler le filtre de decouverte."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/genre/movie/list",
            params={"api_key": _api_key(), "language": "fr-FR"},
        )
        resp.raise_for_status()
        return resp.json().get("genres", [])


async def get_watch_provider_list() -> list[dict]:
    """Liste des plateformes de streaming disponibles en France (id + nom + logo)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/watch/providers/movie",
            params={"api_key": _api_key(), "watch_region": "FR", "language": "fr-FR"},
        )
        resp.raise_for_status()
        data = resp.json().get("results", [])

    # Priorite aux plateformes les plus utilisees en France (display_priorities.FR)
    data.sort(key=lambda p: p.get("display_priorities", {}).get("FR", 999))
    return [
        {
            "provider_id": p["provider_id"],
            "provider_name": p["provider_name"],
            "logo_url": f"https://image.tmdb.org/t/p/w45{p['logo_path']}" if p.get("logo_path") else None,
        }
        for p in data[:40]
    ]


async def discover_movies(
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
) -> dict:
    """Recherche dans tout le catalogue TMDb selon des criteres, independamment de la watchlist.

    genre_id et provider_id acceptent plusieurs identifiants separes par '|' (syntaxe OR de TMDb),
    par exemple '35|18' pour Comedie OU Drame.
    """
    params = {
        "api_key": _api_key(),
        "language": "fr-FR",
        "sort_by": sort_by,
        "page": page,
        "include_adult": "false",
        "vote_count.gte": 50,  # evite les faux positifs "10/10" avec 2 votes
    }
    if genre_id:
        params["with_genres"] = genre_id
    if provider_id:
        params["with_watch_providers"] = provider_id
        params["watch_region"] = "FR"
        params["with_watch_monetization_types"] = "flatrate|free|ads"
    if duree_min:
        params["with_runtime.gte"] = duree_min
    if duree_max:
        params["with_runtime.lte"] = duree_max
    if note_min:
        params["vote_average.gte"] = note_min
    if origin_country:
        params["with_origin_country"] = origin_country
    if annee_min:
        params["primary_release_date.gte"] = f"{annee_min}-01-01"
    if annee_max:
        params["primary_release_date.lte"] = f"{annee_max}-12-31"

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(f"{TMDB_BASE}/discover/movie", params=params)
        resp.raise_for_status()
        data = resp.json()

    return {
        "page": data.get("page", 1),
        "total_pages": data.get("total_pages", 1),
        "results": [_movie_summary(r) for r in data.get("results", [])],
    }


async def get_similar_movies(tmdb_id: int, page: int = 1) -> dict:
    """Films similaires a un film donne (recommandations TMDb)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/movie/{tmdb_id}/recommendations",
            params={"api_key": _api_key(), "language": "fr-FR", "page": page},
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "page": data.get("page", 1),
        "total_pages": data.get("total_pages", 1),
        "results": [_movie_summary(r) for r in data.get("results", [])],
    }


async def search_person(query: str, limit: int = 6) -> list[dict]:
    """Recherche de realisateurs/acteurs par nom, pour l'auto-completion."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/search/person",
            params={"api_key": _api_key(), "query": query, "language": "fr-FR", "include_adult": "false"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for p in data.get("results", [])[:limit]:
        results.append({
            "person_id": p["id"],
            "nom": p.get("name"),
            "photo_url": f"{TMDB_IMG_BASE}{p['profile_path']}" if p.get("profile_path") else None,
            "connu_pour": p.get("known_for_department"),
        })
    return results


async def get_director_filmography(person_id: int) -> dict:
    """Tous les films realises par cette personne (credits en tant que realisateur)."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/person/{person_id}/movie_credits",
            params={"api_key": _api_key(), "language": "fr-FR"},
        )
        resp.raise_for_status()
        data = resp.json()

    directed = [c for c in data.get("crew", []) if c.get("job") == "Director"]
    # Plus recent en premier
    directed.sort(key=lambda c: c.get("release_date") or "", reverse=True)

    return {
        "results": [_movie_summary(c) for c in directed],
    }


async def get_actor_filmography(person_id: int, limit: int = 40) -> dict:
    """Films ou cette personne apparait au casting, tries par popularite."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{TMDB_BASE}/person/{person_id}/movie_credits",
            params={"api_key": _api_key(), "language": "fr-FR"},
        )
        resp.raise_for_status()
        data = resp.json()

    cast = data.get("cast", [])
    cast.sort(key=lambda c: c.get("popularity") or 0, reverse=True)

    return {
        "results": [_movie_summary(c) for c in cast[:limit]],
    }
