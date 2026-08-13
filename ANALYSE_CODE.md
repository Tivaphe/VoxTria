# 🔍 VoxTria — Analyse du code

> Analyse réalisée le 2026-08-12 sur le commit `0707391` (v0.2.0, branche
> `arena/019ff7df-voxtria`). Elle **remplace l'analyse précédente** de ce
> fichier, qui décrivait un état ancien du projet (interface web absente,
> aucun test, failles ouvertes) : ces problèmes sont **corrigés** dans la
> version courante.
>
> Méthode : lecture intégrale du code, compilation (`py_compile`), lint
> (`ruff`), exécution réelle de la suite de tests, et **test de fumée du
> serveur** lancé pour de vrai (uvicorn + requêtes HTTP sur les endpoints).

---

## 1. Synthèse

**VoxTria est un assistant vocal 100 % local** qui enchaîne trois briques :

```
🎤 Micro → ASR (faster-whisper) → LLM (serveur OpenAI-compatible) → TTS (Supertonic) → 🔊 audio
```

Le verdict global est **positif** : le code est propre, défensif, documenté,
testé, et les failles de sécurité identifiées lors de la revue précédente ont
trait à des correctifs réels et vérifiables (tests de non-régression inclus).

| Axe | Note | Commentaire |
|---|---|---|
| Architecture | 🟢 | 3 briques isolées + orchestrateur, interchangeable comme promis |
| Robustesse | 🟢 | Config jamais fatale, erreurs dégradées proprement, rollback d'historique |
| Sécurité | 🟢 (en local) | CSRF, confinement des fichiers, validation stricte ; pas d'auth (assumé) |
| Tests | 🟢 | 104 tests passent en ~3 s, sans télécharger de modèle |
| Documentation | 🟠 | Excellente, mais **3 affirmations ne correspondent plus au code** |
| Supply chain | 🟠 | `clone_voice.py` clone un repo tiers **non épinglé** (voir §5.1) |
| Historique git | 🟠 | Un seul commit « Add files via upload » : dump, pas d'historique |

Résultats des vérifications exécutées pendant cette analyse :

```
pytest -q        → 104 passed in 2.7s        ✅
ruff check .     → All checks passed!        ✅
py_compile       → 4/4 modules compilent     ✅
uvicorn server   → démarre, sert l'UI (200)  ✅
  GET /api/health          → 200, JSON correct
  POST /api/clear (Origin externe) → 403 (CSRF actif) ✅
  POST /api/chat_text sans LLM → 502 avec message lisible ✅
```

> Mise à jour 2026-08-12 : la vérification des corrections annoncées
> (voir **`VERIFICATION.md`**) a confirmé 15/17 revendications, découvert un
> bug réel dans `split_sentences` (guillemets français « … ») — **corrigé** —
> et ajouté 26 tests (78 → 104). Une seconde chasse a ajouté listes numérotées,
> validation 400 vs 500, et passthrough `voice` vers le SDK (voir §8 de
> VERIFICATION.md).

---

## 2. Inventaire

| Fichier | Lignes | Rôle |
|---|---:|---|
| `pipeline.py` | 640 | Briques `ASR` / `LLM` / `TTS` + orchestrateur `Assistant` |
| `server.py` | 477 | API FastAPI (14 endpoints) + service de l'interface |
| `static/index.html` | 879 | Interface web complète (HTML/CSS/JS, zéro dépendance) |
| `clone_voice.py` | 229 | Clonage de voix local (optionnel, GPU requis) |
| `config_schema.py` | 159 | Schéma Pydantic v2 de la configuration |
| `tests/` (5 fichiers) | 572 | 48 fonctions de test, 78 cas avec paramétrisation |
| `config.example.json` | 35 | Modèle de config (la vraie `config.json` est ignorée par git) |
| `install/run .sh/.bat` | ~150 | Scripts d'amorçage Windows / Linux / macOS |
| `ci/github-actions-ci.yml` | 37 | Workflow CI **prêt mais non activé** (voir §5.6) |
| `README.md` / `NOTICE.md` | ~480 | Documentation utilisateur + licences |

