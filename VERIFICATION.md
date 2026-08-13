# ✅ Vérification des corrections annoncées

> Chaque revendication du rapport de travaux a été confrontée au **code réel**
> et, quand c'était possible, à une **exécution** (tests, serveur lancé pour
> de vrai, SDK Supertonic 1.3.1 téléchargé depuis PyPI, logique JS exécutée
> sous Node 22). Date : 2026-08-12 — commit `0707391` + correctifs de cette
> session.
>
> **Verdict global : 15 revendications confirmées, 1 nuancée, 1 non
> mesurable, et 1 vrai bug découvert puis corrigé pendant la vérification.**

---

## 1. Correctifs de sécurité (3/3 ✅)

| # | Revendication | Statut | Preuve |
|---|---|---|---|
| S1 | Écriture de fichier arbitraire sur `/api/upload_voice` | ✅ **corrigé** | Nom rejeté s'il contient `/`, `\` ou `..` ; whitelist `SAFE_VOICE_NAME` ; écriture confinée via `safe_voice_path` ; JSON validé **avant** écriture. Testé en vrai : `filename=../../pwned.json` → **HTTP 400**, aucun fichier créé. Tests : `test_upload_rejette_la_traversee_de_repertoire` + 7 noms dangereux paramétrés + test de confinement. |
| S2 | Absence de protection CSRF | ✅ **corrigé** | Middleware `csrf_guard` sur toutes les méthodes non-GET. Testé en vrai : `POST /api/clear` avec `Origin: https://evil.example.com` → **HTTP 403**. Tests dédiés (refus externe, acceptation locale). |
| S3 | Configuration non validée | ✅ **corrigé** | Schéma Pydantic complet (bornes, enums, chemins). Testé en vrai : `temperature: 5` → **400** ; `custom_style_path: /etc/passwd` → **400**. Bonus vérifié : la clé API n'est jamais renvoyée (`api_key: ""`, drapeau `api_key_set: true`). |

## 2. Corrections de bugs (7/7 ✅)

