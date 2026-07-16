# Filmotheque

Watchlist de films personnelle : films a voir / films vus avec commentaires,
filtres par genre/acteur/statut, auto-completion via l'API TMDb.

Stack : FastAPI + SQLite (meme pattern que Pitch / edgelab), frontend HTML/JS
statique servi directement par FastAPI (pas de build front necessaire).

## 1. Recuperer une cle TMDb (gratuite)

1. Cree un compte sur https://www.themoviedb.org
2. Va dans Parametres > API : https://www.themoviedb.org/settings/api
3. Demande une cle "Developer" (approbation quasi instantanee)
4. Copie la cle "API Key (v3 auth)"

## 2. Deploiement sur le VPS

Depuis MobaXterm (SFTP, panneau de gauche), depose le dossier `filmotheque/`
entier dans `/home/ubuntu/`. Puis dans le terminal :

```bash
cd /home/ubuntu/filmotheque

# Environnement virtuel dedie
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Config
cp .env.example .env
nano .env   # colle ta cle TMDB_API_KEY

# Test manuel (Ctrl+C pour arreter)
uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Ouvre `http://<ip-du-vps>:8010` dans un navigateur (ou via un tunnel SSH,
ou en configurant temporairement le port dans le firewall) pour verifier
que tout fonctionne avant de passer en service permanent.

## 3. Lancer en service permanent (systemd)

```bash
sudo cp /home/ubuntu/filmotheque/filmotheque.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now filmotheque
sudo systemctl status filmotheque
```

Logs en direct :
```bash
journalctl -u filmotheque -f
```

## 4. Exposer via nginx (optionnel, si tu veux y acceder par un nom de domaine)

Si tu as deja un reverse proxy nginx pour Pitch, ajoute un bloc similaire,
par exemple sur un sous-domaine `films.tondomaine.fr` :

```nginx
server {
    listen 80;
    server_name films.tondomaine.fr;

    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Puis `sudo certbot --nginx -d films.tondomaine.fr` si tu veux le HTTPS.
Adapte le port (8010) si tu l'as change dans `filmotheque.service`.

## Structure du projet

```
filmotheque/
  app/
    main.py        routes API (CRUD films + recherche TMDb)
    database.py     connexion SQLite + schema
    schemas.py       modeles Pydantic
    tmdb.py         client TMDb (recherche + details)
  static/
    index.html      frontend (aucune dependance JS externe)
  data/               cree automatiquement, contient filmotheque.db
  requirements.txt
  .env.example
  filmotheque.service
```

## Sauvegarde

La base est un simple fichier SQLite dans `data/filmotheque.db`. Pour la
sauvegarder :
```bash
cp /home/ubuntu/filmotheque/data/filmotheque.db ~/backup-filmotheque-$(date +%F).db
```

## Evolutions possibles

- Export CSV/Excel de la liste (utile pour croiser avec un tracker perso)
- Notation multi-criteres (scenario, realisation, casting)
- Systeme de "listes" (par ex. "a voir avec X", "classiques a rattraper")
- Import en masse depuis un export Letterboxd/IMDb existant
