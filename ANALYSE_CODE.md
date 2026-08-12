# 🔍 VoxTria — Revue de code complète

> Analyse réalisée sur le commit `a41cc09` (branche `arena/019ff72a-voxtria`).
> Périmètre : 13 fichiers, ~1 060 lignes dont **602 lignes de Python**.
> Méthode : lecture intégrale, compilation (`py_compile`), lint (`pyflakes`),
> exécution réelle du serveur FastAPI via `TestClient`, et inspection du SDK
> `supertonic 1.3.1` réellement publié sur PyPI pour vérifier les hypothèses d'API.

---

## 1. Vue d'ensemble

### 1.1 Ce que fait le projet

VoxTria est un **assistant vocal local en cascade** : micro → ASR (faster-whisper)
→ LLM (serveur OpenAI-compatible) → TTS (Supertonic) → audio. Le tout est piloté
par une API FastAPI qui sert aussi une interface web, avec une configuration
JSON modifiable à chaud.

### 1.2 Inventaire

| Fichier | LOC | Rôle | Qualité perçue |
|---|---:|---|---|
| `pipeline.py` | 238 | Briques ASR / LLM / TTS + orchestrateur | 🟢 bonne structure |
| `server.py` | 188 | API HTTP + service de l'UI | 🟠 correcte, failles de validation |
| `clone_voice.py` | 176 | Clonage de voix local (GPU) | 🟠 script « wrapper », risques supply-chain |
| `config.json` | 36 | Configuration runtime | 🟠 versionnée + contient un champ `api_key` |
| `install/run .sh/.bat` | 158 | Scripts d'amorçage | 🟠 bit exécutable manquant |
| `README.md` / `NOTICE.md` | 260 | Documentation | 🟢 excellente, la meilleure partie du dépôt |
| **`static/index.html`** | — | **ABSENT** | 🔴 bloquant |

### 1.3 Impression générale

**Points forts réels :**

- L'**architecture en trois briques isolées** (`ASR`, `LLM`, `TTS` + orchestrateur
  `Assistant`) est propre et tient la promesse d'interchangeabilité : on peut
  remplacer le LLM sans toucher au TTS.
- Le **chargement paresseux** (`_lazy()`) des modèles évite de payer plusieurs
  centaines de Mo de téléchargement au démarrage du serveur.
- La **défensive sur les APIs tierces** est bien pensée : normalisation de l'URL
  vers `/v1`, message d'erreur lisible quand la réponse LLM n'a pas de `choices`,
  fallback `TypeError` sur les kwargs TTS, fallback sur la liste de voix.
- Le **rollback de l'historique** en cas d'échec du LLM (`self.history.pop()`)
  est un détail de qualité qu'on voit rarement dans des projets de cette taille.
- La **documentation** (README + NOTICE) est nettement au-dessus de la moyenne :
  le tableau des licences, l'avertissement OpenRAIL-M et la section éthique sur
  le clonage vocal sont exemplaires.

**Faiblesses structurantes :**

- Le projet **ne démarre pas en l'état** : l'interface web n'est pas dans le dépôt.
- **Aucune validation d'entrée** sur les endpoints d'écriture → une faille
  d'écriture de fichier arbitraire, confirmée par un test.
- **Aucun test, aucune CI, aucun pin de version**, un seul commit
  « Add files via upload » : le dépôt est un *dump* de dossier, pas un historique.
- Plusieurs **états globaux partagés** (historique, noms de fichiers de sortie
  fixes) rendent le serveur incorrect dès qu'il y a deux requêtes simultanées.

---

## 2. 🔴 Bloquant : l'interface web n'existe pas

`server.py:35` lit `static/index.html`, le README décrit longuement cette UI
(thème néon, panneau de paramètres, mains-libres, VAD…), mais **le dossier
`static/` n'est ni dans l'arbre de travail ni dans l'historique Git** :