---

## 3. Architecture

### 3.1 `pipeline.py` — le cœur

Trois classes à responsabilité unique, instanciées par un orchestrateur
`Assistant` (dataclass) :

- **`ASR`** : enrobe `faster-whisper`. Chargement paresseux (`_lazy()`), et
  surtout un mécanisme de **signature** (`_sig` / `invalidate_if_changed`) :
  changer `model_size` dans l'UI décharge le modèle et force son rechargement.
  C'est le correctif d'un bug réel (changement sans effet avant redémarrage),
  couvert par `test_changer_le_modele_asr_decharge_le_modele`.
- **`LLM`** : client OpenAI-compatible basé sur `requests`, versions simple et
  **streaming** (`chat_stream`, parseur SSE maison). Bons points : fenêtre
  glissante d'historique (`max_history_turns`), clé API lue d'abord depuis
  `VOXTRIA_API_KEY`, distinction explicite `ConnectionError` / `Timeout` /
  HTTP ≠ 200 avec messages lisibles en français.
- **`TTS`** : enrobe Supertonic avec deux protections bien pensées contre un
  SDK à l'API mouvante : introspection de la **signature réelle** de
  `synthesize` (`inspect.signature`) pour filtrer les kwargs, et repli sur les
  presets si `list_voices` échoue. Résolution de voix `custom:<nom>` **confinée
  à `./voices/`** via `safe_voice_path`.

Deux utilitaires transverses méritent d'être soulignés :

- **`split_sentences`** : découpe en phrases résistante au streaming —
  exige une espace après la ponctuation finale (écarte les décimaux « 3.5 »),
  whitelist d'abréviations (`M.`, `Dr.`, `etc.`), initiales isolées,
  ponctuation fermante (`»`, `)`…), et garde-fou `max_len` pour les flux sans
  ponctuation. Subtil et correct.
- **`strip_expression_tags`** : retire `<laugh>`, `<sigh>`… avant synthèse
  (sinon le TTS les prononce littéralement) avec attention à la typographie
  française (on ne recolle pas avant `;:!?`).

L'orchestrateur ajoute ce qui distingue un prototype d'un produit :
**verrou global** sérialisant les tours de parole (historique cohérent sous
concurrence — testé `test_historique_partage_serialise`), **rollback** du tour
utilisateur si le LLM échoue, **succès partiel** si le TTS échoue (le texte
n'est pas perdu), et **streaming phrase par phrase** via générateur (premier
son en ~0,2 s au lieu d'attendre toute la réponse).

### 3.2 `server.py` — la couche HTTP

API FastAPI soignée. Les bonnes décisions :

- **Middleware anti-CSRF** : les requêtes non-GET d'origine externe sont
  refusées (403). Justification documentée dans le code : sans auth,
  n'importe quel onglet du navigateur pourrait sinon piloter l'assistant. La
  règle « même hôte que l'URL servante » couvre les usages LAN/proxy.
- **Cycle de vie des fichiers** : noms de sortie uniques (`uuid`), purge TTL
  30 min sur `_out/`, suppression du wav d'entrée dans un `finally`.
- **Patterns async corrects** : le pipeline synchrone (ONNX + HTTP) est
  poussé dans `run_in_threadpool` pour ne pas geler la boucle d'événements ;
  le streaming pont proprement thread → asyncio (`call_soon_threadsafe` +
  `Queue`).
- **La clé API n'est jamais renvoyée** au navigateur (remplacée par le
  drapeau `api_key_set`), et une clé vide en entrée n'efface pas la clé
  stockée.
- **Dégradation explicite** : `index.html` absent → page 503 qui explique,
  au lieu d'un 500 opaque. `/api/models` ne lève jamais.
- **`get_audio` valide le chemin résolu** (parent + suffixe) au lieu de
  s'en remettre au routage.

### 3.3 `config_schema.py` — la validation

