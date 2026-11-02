# SetBuilder

Application Mac pour préparer un set DJ pour Rekordbox à partir d'un dossier de morceaux, même mal rangé.

- **Téléchargement SoundCloud** : un morceau, une playlist (set), un profil ou ses likes (via [yt-dlp](https://github.com/yt-dlp/yt-dlp)). Fichier original quand l'artiste l'autorise, pochette et infos ajoutées, conversion automatique des formats que Rekordbox ne lit pas.
- **Import en vrac** : dossiers dans des dossiers, noms sales (« 01. bad bunny - … [FREE DOWNLOAD] »), fichiers sans tags. L'app trouve tous les fichiers audio, propose des noms propres « Artiste - Titre (Remix) », repère les doublons et les extraits, et copie tout dans `Musique/<projet>/Tracks` (les originaux ne sont jamais modifiés).
- **Pochettes** manquantes ajoutées automatiquement (SoundCloud, Deezer, iTunes) ou à la main.
- **Analyse** BPM, tonalité (Camelot), énergie, volume et qualité, avec [Essentia](https://essentia.upf.edu/).
- **Générateur de set** : durée visée, BPM de départ/arrivée, montée progressive, enchaînements harmoniques, priorité aux remix de titres connus. Édition par glisser-déposer, remplacement par un morceau compatible, verrouillage du début, pré-écoute des transitions.
- **Set étendu** : le set, puis les titres connus restants, puis le reste en vagues qui remontent en tempo.
- **Export Rekordbox** : XML Mac et Windows sans hot cues ni beatgrid (Rekordbox refait l'analyse), playlists `.m3u8`, tracklist imprimable.
- **Playlist Spotify de morceaux similaires** : à partir du set, l'app trouve des morceaux du même style et tempo (IA et/ou artistes similaires Deezer) et crée la playlist sur ton compte Spotify.
- **IA optionnelle et économe** : seulement pour corriger les noms illisibles, noter si un titre est connu et proposer des morceaux similaires. L'analyse audio, les pochettes et le set n'utilisent pas l'IA. Seules les lignes douteuses sont envoyées, avec le modèle le plus léger, et chaque réponse est mémorisée. Trois connexions possibles :
  - ton **abonnement Claude Pro/Max**, via [Claude Code](https://claude.com/product/claude-code) ;
  - ton **abonnement ChatGPT**, via [Codex](https://github.com/openai/codex) ;
  - ou une **clé API** Anthropic (modèle Haiku, facturée à l'usage).

## Installation

macOS 12+ et Python 3.11+ (`brew install python`).

```bash
git clone https://github.com/X3LAX/SetBuilder.git
cd SetBuilder
./install.sh
```

L'app est installée dans `~/Applications/SetBuilder.app` (Spotlight : « SetBuilder »).
Pour l'IA par abonnement : `./install.sh --with-claude` (et/ou `brew install --cask codex`), puis dans l'app **Réglages › Connecter**.

Pour Spotify : crée une app gratuite sur [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) avec la Redirect URI `http://127.0.0.1:47615/api/spotify/callback`, puis colle son Client ID dans **Réglages › Spotify**.

Mise à jour : `git pull && ./install.sh`.

## Utilisation

1. **Importer** : choisis tes dossiers, vérifie les noms (▶ pour écouter, « ✦ Corriger avec l'IA »), puis importe.
2. **Bibliothèque** : « Analyser », puis ajuste ce qui doit l'être (nom, BPM, clé, note « connu », forcer / exclure).
3. **Set** : règle la durée et le tempo, « Générer le set », puis ajuste.
4. **Export Rekordbox** : exporte le XML et suis les étapes affichées pour l'importer.

Un dossier existant qui contient un sous-dossier `Tracks` peut être ouvert directement (« Ouvrir un dossier existant »).

## Développement

```bash
python3 tests/test_core.py                                   # tests rapides (nécessite numpy)
~/Applications/SetBuilder.app/Contents/Resources/venv/bin/python app/main.py --headless --port 8765
# puis http://127.0.0.1:8765 dans un navigateur
```

- `app/setbuilder/library.py` : scan, noms, tags, projets
- `app/setbuilder/covers.py` : recherche de pochettes
- `app/setbuilder/analysis.py` : analyse audio (cache dans `~/Library/Application Support/SetBuilder`)
- `app/setbuilder/planner.py` : construction du set (beam search)
- `app/setbuilder/ai.py` : IA (Claude Code, Codex ou API) avec cache
- `app/setbuilder/soundcloud.py` : téléchargements SoundCloud
- `app/setbuilder/spotify.py` : connexion Spotify, recherche, playlists, similaires Deezer
- `app/setbuilder/export.py` : XML Rekordbox, m3u8, tracklist
- `app/setbuilder/server.py` + `app/web/` : interface

Journal en cas de problème : `~/Library/Logs/SetBuilder.log`.