```bash
$ git log --all --diff-filter=A --name-only | grep -i static
(aucun résultat)
```

Test d'exécution réel :

```
GET /             -> 500 Internal Server Error   (FileNotFoundError: static/index.html)
GET /api/health   -> 200 {"ok":true}
GET /api/config   -> 200 {...}
GET /api/voices   -> 200 {"voices":[...], "warning":"No module named 'supertonic'"}
```

**Conséquence :** l'API fonctionne, mais le produit tel que documenté est
inutilisable. Tout le pilotage (VAD, mains-libres, presets de voix, découverte
de modèles) décrit dans le README vit côté navigateur — c'est-à-dire dans le
fichier manquant. C'est aussi ce qui explique que les sections `vad` et `agent`
de `config.json` ne soient référencées **nulle part** dans le code Python.

**À faire :** soit ajouter `static/index.html` (le fichier semble avoir été
oublié lors de l'upload — le `.gitignore` mentionne d'ailleurs
`static/index_simple.html.bak`, preuve qu'il existait), soit dégrader
proprement :

```python
@app.get("/", response_class=HTMLResponse)
def index():
    p = BASE / "static" / "index.html"
    if not p.exists():
        return HTMLResponse("<h1>UI absente</h1><p>API disponible sur /docs</p>", 503)
    return p.read_text(encoding="utf-8")
```

---

## 3. 🔴 Sécurité

### 3.1 Écriture de fichier arbitraire via `/api/upload_voice` (critique)

`server.py:119-141` :

```python
dest = VOICES_DIR / file.filename      # ← nom de fichier contrôlé par le client
dest.write_bytes(await file.read())    # ← écriture AVANT toute validation
_json.loads(dest.read_text(...))       # ← validation après coup, sans nettoyage
```

Trois défauts cumulés :

1. **Traversée de répertoire.** `file.filename` n'est pas assaini. `Path("voices") / "../../x.json"` sort du dossier.
2. **Écriture avant validation.** Le contenu est écrit sur disque *avant* le
   `json.loads` ; si la validation échoue, le fichier reste en place. Le contrôle
   « c'est bien du JSON » ne protège donc rien.
3. **Filtre d'extension trivialement contournable** : `.endswith(".json")` est
   satisfait par `../../.config/autostart/x.desktop.json`, et surtout la
   vérification porte sur un nom entièrement fourni par l'attaquant.

Vérification effectuée dans le bac à sable :

```
POST /api/upload_voice  filename="../../pwned.json"
-> 200 {"ok":true,"path":"/home/user/VoxTria/voices/../../pwned.json"}
$ ls /home/user/pwned.json
-rw-r--r-- 1 user user 7 ...   ← fichier écrit hors du dossier prévu
```

**Aggravant — CSRF exploitable depuis n'importe quel site web.** Le serveur n'a
ni authentification, ni vérification d'`Origin`, ni jeton CSRF. Or
`multipart/form-data` est un *content-type simple* au sens CORS : **aucune
requête de préflight n'est envoyée**. Une page web malveillante ouverte dans le
navigateur de l'utilisateur peut donc soumettre un formulaire vers
`http://127.0.0.1:8500/api/upload_voice` et écrire un fichier arbitraire, à un
chemin arbitraire, sur la machine. L'attaquant ne lit pas la réponse, mais
l'effet de bord suffit (écriture dans un dossier de démarrage Windows, dans
`~/.bashrc`, écrasement de `config.json`…).

**Correctif recommandé :**