Pydantic v2, `extra="ignore"` (tolérant aux anciennes configs), **bornes sur
tous les nombres**, whitelists sur les enums, `base_url` normalisée vers
`/v1` à un seul endroit, et `custom_style_path` **confiné à `./voices/`**
(correctif d'une faille de lecture de fichier arbitraire, testée).

### 3.4 `static/index.html` — l'interface

879 lignes autonomes (aucune dépendance JS/CDN) : panneau de réglages
complet, file d'attente audio sans chevauchement pour le streaming,
**VAD énergétique** (RMS via `AnalyserNode`) pour la détection de fin de
parole, mode mains-libres, parser SSE côté client. Points notables :

- Pas d'`innerHTML` avec du contenu dynamique (tout en `textContent` /
  nœuds texte) → **pas de XSS** malgré du texte produit par un LLM.
- URLs **relatives** → fonctionne derrière un proxy ou en accès LAN.
- Cas limites gérés : lecture auto bloquée par le navigateur, micro refusé,
  `getUserMedia` sans HTTPS (bouton grisé).

### 3.5 Tests — `tests/`

48 fonctions / 78 cas, exécutés dans un répertoire temporaire (`conftest.py`
copie les modules dans un `tmp_path` et purge `sys.modules` — isolement
réel du vrai `config.json`). Organisation parlante : `test_security.py` est
une **suite de non-régression des failles corrigées**, chaque docstring
rappelant le bug d'origine. Couverture : API, validation de config, logique
pipeline, concurrence, confinement des écritures, CSRF.

---

## 4. Points forts

1. **Correctifs prouvés par des tests.** Chaque faille de la revue
   précédente a son test de non-régression (traversée de répertoire, CSRF,
   fuite de clé API, nom de fichier fixe, config corrompue…).
2. **Défense en profondeur** : le confinement de `./voices/` est vérifié à
   trois niveaux (regex du nom, résolution du chemin, validation Pydantic).
3. **Choix d'ingénierie expliqués dans le code** : les commentaires n'écrivent
   pas *ce que* fait le code mais *pourquoi* (souvent « Avant : … »).
4. **Expérience de dégradation** : chaque brique peut échouer sans casser les
   autres (réponse texte sans audio, voix fallback, modèles LLM non
   découvrables → saisie manuelle).
5. **Documentation** : README complet (dépannage, licences, sécurité) et un
   `NOTICE.md` qui traite honnêtement la licence OpenRAIL-M des poids TTS.

---

## 5. Anomalies et risques restants

### 5.1 🟠 `clone_voice.py` : épinglage promis mais absent

```python
# Commit épinglé : sans cela, le code tiers exécuté par ce script peut
# changer à tout moment côté amont. Mettre à jour sciemment après revue.
REPO_REF = "main"
```

Le commentaire annonce un commit épinglé, mais la valeur est `"main"` — une
référence **flottante**. Le script exécute donc du code tiers non figé
(`git clone` → `optimize_style.py`) avec installation de dépendances dans
l'environnement courant : c'est précisément le risque supply-chain que le
commentaire dit adresser. **Fix** : remplacer par un SHA complet.

### 5.2 🟠 Documentation en dérive — **corrigée le 2026-08-12**

Constat d'origine : le README annonçait 130 tests (il y en avait 78),
prétendait couvrir `split_sentences` et la VAD sous Node (aucun de ces tests
n'existait) et mentionnait une police Google Fonts absente du HTML.

**Résolu** : 21 tests `split_sentences` + 1 test SSE + tests de validation
ajoutés (104 tests au total), README resynchronisé. Voir `VERIFICATION.md`.

### 5.3 🟡 Concurrence et cycle de vie du streaming

- Si le client se déconnecte pendant `/api/chat_stream`, le thread producteur
  continue : synthèses TTS inutiles, wav écrits, `purge_old_audio()` non
  appelé (il le sera au prochain appel — coût borné, mais réel).
- La `asyncio.Queue` du pont thread→asyncio n'est pas bornée (contre-pression
  absente).
