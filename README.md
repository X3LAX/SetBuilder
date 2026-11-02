# SetBuilder

Application Mac pour préparer un set DJ pour Rekordbox à partir d'un dossier de morceaux, même mal rangé.

- **Import en vrac** : dossiers dans des dossiers, noms sales (« 01. bad bunny - … [FREE DOWNLOAD] »), fichiers sans tags. L'app trouve tous les fichiers audio, propose des noms propres « Artiste - Titre (Remix) », repère les doublons et les extraits, et copie tout dans `Musique/<projet>/Tracks` (les originaux ne sont jamais modifiés).
- **Pochettes** manquantes ajoutées automatiquement (SoundCloud, Deezer, iTunes) ou à la main.
- **Analyse** BPM, tonalité (Camelot), énergie, volume et qualité, avec [Essentia](https://essentia.upf.edu/).
- **Générateur de set** : durée visée, BPM de départ/arrivée, montée progressive, enchaînements harmoniques, priorité aux remix de titres connus. Édition par glisser-déposer, remplacement par un morceau compatible, verrouillage du début, pré-écoute des transitions.
- **Set étendu** : le set, puis les titres connus restants, puis le reste en vagues qui remontent en tempo.
- **Export Rekordbox** : XML Mac et Windows sans hot cues ni beatgrid (Rekordbox refait l'analyse), playlists `.m3u8`, tracklist imprimable.
- **IA optionnelle (Claude)** : corrige les noms illisibles et note à quel point chaque titre est connu. Deux façons de se connecter :
  - **ton abonnement Claude Pro/Max**, via [Claude Code](https://claude.com/product/claude-code) (comme Codex avec un compte ChatGPT) ;
  - ou une **clé API** Anthropic (facturée à l'usage).

## Installation

macOS 12+ et Python 3.11+ (`brew install python`).

```bash
git clone https://github.com/X3LAX/SetBuilder.git
cd SetBuilder
./install.sh
```

L'app est installée dans `~/Applications/SetBuilder.app` (Spotlight : « SetBuilder »).
Pour utiliser ton abonnement Claude : `./install.sh --with-claude`, puis dans l'app **Réglages › Connecter mon abonnement**.

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
- `app/setbuilder/ai.py` : Claude (abonnement via Claude Code, ou API)
- `app/setbuilder/export.py` : XML Rekordbox, m3u8, tracklist
- `app/setbuilder/server.py` + `app/web/` : interface

Journal en cas de problème : `~/Library/Logs/SetBuilder.log`.