```python
import re, json as _json
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

@app.post("/api/upload_voice")
async def upload_voice(file: UploadFile = File(...)):
    stem = Path(file.filename or "").stem
    if not SAFE_NAME.match(stem):
        return JSONResponse({"error": "Nom de voix invalide (A-Z, a-z, 0-9, _ et - uniquement)."}, 400)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:                     # borne de taille
        return JSONResponse({"error": "Fichier trop volumineux."}, 413)
    try:
        data = _json.loads(raw.decode("utf-8"))        # valider AVANT d'écrire
    except Exception:
        return JSONResponse({"error": "JSON invalide."}, 400)
    dest = (VOICES_DIR / f"{stem}.json").resolve()
    if dest.parent != VOICES_DIR.resolve():            # ceinture + bretelles
        return JSONResponse({"error": "Chemin refusé."}, 400)
    dest.write_text(_json.dumps(data), encoding="utf-8")
    ...
```

Et pour le CSRF, ajouter un contrôle d'origine global :

```python
@app.middleware("http")
async def block_cross_origin(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse({"error": "origine refusée"}, 403)
    return await call_next(request)
```

### 3.2 `POST /api/config` : écriture de configuration non validée

`server.py:43-47` accepte **n'importe quel dictionnaire** et l'écrit tel quel
dans `config.json` :

- Un corps `{}` casse toute l'application (les accès `cfg["llm"]["base_url"]`,
  `cfg["asr"]["model_size"]`… lèvent des `KeyError` → 500 sur tous les endpoints).
- `tts.custom_style_path` accepte un chemin absolu arbitraire → lecture de
  fichier arbitraire via le moteur TTS.
- `llm.base_url` accepte n'importe quelle URL → le serveur devient un relais
  SSRF vers le réseau interne (avec la clé API attachée en en-tête si elle est
  définie).
- Même vecteur CSRF que ci-dessus atténué ici, car `application/json`
  déclenche un préflight — mais un client local malveillant reste libre.

**Correctif :** modéliser la configuration avec Pydantic (FastAPI l'intègre
nativement) plutôt qu'un `dict` brut :

```python
class TTSCfg(BaseModel):
    engine: Literal["supertonic"] = "supertonic"
    voice: str = "M1"
    total_steps: int = Field(8, ge=1, le=64)
    speed: float = Field(1.05, gt=0.1, le=3.0)
    silence_duration: float = Field(0.3, ge=0.0, le=5.0)
    custom_style_path: str = ""

class Config(BaseModel):
    asr: ASRCfg; llm: LLMCfg; tts: TTSCfg; vad: VADCfg
```

Bénéfice secondaire : la doc OpenAPI (`/docs`) devient réellement utilisable,
alors qu'aujourd'hui tous les corps sont des `dict` opaques.

### 3.3 Divulgation de traces d'exécution

Quatre endpoints renvoient `traceback.format_exc()` au client
(`server.py:116, 141, 154, 169`). Cela expose des chemins absolus, la structure
du projet et parfois des fragments de configuration. Acceptable en local, à
conditionner à un mode debug :

```python
DEBUG = os.getenv("VOXTRIA_DEBUG") == "1"
detail = {"error": str(e)} | ({"trace": traceback.format_exc()} if DEBUG else {})
```

### 3.4 `/api/audio/{name}` — risque résiduel faible

Test effectué : `GET /api/audio/..%2F..%2Fconfig.json` → **404**. Starlette
n'accepte pas les séparateurs encodés dans un paramètre de chemin simple, donc
la traversée n'est pas exploitable telle quelle. Le code reste néanmoins
fragile (il repose sur un comportement du framework, pas sur une validation
explicite). Ajouter deux lignes :

```python
p = (OUT_DIR / name).resolve()
if p.parent != OUT_DIR.resolve() or not p.is_file():
    return JSONResponse({"error": "not found"}, 404)
```

### 3.5 Secret dans un fichier versionné

`config.json` est **suivi par Git** et contient un champ `api_key`. Le
`.gitignore` protège soigneusement `.env`, `*.key` et `secrets.json`… mais pas
le fichier qui contiendra réellement la clé dès qu'un utilisateur branchera une
API cloud (cas explicitement documenté dans le README). Le risque de fuite par
`git commit -a` est élevé.

**Correctif :** versionner `config.example.json`, ignorer `config.json`, et
permettre une surcharge par variable d'environnement :