- Le verrou global d'`Assistant` sérialise **tous** les tours de tout le
  monde : acceptable pour l'usage visé (local, mono-utilisateur) mais à
  documenter comme limite de conception.

### 5.4 🟡 Menu fretin

- `chat_audio` enregistre l'upload du navigateur (souvent du **WebM/Opus**)
  sous un nom en `.wav` ; ça marche parce que whisper s'appuie sur ffmpeg,
  mais le nom est trompeur.
- `purge_old_audio` peut supprimer un wav encore en file de lecture si le TTL
  (30 min) est dépassé — cas marginal.
- `respond_stream` utilise `while True` + `try/except StopIteration` là où un
  simple `for piece in stream` suffirait.
- Avertissement de dépréciation Starlette sur `TestClient`/httpx (à surveiller
  lors d'une future montée de version).
- `ruff format --check` reformaterait 10 fichiers : le format n'est pas
  appliqué ni vérifié en CI (seul le lint l'est).

### 5.5 🟠 Historique git et gestion de versions

- Un seul commit « Add files via upload » : le dépôt est un *dump*. Les
  docstrings des tests racontent l'historique des bugs… que git ne raconte
  pas.
- `requirements.txt` borne les versions mais **sans lock** — pour un SDK
  Supertonic dont le commentaire dit que l'API « a déjà changé entre
  versions », un `pip freeze > requirements-lock.txt` serait une bonne
  assurance.
- Pas de tag/release pour la v0.2.0 annoncée dans `pyproject.toml`.

### 5.6 🟡 CI livrée mais non activée

Le workflow vit dans `ci/` au lieu de `.github/workflows/` (permission
`workflows` manquante sur le token de push — expliqué dans `ci/README.md`).
Il suffit de le copier pour l'activer ; en l'état, **rien ne vérifie les
prochains commits**.

### 5.7 ℹ️ Sécurité : modèle de menace assumé — à ne pas oublier

Pas d'authentification : c'est un choix documenté pour l'usage local, avec
garde-fous (CSRF, confinement, debug masqué). Les résidus acceptables :
l'historique de conversation est lisible via `GET /api/history` (GET donc
hors CSRF, mais non lisible cross-origin grâce à l'absence de CORS), et les
messages d'erreur 502 incluent l'URL du LLM. Le README le dit bien : **ne
pas exposer tel quel sur Internet**.

---

## 6. Recommandations priorisées

| # | Action | Effort | Impact | État |
|---|---|---|---|---|
| 1 | Ajouter des tests pour `split_sentences` | faible | élevé | ✅ **fait** (21 tests) |
| 2 | Épingler `REPO_REF` sur un SHA dans `clone_voice.py` | trivial | élevé | ⬜ à faire |
| 3 | Corriger le README (nombre de tests, VAD/Node, Google Fonts) | faible | moyen | ✅ **fait** |
| 4 | Activer la CI (`cp ci/github-actions-ci.yml .github/workflows/`) | trivial | moyen | ⬜ à faire |
| 5 | Annuler le producteur de `/api/chat_stream` à la déconnexion client | moyen | moyen | ⬜ à faire |
| 6 | Committer le travail par étapes + taguer la v0.2.0 | faible | moyen | ⬜ à faire |
| 7 | Ajouter `ruff format --check` au lint CI (ou formatter une fois) | trivial | faible | ⬜ à faire |
| 8 | ~~Bug guillemets « … » dans le découpage~~ | faible | moyen | ✅ **fait** (découvert en vérif., voir VERIFICATION.md) |

---

## 7. Conclusion

VoxTria est passé d'un dump sans tests et avec failles (état décrit par
l'analyse précédente) à un **petit projet bien tenu** : architecture modulaire
réelle, sécurité locale réfléchie, tests de non-régression honnêtes, et une
documentation exemplaire dans son genre. Il reste trois dettes principales :
une **fonction critique non testée** (`split_sentences`), un **risque
supply-chain** dans le script de clonage, et une **documentation qui s'écarte
du code**. Aucun n'est bloquant ; les trois se corrigent en moins d'une
journée.