| # | Revendication | Statut | Preuve |
|---|---|---|---|
| B1 | Modèle Whisper ne se rechargeait pas | ✅ | `ASR.invalidate_if_changed` compare une signature `(model_size, device, compute_type)` et décharge le modèle. Test `test_changer_le_modele_asr_decharge_le_modele`. |
| B2 | Boucle d'événements gelée | ✅ | `run_in_threadpool` sur `chat_text` / `chat_audio` / `tts_test` ; le streaming passe par un thread + `asyncio.Queue`. |
| B3 | Collisions sur `reply.wav` | ✅ | Noms uniques `uuid4`. Test `test_noms_audio_uniques` : 5 requêtes → 5 URLs distinctes, toutes servies. |
| B4 | Fichiers temporaires jamais supprimés | ✅ | `finally: in_path.unlink(missing_ok=True)` + purge TTL 30 min. Tests dédiés (suppression d'entrée, purge). |
| B5 | Historique non borné | ✅ | Fenêtre glissante `max_history_turns` dans `chat` et `chat_stream`. Test : 50 messages → 1 système + 6 envoyés. |
| B6 | Échec TTS perdait la réponse texte | ✅ | `_respond` renvoie un succès partiel (`audio=None`, `tts_error`). Test dédié. |
| B7 | — (cohérence concurrence) | ✅ | Bonus vérifié : verrou global, test `test_historique_partage_serialise` (alternance stricte user/assistant sous 4 requêtes parallèles). |

## 3. Les deux « faux amis » du SDK (2/2 ✅ — vérifiés contre le vrai SDK)

Le package `supertonic-1.3.1` a été **téléchargé depuis PyPI** et inspecté :

| # | Revendication | Statut | Preuve |
|---|---|---|---|
| F1 | `list_voices()` ne pouvait pas fonctionner | ✅ **confirmé** | Le SDK publie `voice_style_names` (attribut, `pipeline.py:132` du SDK) — pas de méthode `list_voices()`. Le code de VoxTria tente `voice_style_names` en premier : correct. Également vérifié au passage : `get_voice_style(voice_name=...)`, `get_voice_style_from_path(...)` et la signature réelle de `synthesize` (`total_steps`, `speed`, `silence_duration`, `lang` existent tous) et son retour en `tuple` (le code prend `result[0]` : correct). |
| F2 | Balises `<laugh>` non supportées par le SDK | ✅ **confirmé** | Aucune occurrence de `laugh`/`sigh`/`breath` dans le source du SDK : les balises seraient lues littéralement. Le nettoyage `strip_expression_tags` est donc justifié (10 cas paramétrés, dont `<LAUGH>`, `</laugh>`, `<laugh/>`). |

## 4. Phase 3 — Interface web

| # | Revendication | Statut | Preuve |
|---|---|---|---|
| I1 | `static/index.html` reconstruit (il était absent) | ✅ | 879 lignes présentes, `GET /` → **200** en vrai. |
| I2 | Syntaxe JS validée | ✅ **rejoué** | Extraction du `<script>` (633 lignes) → `node --check` : **OK**. |
| I3 | Cohérence des endpoints | ✅ **rejoué** | Les 9 routes appelées par le JS (`chat_text`, `chat_stream`, `chat_audio`, `clear`, `config`, `models`, `tts_test`, `upload_voice`, `voices` + `voices/{name}`) existent toutes côté serveur. Les URLs audio proviennent du serveur (`/api/audio/{name}`). |
| I4 | « Logique VAD extraite et exercée sous Node » | ⚠️ **nuancé** | **Aucun artefact de test Node n'est versionné** : la validation d'alors a été faite en session puis perdue (le README la présente pourtant au présent comme une couverture de tests). Rejouée pendant cette vérification avec 5 scénarios synthétiques : parole→silence ⇒ coupure à +1 s ; parole continue ⇒ plafond 30 s ; souffle sous le seuil ⇒ jamais « de la parole » ; silence total ⇒ pas d'envoi ; pic bref ⇒ coupure après le silence. **La logique est correcte**, mais l'affirmation « les tests couvrent la VAD » reste fausse au sens « tests versionnés ». |

## 5. Phase 4 — Streaming LLM → TTS

| # | Revendication | Statut | Preuve |
|---|---|---|---|
| T1 | Découpage qui ne coupe pas sur `3.5` ni `M. Dupont` | ✅ **exécuté** | `split_sentences("Le taux est 3.5, pas 4.")` → aucune coupe ; idem `M.`, `etc.`, initiales. Flux `"3."` + `"5 sur 20. "` en deux morceaux → recombiné correctement. **Et ces cas sont désormais des tests versionnés** (ils n'existaient pas avant cette vérification). |
| T2 | Endpoint SSE `/api/chat_stream` | ✅ | Présent, format `data: {…}` vérifié par un nouveau test direct (`delta` → `sentence` → `done`, conversion `audio` → `audio_url`). |
| T3 | File d'attente audio côté UI | ✅ logique relue | `audioQueue` / `drainQueue` : enchaînement sans chevauchement, échec de lecture non bloquant. Non testable sans navigateur. |
| T4 | « Premier son à ~0,2 s au lieu de ~1,5 s » | ⏱️ **non mesurable ici** | Exigerait les vrais modèles (ASR/TTS) chargés. Le mécanisme (synthèse dès la première phrase) existe et est bon ; le chiffre précis reste une affirmation de la session précédente. |

### 🐛 Bug découvert pendant la vérification — puis corrigé

`split_sentences("« Bonjour. » dit-il. ")` renvoyait `['« Bonjour.', '» dit-il.']` :
la typographie française met une **espace avant le fermant**, que le code
n'absorbait pas — guillemets orphelins des deux côtés, mal prononcés par le TTS.
Deux mécanismes ajoutés dans `pipeline.py` :

1. absorption des fermants après espaces (`« Bonjour. »` → coupure **après** le `»`) ;
2. **citation non refermée** : une phrase contenant un « ou " non fermé n'est pas
   émise (le `»` peut arriver dans le morceau suivant) — avec le garde-fou
   `max_len` qui force l'émission si le LLM n'a jamais refermé.

18 nouveaux tests ont couvert ces cas (dont les citations multi-phrases et le
flux morcelé). Suite complète : **78 → 96 tests, tous verts**, `ruff` propre.

## 6. Phase 5 — Livraison

Hors périmètre du code (blocages plateforme de l'époque) : rien à vérifier.

## 7. Ce qui a été corrigé pendant cette vérification

| Action | Fichier |
|---|---|
| Bug guillemets français corrigé + règle « citation non refermée » | `pipeline.py` |
| 17 tests `split_sentences` (la fonction critique n'en avait **aucun**, contrairement à ce que dit le README) | `tests/test_pipeline.py` |
| 1 test direct de l'endpoint SSE | `tests/test_api.py` |
| README resynchronisé (96 tests, couverture réelle) | `README.md` |

### Reste à faire → traité en fin de session

- ~~`clone_voice.py` : `REPO_REF = "main"`~~ → **épinglé sur `ec5325e`**
  (résolu au SHA de `main` au 2026-08-12), checkout idempotent.
- ~~CI non activée~~ → `.github/workflows/ci.yml` en place.
- ~~Producteur SSE non annulé à la déconnexion~~ → drapeau `stop` +
  `gen.close()` (libère le verrou sans attendre le GC) + purge systématique,
  test dédié de déconnexion précoce.
- Pas de test navigateur (VAD + queue audio relus mais exercés sous Node
  uniquement de façon ponctuelle) — **toujours ouvert**.

---

## 8. Seconde chasse (fuzz + sondes d'entrées) — même session

Après la vérification, une passe offensive supplémentaire (fuzz aléatoire,
corps de requêtes mal typés, relecture du SDK téléchargé) a trouvé — puis
corrigé — trois problèmes supplémentaires :

| # | Trouvaille | Gravité | Correctif |
|---|---|---|---|
| 8.1 | **Listes numérotées** : `« 1. Premier point. »` envoyait `« 1. »` seul au TTS (prononcé « un » isolément). La règle « initiale isolée » ne couvrait que les lettres. | 🟠 qualité audio | Un chiffre ≤ 2 n'ouvre une coupure que s'il **termine** la phrase (`« Tu as 20. »` coupe ; `« 1. … »` non). |
| 8.2 | **500 opaques au lieu de 400** : `chat_text`/`chat_stream` avec `{"text": 42}` → `AttributeError` sur `.strip()` ; `tts_test` avec `total_steps: "abc"` ou `text: 42` → 500. | 🟡 robustesse API | Validation explicite + messages 400 lisibles (`_message_text`, coercion bornée). |
| 8.3 | **Passthrough non assaini vers le SDK** : `tts_test?voice=` était transmis tel quel à `get_voice_style()`, qui résout `model_dir/voice_styles/f"{name}.json"` **sans validation** dans supertonic 1.3.1 → lecture d'un `.json` arbitraire via `../../`. Local uniquement, faible impact (le contenu devient un style vocal, jamais renvoyé), mais incohérent avec le durcissement appliqué partout ailleurs. | 🟡 sécurité (défense en profondeur) | `voice` doit correspondre à `VOICE_RE` (preset ou `custom[:nom]`), comme dans la config validée. |

Vérifié sain au passage : **fuzz de `split_sentences` (8 000 cas aléatoires,
aucun crash, aucune perte de texte)** et `/api/config` avec corps liste/chaîne
→ 422 propre via FastAPI.

Suite après seconde chasse : **104 tests, tous verts, `ruff` propre.**
