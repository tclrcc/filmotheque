from pydantic import BaseModel, Field
from typing import Optional


class FilmIn(BaseModel):
    titre: str
    tmdb_id: Optional[int] = None
    annee: Optional[int] = None
    genres: str = ""
    acteurs: str = ""
    realisateur: str = ""
    plateforme: str = ""
    poster_url: Optional[str] = None
    statut: str = Field(default="avoir", pattern="^(avoir|vu)$")
    note: Optional[float] = None
    commentaire: str = ""


class FilmUpdate(BaseModel):
    titre: Optional[str] = None
    annee: Optional[int] = None
    genres: Optional[str] = None
    acteurs: Optional[str] = None
    realisateur: Optional[str] = None
    plateforme: Optional[str] = None
    poster_url: Optional[str] = None
    statut: Optional[str] = Field(default=None, pattern="^(avoir|vu)$")
    note: Optional[float] = None
    commentaire: Optional[str] = None


class FilmOut(FilmIn):
    id: int
    date_ajout: str
    date_vu: Optional[str] = None
