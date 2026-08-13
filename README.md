🎙️ VoxTria — Assistant vocal local (ASR · LLM · TTS)
VoxTria est un assistant vocal 100% local, modulaire, qui enchaîne trois
briques en cascade :
```
🎤 Micro → ASR (Whisper) → texte → LLM (serveur OpenAI-compatible)
        → réponse → TTS (Supertonic) → 🔊 audio
```
Pensé pour fonctionner hors-ligne sur CPU (Windows / Linux / macOS), avec une
interface web pour tout piloter. Chaque brique est interchangeable.
> Nom du dépôt suggéré : `asr-llm-tts`
---
✨ Fonctionnalités
🎤 ASR via `faster-whisper` (transcription FR, optimisée CPU)
🧠 LLM via n'importe quel serveur OpenAI-compatible (LM Studio,
llama.cpp, ou API cloud) — avec découverte automatique des modèles
🔊 TTS via Supertonic (voix multilingues natives, rapide sur CPU)
⚡ Réponse progressive (streaming) : le LLM est lu au fil de l'eau et
chaque phrase est synthétisée dès qu'elle est complète — le son démarre en
~0,2 s au lieu d'attendre toute la réponse
🔁 Mode mains-libres : détection automatique de fin de parole (VAD),
conversation continue sans recliquer
💬 Historique de conversation + purge
⚙️ Panneau de paramètres complet :
LLM : URL, modèle (auto-découverte), clé API, température, max tokens, prompt
TTS : voix (M1–M5 / F1–F5), Qualité (steps), Vitesse, Silence
Test rapide de voix
🎭 Voix clonées (upload JSON Voice Builder ou clonage local)
ASR : choix du modèle Whisper + réglages VAD
🗣️ Expression tags (`<laugh>`, `<sigh>`, `<breath>`…) insérés par le LLM
🌐 Traduction live: traduit en quasi temps réel un flux audio (YouTube
live, onglet navigateur, podcast…) dans la langue de ton choix, via un
LLM de traduction (ex. `tencent/Hy-MT2-1.8B-GGUF` via llama.cpp)
🖥️ Interface web soignée (thème néon), servie en local
🚀 Scripts d'installation et de lancement automatiques (`.bat` / `.sh`)
---

## 🌐 Traduction live

VoxTria peut traduire en quasi temps réel un flux audio dans la langue
de ton choix. Trois sources sont supportées :

| Source | Mode | Latence ajoutée | Pré-requis |
|---|---|---|---|
| **YouTube (sous-titres)** | le + fiable et rapide — pas d'ASR | ~1 s + retard des subs (2-4 s) | la vidéo a des sous-titres |
| **Onglet navigateur** | capture l'audio de n'importe quel onglet (Twitch, Zoom, podcast) | ~1-2 s | Chrome/Edge (le navigateur affiche un prompt) |
| **URL générique** | télécharge via `yt-dlp` + transcrit via Whisper | ~3-5 s | `pip install yt-dlp` + `ffmpeg` |

### Pipeline

```
[source audio] → segment texte (~1 phrase)
        ↓
[LLM de traduction]   (ex. tencent/Hy-MT2-1.8B-GGUF via llama.cpp)
        ↓
[TTS Supertonic]      (voix/langue déjà câblés dans le pipeline existant)
        ↓
[audio lu dans les haut-parleurs]   (file audio côté navigateur, sans blanc)
```

Sur une RTX 2000 Ada (16 Go) on fait tourner en parallèle Whisper
`large-v3` (float16) + llama.cpp avec Hy-MT2 Q4_K_M + Supertonic, avec
~10 Go de VRAM libre.

### Configuration rapide (YouTube → Français)

1. **Télécharge Hy-MT2** (une seule fois, ~1-2 Go) :
   ```bash
   # Option A : depuis l'UI
   #   ⚙️ → 🌐 Traduction live → ⬇️ Télécharger le modèle Hy-MT2 → 🔍 Lister
   #   → choisir Q4_K_M (1.13 Go, bon compromis) → ⬇️ Télécharger
   #
   # Option B : en CLI
   python -c "from translate import list_hymt2_quants, download_hymt2; \\
              [print(q) for q in list_hymt2_quants()]; \\
              download_hymt2('Q4_K_M', './models', '1.8B')"
   ```
   Le modèle est alors dans `./models/Hy-MT2-1.8B-Q4_K_M.gguf`.