```python
api_key = os.getenv("VOXTRIA_API_KEY") or c.get("api_key", "")
```

---

## 4. 🟠 Bugs fonctionnels confirmés

### 4.1 Changer le modèle Whisper ou le moteur TTS n'a aucun effet

`pipeline.py:196-198` :

```python
def reload_config(self):
    self.cfg = load_config()
    self.asr.cfg = self.llm.cfg = self.tts.cfg = self.cfg
```

Le dictionnaire est bien remplacé, mais `ASR._model` et `TTS._tts` sont déjà
instanciés et **ne sont jamais invalidés**. Passer `small` → `medium` dans l'UI
enregistre la config, affiche un succès… et continue d'utiliser `small` jusqu'au
redémarrage du serveur. Même problème pour tout paramètre consommé à la
construction (`device`, `compute_type`).

```python
def reload_config(self):
    old = self.cfg
    self.cfg = load_config()
    self.asr.cfg = self.llm.cfg = self.tts.cfg = self.cfg
    if self.cfg["asr"] != old.get("asr"):
        self.asr._model = None          # forcera un rechargement paresseux
```

À noter : les paramètres *par appel* du TTS (`total_steps`, `speed`,
`silence_duration`, `voice`) sont eux relus correctement à chaque synthèse.

### 4.2 `/api/models` peut renvoyer un 500 non intercepté

`server.py:80-98` : `import requests` et l'accès `cfg["llm"]["base_url"]` sont
**hors du bloc `try`**. Observé en conditions réelles (dépendance absente) :

```
GET /api/models -> 500 Internal Server Error
```

alors que la fonction est justement écrite pour renvoyer
`{"models": [], "warning": ...}` en cas de problème. Même chose si la config a
été corrompue via §3.2. Il suffit de déplacer ces deux lignes dans le `try`.

### 4.3 Le blocage de la boucle d'événements

`chat_audio` (`server.py:157`) et `upload_voice` sont déclarés `async def` mais
appellent du code **entièrement synchrone et lent** : `assistant.handle_audio()`
enchaîne Whisper (secondes), un appel HTTP bloquant au LLM (`timeout=120`) et la
synthèse TTS. Comme la coroutine ne rend jamais la main, **tout le serveur est
gelé** pendant toute la durée du tour de parole : plus aucune requête ne peut
être servie, y compris `/api/health` ou l'arrêt propre côté UI.

Les endpoints `def` classiques (`chat_text`, `tts_test`) ne souffrent pas de ça
— FastAPI les délègue à un threadpool. L'incohérence est donc purement
accidentelle.

```python
from starlette.concurrency import run_in_threadpool
res = await run_in_threadpool(assistant.handle_audio, in_path, str(out))
```

### 4.4 Fichiers temporaires jamais supprimés

`server.py:162` : `NamedTemporaryFile(suffix=".wav", delete=False, dir=OUT_DIR)`.
Chaque tour de parole laisse un WAV dans `_out/`, **définitivement**. Une
conversation d'une heure en mains-libres laisse des centaines de fichiers. Il
manque un `finally: os.unlink(in_path)`.

### 4.5 État global partagé : le serveur est incorrect en concurrence

Trois ressources sont globales et non protégées :

- `assistant.history` — une seule conversation pour tous les onglets/clients ;
- `_out/reply.wav` — **nom fixe** : deux requêtes simultanées écrasent
  mutuellement leur réponse audio, et le client peut télécharger l'audio de
  quelqu'un d'autre ;
- `_out/voice_test.wav` — idem pour les tests de voix.

Le nom fixe pose aussi un problème de **cache navigateur** : l'URL
`/api/audio/reply.wav` ne change jamais, donc le navigateur peut rejouer
l'audio précédent (l'UI manquante devait probablement ajouter un cache-buster).

