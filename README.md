# 🎙️ VoxTria — Assistant vocal local (ASR · LLM · TTS)

**VoxTria** est un assistant vocal **100% local**, modulaire, qui enchaîne trois
briques en cascade :

```
🎤 Micro → ASR (Whisper) → texte → LLM (serveur OpenAI-compatible)
        → réponse → TTS (Supertonic) → 🔊 audio
```

Pensé pour fonctionner **hors-ligne sur CPU** (Windows / Linux / macOS), avec une
interface web pour tout piloter. Chaque brique est **interchangeable**.

> Nom du dépôt suggéré : `asr-llm-tts`

---

## ✨ Fonctionnalités

- 🎤 **ASR** via `faster-whisper` (transcription FR, optimisée CPU)
- 🧠 **LLM** via n'importe quel serveur **OpenAI-compatible** (LM Studio,
  llama.cpp, ou API cloud) — avec **découverte automatique des modèles**
- 🔊 **TTS** via **Supertonic** (voix multilingues natives, rapide sur CPU)
- 🔁 **Mode mains-libres** : détection automatique de fin de parole (VAD),
  conversation continue sans recliquer
- 💬 **Historique** de conversation + purge
- ⚙️ **Panneau de paramètres** complet :
  - LLM : URL, modèle (auto-découverte), clé API, température, max tokens, prompt
  - TTS : voix (M1–M5 / F1–F5), **Qualité (steps)**, **Vitesse**, **Silence**
  - Test rapide de voix
  - 🎭 Voix clonées (upload JSON Voice Builder **ou** clonage local)
  - ASR : choix du modèle Whisper + réglages VAD
- 🗣️ **Expression tags** (`<laugh>`, `<sigh>`, `<breath>`…) insérés par le LLM
- 🖥️ Interface web soignée (thème néon), servie en local
- 🚀 Scripts d'installation et de lancement automatiques (`.bat` / `.sh`)

---

## 🚀 Démarrage rapide

### Windows
1. Double-clique sur **`install.bat`** (une seule fois)
2. Double-clique sur **`run.bat`** (démarre le serveur + ouvre la page web)

### Linux / macOS
```bash
./install.sh    # une seule fois
./run.sh        # démarre + ouvre le navigateur
```

### Installation manuelle
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

## 🧠 Démarrer un serveur LLM (obligatoire pour le chat)

VoxTria ne lance pas le LLM lui-même : tu choisis ta source.

### LM Studio
1. Charge un modèle (ex. **LFM2.5-1.2B-Instruct**)
2. Onglet *Developer* → **Start Server** (port 1234 par défaut)
3. Dans VoxTria → ⚙️ → URL = `http://localhost:1234/v1`, puis 🔍 pour
   découvrir et choisir le modèle.

### llama.cpp
```bash
llama-server -hf LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M --port 8080
```
→ URL = `http://localhost:8080/v1`

### API cloud (OpenAI, etc.)
→ URL = endpoint `/v1`, renseigne la **clé API** et le **nom du modèle**.

---

## 🎛️ Utilisation

1. Démarre ton serveur LLM.
2. Ouvre VoxTria, clique **Pipeline : OFF → ON**.
3. **Tape** un message, **ou** clique 🎤 et parle (la fin est détectée auto).
4. Active **🔁 Mains-libres** pour une conversation continue.
5. ⚙️ Paramètres : change la voix, teste-la, ajuste qualité/vitesse, change de LLM.

---

## 🎭 Voix : presets, clonage en ligne, clonage local

### Presets
10 voix intégrées : **M1–M5** (masculines), **F1–F5** (féminines).

### Voix clonée via Voice Builder (en ligne)
1. Crée un JSON de voix sur le Voice Builder de Supertone (court extrait audio).
2. ⚙️ → section « Voix clonée » → charge le `.json` (bouton ⬆️).
3. Elle apparaît comme « 🎭 custom ».

### 🧬 Clonage 100% local (`clone_voice.py`) — GPU NVIDIA requis
```bash
python clone_voice.py --wav mon_echantillon.wav --name ma_voix
```
Crée `./voices/ma_voix.json`, qui apparaît automatiquement dans la liste des voix.

> ⚠️ **Nécessite un GPU NVIDIA (4 Go+ VRAM, CUDA). Ne marche pas sur CPU seul.**
> Sans GPU : utilise **Google Colab** (GPU T4 gratuit), puis charge le `.json`
> via l'upload. 🔒 **Ne clone que des voix consenties.**

---

## 🗂️ Architecture

```
voice_assistant/
├── server.py            # API FastAPI + sert l'interface
├── pipeline.py          # briques ASR / LLM / TTS + orchestrateur
├── clone_voice.py       # clonage de voix local (optionnel, GPU)
├── config.json          # configuration (modifiable via l'UI)
├── requirements.txt
├── static/
│   └── index.html       # interface web
├── voices/              # voix clonées (.json) — créé à l'usage
├── install.bat / run.bat        # Windows
├── install.sh  / run.sh         # Linux / macOS
├── LICENSE              # MIT (code du projet)
└── NOTICE.md            # licences des dépendances/modèles
```

### Endpoints API
| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/config` | lit la config |
| POST | `/api/config` | met à jour la config |
| GET | `/api/voices` | voix dispo (+ voix clonées) |
| GET | `/api/models` | découverte des modèles LLM (`/v1/models`) |
| POST | `/api/tts_test` | test d'une voix |
| POST | `/api/upload_voice` | upload d'une voix clonée (JSON) |
| POST | `/api/chat_text` | pipeline depuis texte |
| POST | `/api/chat_audio` | pipeline depuis audio (micro) |
| POST | `/api/clear` | efface l'historique |

---

## ⚙️ Configuration (`config.json`)

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

## 🧩 Dépannage

| Problème | Solution |
|---|---|
| `Connection refused` sur le LLM | démarre ton serveur LLM, vérifie l'URL et le `/v1` |
| Erreur « pas de choices » | mauvaise URL/modèle → utilise 🔍 pour découvrir |
| Le micro ne transcrit pas | installe **ffmpeg** et ajoute-le au PATH |
| VAD coupe trop tôt/tard | ajuste *Sensibilité* et *Durée de silence* dans ⚙️ |
| `Torch not compiled with CUDA` (clonage) | normal sur CPU : le clonage local exige un GPU |

---

## 📜 Licence

- **Code de VoxTria** : **MIT** (voir `LICENSE`).
- **Dépendances et modèles** : licences propres, voir **`NOTICE.md`**.
  ⚠️ La plus restrictive est **OpenRAIL-M** (poids Supertonic) : usage
  commercial OK mais **restrictions d'usage** + attribution. Le modèle LLM que
  vous choisissez a aussi sa propre licence.

VoxTria **ne redistribue aucun poids de modèle** (ils sont téléchargés par
l'utilisateur), ce qui simplifie la conformité.

## 🙏 Crédits
Construit avec [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT),
[Supertonic](https://huggingface.co/Supertone/supertonic-3) (code MIT, poids
OpenRAIL-M), [FastAPI](https://fastapi.tiangolo.com/), et éventuellement
[supertonic.embed](https://github.com/kdrkdrkdr/supertonic.embed) pour le
clonage local. Police d'affichage via Google Fonts (chargée en ligne, fallback
système hors-ligne).