2. **Démarre llama.cpp avec Hy-MT2** (sur un port dédié) :
   ```bash
   llama-server -m ./models/Hy-MT2-1.8B-Q4_K_M.gguf --port 8081
   ```
   (Le repo HF contient aussi Q6_K et Q8_0 si tu veux plus de qualité
   au prix de la RAM.)

3. **Démarre VoxTria** (`.sh` / `.bat` ou `uvicorn server:app`).

4. Dans l'UI, ouvre ⚙️ → section **🌐 Traduction live** :
   - **Source** : `YouTube — sous-titres`
   - **URL** : colle l'URL YouTube
   - **Cible langue** : `Français`
   - **URL du LLM de traduction** : `http://localhost:8081/v1`
   - **Modèle** : `Hy-MT2-1.8B-Q4_K_M`
   - ▶ **Démarrer**

5. Le son traduit sort dans tes haut-parleurs, et les segments
   apparaissent en bas du chat avec leur source et leur timing.

### Paramètres d'inférence (avancé)

Les paramètres recommandés par Tencent pour Hy-MT2 (1.8B & 7B) sont
**appliqués par défaut** :

| Paramètre | Défaut | Effet |
|---|---:|---|
| `temperature` | 0.7 | contrôle le caractère aléatoire de la sélection des tokens |
| `top_p` | 0.6 | nucleus sampling (ne garde que les tokens cumulant p% de proba) |
| `top_k` | 20 | limite aux 20 tokens les plus probables |
| `repetition_penalty` | 1.05 | pénalise les répétitions (>1 = moins de répétitions) |
| `max_tokens` | 4096 | longueur max générée (automatiquement bornée par la longueur d'entrée) |

Tous sont modifiables depuis l'UI (⚙️ → 🌐 → ▸ Paramètres d'inférence).
Tu peux les ajuster si tu utilises un autre modèle de traduction (NLLB,
MADLAD, etc.) ou si tu veux plus de déterminisme.

> **Pourquoi pas `temperature=0` ?** Hy-MT2 a été entraîné avec sampling
> (`0.7/0.6/20/1.05`) — mettre la température à 0 *dégrade* sa qualité
> de traduction, contrairement à un LLM généraliste.

> Tu peux laisser `URL du LLM` à `auto` pour réutiliser le LLM du chat
> vocal, mais un llama.cpp dédié à Hy-MT2 est **fortement recommandé** :
> Hy-MT2 a été entraîné pour la traduction et écrasera en qualité tout
> LLM généraliste sur cette tâche.

### Endpoints utiles (intégration / debug)

| Méthode | Route | Rôle |
|---|---|---|
| `POST` | `/api/translate/text` | traduit un texte (`{text, src, tgt, tts}`) |
| `POST` | `/api/translate/session/start` | démarre une session (`{source, url, src, tgt}`) |
| `POST` | `/api/translate/session/{id}/segment` | pousse un segment, renvoie la traduction + audio |
| `GET`  | `/api/translate/session/{id}/segments?since=N` | récupère les segments (utile pour un worker Python) |
| `GET`  | `/api/translate/youtube/next?video_id=…&last_t_ms=…` | helper : prochain segment timedtext |
| `POST` | `/api/translate/session/{id}/stop` | ferme la session |
| `GET`  | `/api/translate/hymt2/list?size=1.8B\|7B` | liste les quantizations HF |
| `POST` | `/api/translate/hymt2/download` | télécharge une quantization (`{quant, size, out_dir}`) |

> Les sessions sont en mémoire (LRU borné à 16). Elles sont perdues au
> redémarrage du serveur — c'est volontaire, ce sont des flux éphémères.