**Correctif :** générer un identifiant par réponse
(`reply_{uuid4().hex}.wav`), le renvoyer dans `audio_url`, et purger les
fichiers de plus de N minutes. Pour l'historique, l'indexer par identifiant de
session (cookie ou en-tête) plutôt que de le garder au niveau du processus.

### 4.6 Historique de conversation non borné

`pipeline.py:215-221` empile indéfiniment. Aucune troncature, aucun comptage de
tokens. Après quelques dizaines de tours, la requête dépasse la fenêtre de
contexte du modèle local → erreur HTTP du serveur LLM ou troncature silencieuse
du prompt système, et la latence croît linéairement. Il faut une fenêtre
glissante :

```python
MAX_TURNS = 20
messages = [system] + self.history[-MAX_TURNS * 2:]
```

### 4.7 Incohérence d'état si le TTS échoue

Dans `_respond`, le rollback ne couvre que l'échec du LLM. Si `tts.synthesize()`
lève une exception, la réponse de l'assistant **reste dans l'historique** alors
que le client reçoit un 500 et n'affiche rien. Les tours suivants référencent
donc une réponse que l'utilisateur n'a jamais vue ni entendue. Envelopper aussi
la synthèse, ou renvoyer un succès partiel (`{"assistant": reply, "tts_error": ...}`)
— ce dernier choix est probablement le meilleur : le texte a de la valeur même
sans audio.

### 4.8 Écriture de configuration non atomique

`save_config` ouvre en `"w"` (troncature immédiate) : une interruption pendant
l'écriture laisse un `config.json` vide ou tronqué, et l'application ne démarre
plus. Deux `POST /api/config` concurrents peuvent aussi s'entrelacer.

```python
tmp = CONFIG_PATH.with_suffix(".json.tmp")
tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
os.replace(tmp, CONFIG_PATH)   # atomique sur POSIX et Windows
```

### 4.9 Le premier appel à `/api/voices` télécharge tout le modèle

`voices()` appelle `assistant.tts.list_voices()` → `_lazy()` →
`STTS(auto_download=True)`, soit plusieurs centaines de Mo au premier
chargement de la page de paramètres, en bloquant la requête. Or la fonction
finit dans 100 % des cas par renvoyer la liste codée en dur (voir §5.1). Il
suffit de ne pas instancier le moteur pour lister des noms de voix.

---

## 5. 🟡 Points de justesse vis-à-vis du SDK Supertonic

J'ai téléchargé et inspecté `supertonic 1.3.1` (la version que `pip install
supertonic` installe aujourd'hui) pour confronter le code aux API réelles.

### 5.1 `list_voices()` ne trouvera jamais rien

```python
for attr in ("list_voice_styles", "list_voices", "get_voice_names", "voices"):
```

La classe `supertonic.TTS` n'expose **aucun** de ces quatre noms. L'attribut
réel est **`voice_style_names`** (défini dans `TTS.__init__`). La boucle échoue
donc systématiquement et tombe sur la liste codée en dur — qui se trouve être
correcte, mais par accident. Ajouter `"voice_style_names"` en tête de la liste
rendrait la découverte fonctionnelle (utile pour les voix ajoutées au dossier
de modèles).

### 5.2 Les balises d'expression ne sont probablement pas supportées

Le `system_prompt` de `config.json` demande au LLM d'insérer `<laugh>`,
`<sigh>`, `<breath>`, etc., et le README en fait une fonctionnalité affichée.
Or, dans `supertonic 1.3.1` :

- le préprocesseur de texte ne connaît que les balises de **langue**
  (`<fr>…</fr>`), générées en interne par `_add_language_token` ;
- le filtre de symboles spéciaux est `re.compile(r"[♥☆♡©\\]")` — il ne supprime
  ni `<` ni `>` ;
- aucune occurrence de « laugh », « breath » ou « expression » dans tout le SDK.

Conséquence probable : les balises sont **transmises telles quelles au modèle**,
qui les prononcera littéralement ou produira des artefacts, et
`validate_text()` peut signaler des caractères non supportés. Aucun code de
VoxTria ne les retire avant synthèse.

**Deux options, l'une ou l'autre :**

```python
EXPR = re.compile(r"</?(laugh|chuckle|sigh|breath|gasp|cough|sniff|groan|yawn|clear_throat)>")
clean = EXPR.sub("", reply)     # à synthétiser
# on renvoie `reply` (avec balises) à l'UI pour l'affichage, `clean` au TTS
```

…ou bien retirer l'instruction du prompt système et la mention du README. Dans
tous les cas, la situation actuelle (le LLM est *explicitement invité* à
produire des balises que rien ne consomme) est un bug fonctionnel.

### 5.3 Le repli `except TypeError` masque de vraies erreurs

```python
try:
    result = tts.synthesize(text, voice_style=style, **kwargs)
