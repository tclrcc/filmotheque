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
    }


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