---
🚀 Démarrage rapide
Windows
Double-clique sur `install.bat` (une seule fois)
Double-clique sur `run.bat` (démarre le serveur + ouvre la page web)
Linux / macOS
```bash
./install.sh    # une seule fois
./run.sh        # démarre + ouvre le navigateur
```
Installation manuelle
```bash
python -m venv .venv
# Windows : .venv\Scripts\activate   |   Linux/mac : source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 127.0.0.1 --port 8500
# puis ouvre http://127.0.0.1:8500
```
> 🎬 Pour le **micro**, installe **ffmpeg** (lecture de l'audio navigateur) :
> - Windows : `winget install Gyan.FFmpeg`
> - Ubuntu/Debian : `sudo apt install ffmpeg`
> - macOS : `brew install ffmpeg`
---
🧠 Démarrer un serveur LLM (obligatoire pour le chat)
VoxTria ne lance pas le LLM lui-même : tu choisis ta source.
LM Studio
Charge un modèle (ex. LFM2.5-1.2B-Instruct)
Onglet Developer → Start Server (port 1234 par défaut)
Dans VoxTria → ⚙️ → URL = `http://localhost:1234/v1`, puis 🔍 pour
découvrir et choisir le modèle.
llama.cpp
```bash
llama-server -hf LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M --port 8080
```
→ URL = `http://localhost:8080/v1`
API cloud (OpenAI, etc.)
→ URL = endpoint `/v1`, renseigne la clé API et le nom du modèle.
---
⚡ Streaming (réponse progressive)
Activé par défaut (⚙️ → Réponse progressive). Le serveur lit la réponse du
LLM en flux, la découpe en phrases et synthétise chacune dès qu'elle est
complète ; le client les enchaîne sans blanc.
```
sans streaming : [------ LLM ------][---- TTS ----] 🔊   (4 à 10 s de silence)
avec streaming : [LLM..][TTS] 🔊 [TTS] 🔊 [TTS] 🔊        (premier son ~0,2 s)
```
Le découpage évite les faux positifs classiques : « 3.5 », « M. Dupont »,
« etc. » ne déclenchent pas de coupure. Une phrase dont la synthèse échoue
n'interrompt pas le reste du flux.
L'endpoint `/api/chat_stream` émet du SSE :
Événement	Contenu
`delta`	fragment de texte brut (affichage au fil de l'eau)
`sentence`	phrase complète + `audio_url` (ou `tts_error`)
`done`	texte final, `elapsed`, `timings.first_audio_ms`
`error`	erreur survenue pendant le flux
---
🎛️ Utilisation
Démarre ton serveur LLM.
Ouvre VoxTria, clique Pipeline : OFF → ON.
Tape un message, ou clique 🎤 et parle (la fin est détectée auto).
Active 🔁 Mains-libres pour une conversation continue.
⚙️ Paramètres : change la voix, teste-la, ajuste qualité/vitesse, change de LLM.
---
🎭 Voix : presets, clonage en ligne, clonage local
Presets
10 voix intégrées : M1–M5 (masculines), F1–F5 (féminines).
Voix clonée via Voice Builder (en ligne)
Crée un JSON de voix sur le Voice Builder de Supertone (court extrait audio).
⚙️ → section « Voix clonée » → charge le `.json` (bouton ⬆️).
Elle apparaît comme « 🎭 custom ».
🧬 Clonage 100% local (`clone_voice.py`) — GPU NVIDIA requis
```bash
python clone_voice.py --wav mon_echantillon.wav --name ma_voix
```
Crée `./voices/ma_voix.json`, qui apparaît automatiquement dans la liste des voix.
> ⚠️ **Nécessite un GPU NVIDIA (4 Go+ VRAM, CUDA). Ne marche pas sur CPU seul.**
> Sans GPU : utilise **Google Colab** (GPU T4 gratuit), puis charge le `.json`
> via l'upload. 🔒 **Ne clone que des voix consenties.**
---
🗂️ Architecture
```
VoxTria/
├── server.py            # API FastAPI + sert l'interface
├── pipeline.py          # briques ASR / LLM / TTS + orchestrateur
├── config_schema.py     # schéma de configuration validé (Pydantic)
├── clone_voice.py       # clonage de voix local (optionnel, GPU)
├── config.example.json  # modèle de configuration (versionné)
├── config.json          # config locale — NON versionnée (peut contenir une clé API)
├── requirements.txt / requirements-dev.txt
├── pyproject.toml       # config pytest + ruff
├── tests/               # tests (104) — aucun modèle requis
├── static/
│   └── index.html       # interface web
├── voices/              # voix clonées (.json) — créé à l'usage
├── install.bat / run.bat        # Windows
├── install.sh  / run.sh         # Linux / macOS
├── LICENSE              # MIT (code du projet)
└── NOTICE.md            # licences des dépendances/modèles
```
Endpoints API
Méthode	Route	Rôle
GET	`/api/config`	lit la config
POST	`/api/config`	met à jour la config
GET	`/api/voices`	voix dispo (+ voix clonées)
GET	`/api/models`	découverte des modèles LLM (`/v1/models`)
POST	`/api/tts_test`	test d'une voix
POST	`/api/upload_voice`	upload d'une voix clonée (JSON)
POST	`/api/chat_text`	pipeline depuis texte
POST	`/api/chat_stream`	pipeline en streaming (SSE, phrase par phrase)
POST	`/api/chat_audio`	pipeline depuis audio (micro)
POST	`/api/clear`	efface l'historique
GET	`/api/history`	historique courant
GET	`/api/config/schema`	schéma JSON de la config (pour l'UI)
DELETE	`/api/voices/{nom}`	supprime une voix clonée
GET	`/api/health`	état du service
> `GET /api/voices` renvoie les presets sans charger le moteur TTS.
> Ajoute `?probe=true` pour interroger réellement Supertonic (déclenche le
> téléchargement du modèle au premier appel).
---
🔒 Sécurité
VoxTria est prévu pour tourner en local, sans authentification. Quelques
garde-fous sont en place, à connaître avant de l'exposer sur un réseau :
Protection CSRF : les requêtes non-GET portant une origine externe sont
refusées (403). Sans cela, n'importe quel site ouvert dans ton navigateur
pourrait piloter l'assistant. Autorise d'autres origines avec
`VOXTRIA_ALLOW_ORIGINS=https://mon-domaine`.
Uploads confinés : les voix clonées sont écrites uniquement dans
`./voices/`, sous un nom assaini, après validation du JSON.
Config validée : toutes les valeurs sont bornées (schéma Pydantic) ;
`custom_style_path` ne peut pointer que dans `./voices/`.
Clé API : préfère `VOXTRIA_API_KEY` (variable d'environnement) plutôt que
`config.json`. La clé n'est jamais renvoyée au navigateur.
Traces d'erreur : masquées par défaut, activables avec `VOXTRIA_DEBUG=1`.
> ⚠️ N'expose pas ce serveur sur Internet tel quel : place-le derrière un
> reverse proxy avec authentification.
---
🧪 Développement
```bash
pip install -r requirements-dev.txt
pytest -q          # 104 tests, aucun modèle requis
ruff check .
```
Les tests couvrent l'API (dont l'endpoint SSE), la validation de
configuration, la logique du pipeline et le découpage en phrases du streaming
: ils tournent en quelques secondes sans télécharger Whisper ni Supertonic.
L'interface web (logique VAD, file d'attente audio) n'est pas couverte de
tests automatisés — elle a été vérifiée ponctuellement sous Node.
---
⚙️ Configuration (`config.json`)
> `config.json` **n'est pas versionné** (il peut contenir une clé API). Il est
> créé automatiquement depuis `config.example.json` au premier lancement.
```jsonc
{
  "asr":  { "model_size": "small", "device": "cpu", "compute_type": "int8", "language": "fr" },
  "llm":  { "base_url": "http://localhost:8080/v1", "model": "...", "temperature": 0.7, ... },
  "tts":  { "voice": "M1", "lang": "fr", "total_steps": 8, "speed": 1.05, "silence_duration": 0.3 },
  "vad":  { "threshold": 0.02, "silence_ms": 1000 }
}
```
Tout est éditable depuis l'interface (⚙️).
---
🧩 Dépannage
Problème	Solution
`Connection refused` sur le LLM	démarre ton serveur LLM, vérifie l'URL et le `/v1`
Erreur « pas de choices »	mauvaise URL/modèle → utilise 🔍 pour découvrir
Le micro ne transcrit pas	installe ffmpeg et ajoute-le au PATH
VAD coupe trop tôt/tard	ajuste Sensibilité et Durée de silence dans ⚙️
`Torch not compiled with CUDA` (clonage)	normal sur CPU : le clonage local exige un GPU
`503` sur la page d'accueil	`static/index.html` est absent ; l'API reste utilisable sur `/docs`
Le micro est grisé	le navigateur exige HTTPS ou `localhost` pour `getUserMedia`
`403 Origine non autorisée`	ajoute ton origine dans `VOXTRIA_ALLOW_ORIGINS`
Changement de modèle Whisper sans effet	corrigé : le modèle est rechargé automatiquement
---
📜 Licence
Code de VoxTria : MIT (voir `LICENSE`).
Dépendances et modèles : licences propres, voir `NOTICE.md`.
⚠️ La plus restrictive est OpenRAIL-M (poids Supertonic) : usage
commercial OK mais restrictions d'usage + attribution. Le modèle LLM que
vous choisissez a aussi sa propre licence.
VoxTria ne redistribue aucun poids de modèle (ils sont téléchargés par
l'utilisateur), ce qui simplifie la conformité.
🙏 Crédits
Construit avec faster-whisper (MIT),
Supertonic (code MIT, poids
OpenRAIL-M), FastAPI, et éventuellement
supertonic.embed pour le
clonage local. L'interface utilise la pile de polices système (100% hors-ligne).