except TypeError:
    result = tts.synthesize(text, voice_style=style, lang=lang)
```

L'intention (compatibilité avec un SDK plus ancien) est bonne, mais `TypeError`
attrape aussi toute erreur de type levée **à l'intérieur** de la synthèse
(par exemple un `voice_style` invalide) — et déclenche alors un second appel
coûteux qui échouera pareillement, en doublant le temps d'échec et en brouillant
le diagnostic. Détecter les kwargs supportés une fois pour toutes via
`inspect.signature` est plus sûr :

```python
supported = inspect.signature(tts.synthesize).parameters
kwargs = {k: v for k, v in kwargs.items() if k in supported}
```

### 5.4 Détail : `lang` par défaut

Le code force `lang="fr"` depuis la config. Supertonic-3 gère 31 langues et
accepte `"na"` comme repli agnostique ; exposer ce choix (ou l'aligner sur la
langue détectée par Whisper, qui la renvoie dans l'objet `info` actuellement
ignoré via `segments, _ = model.transcribe(...)`) serait un vrai plus pour un
usage multilingue.

---

## 6. 🟡 Qualité, packaging et industrialisation

### 6.1 Résultats du lint

```
clone_voice.py:26:1  'os' imported but unused
clone_voice.py:171:11  f-string is missing placeholders
pipeline.py:12:1     'io' imported but unused
pipeline.py:18:1     'numpy as np' imported but unused
server.py:21:1       'fastapi.staticfiles.StaticFiles' imported but unused
```

`numpy` et `soundfile` figurent dans `requirements.txt` mais **ne sont utilisés
nulle part** (l'écriture WAV passe par `tts.save_audio`). Deux dépendances
lourdes à retirer — ou alors `numpy` reste une dépendance transitive implicite
de `supertonic`, ce qui n'est pas une raison pour l'importer.

L'import `StaticFiles` non utilisé confirme au passage qu'un
`app.mount("/static", ...)` a existé puis a disparu avec le dossier.

### 6.2 Aucune version épinglée

```
fastapi
uvicorn[standard]
faster-whisper
supertonic
...
```

Aucune borne. Or le code contient déjà des contournements pour des variations
d'API entre versions de `supertonic` (§5.3) : c'est l'aveu que l'API bouge.
Une mise à jour majeure du SDK cassera silencieusement les installations
existantes. Minimum vital :

```
supertonic>=1.3,<2
faster-whisper>=1.0,<2
fastapi>=0.110,<1
```

Et idéalement, un `pyproject.toml` avec `requires-python = ">=3.10"` (le README
l'exige, rien ne le vérifie) remplacerait `requirements.txt`.

### 6.3 Scripts shell non exécutables

```bash
$ git ls-files -s install.sh run.sh
100644 install.sh      ← devrait être 100755
100644 run.sh          ← devrait être 100755
```

Le README indique `./install.sh` : sur un clone frais sous Linux/macOS, cela
échoue avec `Permission denied`. À corriger dans l'index Git :

```bash
git update-index --chmod=+x install.sh run.sh
```

### 6.4 Zéro test, zéro CI

Aucun `tests/`, aucun `.github/workflows/`. Pourtant plusieurs parties du code
sont **facilement testables sans modèle** : normalisation de l'URL LLM,
`_get_style` (résolution `custom:`/`custom`/preset), `load_config`/`save_config`,
et tous les endpoints via `TestClient` en remplaçant `Assistant` par un double.
Une CI minimale (ruff + pytest sur 3.10/3.11/3.12) coûterait une trentaine de
lignes et attraperait les régressions d'API.

### 6.5 Historique Git

Un unique commit `Add files via upload`, sans branche ni tag. Le dépôt perd
tout l'intérêt de Git (bisect, revue, attribution). Ce n'est pas un défaut de
code, mais c'est le premier signal qu'un contributeur externe regarde.

### 6.6 Configuration morte

`config.json` déclare :

```jsonc
"agent": { "enabled": false, "tools": ["web_search_stub", "clock"] },
"vad":   { "threshold": 0.02, "silence_ms": 1000 }
```

Aucune des deux clés n'est lue côté Python. `vad` est légitime (il pilote le
front-end manquant), mais `agent` ne correspond à **aucune** implémentation, ni
même à un stub : c'est un drapeau de fonctionnalité fantôme, à retirer ou à
documenter comme « prévu ».

---

## 7. `clone_voice.py` — remarques spécifiques

Le script est un *wrapper* honnête et bien commenté, mais il fait trois choses
risquées à l'exécution, en dehors de tout contrôle :

1. **`git clone` d'un dépôt tiers sans commit épinglé** (`REPO_URL`, branche par
   défaut) : le code exécuté peut changer à tout moment côté amont. Épingler un
   SHA (`git clone` puis `git checkout <sha>`) est indispensable pour un script
   qui va ensuite lancer `python optimize_style.py`.
2. **`pip install -r` du `requirements.txt` distant dans l'environnement
   courant** : peut écraser les versions de `torch`/`onnxruntime` du venv de
   VoxTria et casser l'assistant lui-même. Un venv dédié au clonage serait plus
   sain.
3. **`input()` interactif** au milieu d'un script par ailleurs scriptable :
   empêche toute utilisation en CI ou en Colab non interactif. Un
   `--force/--yes` réglerait le problème.

Détails mineurs :

- `find_result_json` fait `sorted(logs.glob(...))` : tri **lexicographique**.
  Si les checkpoints ne sont pas à largeur fixe (`_900` vs `_1000`), le
  « dernier » sélectionné sera le mauvais. Trier par `st_mtime` est plus robuste.
- `make_config` écrit `"total_step": 5` alors que le reste du projet utilise
  `total_steps: 8` — deux clés différentes, valeurs différentes, aucun
  commentaire : source de confusion.
- La valeur de retour de `make_config` (le chemin) n'est jamais utilisée.
- `import os` inutilisé, f-string sans placeholder ligne 171.
- Aucune validation du WAV d'entrée (durée, mono/stéréo, taux d'échantillonnage)
  alors que la docstring exige « 3-10 s, un seul locuteur ».

---

## 8. Recommandations d'architecture (au-delà des correctifs)

### 8.1 Latence : le point faible structurel du design actuel

Le pipeline est strictement séquentiel et **entièrement bufferisé** :

```
[ASR complet] → [LLM complet, stream=False] → [TTS complet] → lecture
```

L'utilisateur n'entend rien avant que la totalité de la réponse ait été générée
*puis* synthétisée. Sur CPU avec un modèle 1,2 B, cela fait facilement 4 à 10
secondes de silence — ce qui casse l'illusion conversationnelle.

Le levier le plus rentable du projet serait le **streaming par phrases** :

```
LLM en stream=True → découpage à la ponctuation → TTS phrase par phrase
                   → file d'attente audio côté client
```

Le premier son sort alors après la première phrase (~1 s). Supertonic étant
rapide sur CPU, la synthèse suit largement le débit du LLM. Cela demande :
`stream=True` dans le payload, un parseur SSE, un découpage par phrase, et un
endpoint WebSocket ou `StreamingResponse` côté serveur.

### 8.2 Interruption (« barge-in »)

Aucun mécanisme d'annulation n'existe : une fois `chat_audio` lancé, l'appel va
au bout. Dans un mode mains-libres, pouvoir couper la parole de l'assistant est
une attente forte. Un `asyncio.Event` par session + `requests` remplacé par
`httpx.AsyncClient` (annulable) suffiraient.

### 8.3 Multi-session

Passer d'un `Assistant` global à un dictionnaire `session_id → Assistant`
(avec expiration) résoudrait d'un coup §4.5 (historique partagé) et une partie
des collisions de fichiers, et rendrait le serveur utilisable depuis deux
appareils du même réseau local.

### 8.4 Observabilité

Le projet utilise `print()` partout. Passer à `logging` avec des niveaux
permettrait de couper la verbosité en production et d'obtenir des durées par
étape (`asr_ms`, `llm_ms`, `tts_ms`) — précieux pour un projet dont l'enjeu
principal est la latence. Le champ `elapsed` global existe déjà : le détailler
serait un petit pas très utile.

---

## 9. Plan d'action priorisé

| # | Action | Sévérité | Effort |
|---:|---|---|---|
| 1 | Ajouter `static/index.html` (ou dégrader `GET /` en 503 explicite) | 🔴 bloquant | S |
| 2 | Assainir `file.filename` + valider avant écriture (`/api/upload_voice`) | 🔴 critique | S |
| 3 | Contrôle d'`Origin` sur toutes les requêtes non-GET (anti-CSRF local) | 🔴 critique | S |
| 4 | Valider la config avec Pydantic (`POST /api/config`) | 🟠 élevée | M |
| 5 | Invalider les modèles dans `reload_config()` | 🟠 élevée | S |
| 6 | `run_in_threadpool` dans `chat_audio` | 🟠 élevée | S |
| 7 | Noms de fichiers audio uniques + suppression des temporaires | 🟠 élevée | S |
| 8 | Fenêtre glissante sur l'historique | 🟠 moyenne | S |
| 9 | Nettoyer les balises `<laugh>` avant TTS (ou retirer la fonctionnalité) | 🟠 moyenne | S |
| 10 | `import requests` dans le `try` de `/api/models` | 🟡 faible | S |
| 11 | Écriture atomique de `config.json` ; sortir `config.json` de Git | 🟡 faible | S |
| 12 | Épingler les versions, retirer numpy/soundfile, `pyproject.toml` | 🟡 faible | M |
| 13 | `chmod +x` sur les `.sh` dans l'index Git | 🟡 faible | S |
| 14 | Tests unitaires + CI (ruff + pytest) | 🟡 dette | M |
| 15 | Streaming LLM → TTS par phrases | 🟢 amélioration | L |

---

## 10. Conclusion

VoxTria est un projet à la **conception saine** : la séparation ASR/LLM/TTS est
juste, la documentation est remarquable pour un dépôt de cette taille, et la
prise au sérieux des questions de licence et d'éthique du clonage vocal est un
signal de maturité rare.

Ce qui manque relève de l'**industrialisation**, pas de la conception : le
livrable est incomplet (l'interface web absente rend le produit inutilisable en
l'état), la surface HTTP fait confiance à ses entrées (une faille d'écriture de
fichier arbitraire, confirmée par un test), et l'état global du serveur suppose
un unique utilisateur, une unique requête à la fois.

Les points 1 à 3 du plan d'action sont bloquants et se traitent en une petite
journée. Les points 4 à 9 transformeraient le prototype en logiciel fiable.
Le point 15 (streaming) est celui qui changerait le plus la perception du
produit par ses utilisateurs.
