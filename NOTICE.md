# NOTICE — Licences des composants tiers

Le code de **VoxTria** (ce dépôt) est distribué sous licence **MIT** (voir `LICENSE`).

⚠️ **Important** : VoxTria n'embarque PAS les modèles ; il les télécharge à
l'exécution. Chaque dépendance et chaque modèle conserve **sa propre licence**.
Certaines sont **plus restrictives que MIT**. Vous êtes responsable du respect
de ces licences pour votre usage (notamment commercial).

## Récapitulatif des licences

| Composant | Rôle | Licence | Type |
|---|---|---|---|
| **Code VoxTria** (ce dépôt) | application | **MIT** | permissive |
| FastAPI | serveur web | MIT | permissive |
| Uvicorn | serveur ASGI | BSD-3-Clause | permissive |
| requests | client HTTP | Apache-2.0 | permissive |
| numpy | calcul | BSD-3-Clause | permissive |
| soundfile | I/O audio | BSD-3-Clause | permissive |
| python-multipart | upload | Apache-2.0 | permissive |
| **faster-whisper** (code) | ASR | MIT | permissive |
| Modèles Whisper (poids) | ASR | MIT (OpenAI Whisper) | permissive |
| **Supertonic** (SDK/code) | TTS | MIT | permissive |
| **Supertonic** (POIDS du modèle) | TTS | **OpenRAIL-M** | ⚠️ **restrictions d'usage** |
| supertonic.embed (optionnel) | clonage voix | code de recherche / usage académique | ⚠️ restrictif |
| Modèle LLM (au choix) | LLM | dépend du modèle (ex. LFM Open License) | ⚠️ à vérifier |

## 🔴 La licence la plus restrictive : OpenRAIL-M (poids Supertonic)

Les **poids du modèle Supertonic** ne sont **pas** sous MIT mais sous
**OpenRAIL-M** (Open Responsible AI License). Points clés :

- ✅ Utilisation **commerciale autorisée**
- ✅ Modification et redistribution autorisées
- ⚠️ **Restrictions d'usage** : interdiction d'usages nuisibles, d'usurpation
  d'identité vocale sans consentement, de fraude, de désinformation, etc.
- ⚠️ **Obligation d'attribution** et de propager les restrictions d'usage en aval.

➡️ OpenRAIL-M **n'est pas équivalent à MIT**. Lisez le texte intégral de la
licence avant tout déploiement : la carte modèle Supertonic sur Hugging Face.

## ⚠️ Clonage de voix (supertonic.embed)

Le module optionnel de clonage local est du **code de recherche à usage
académique**. Le clonage de voix engage des responsabilités légales et
éthiques fortes :

- N'effectuez du clonage **qu'avec le consentement explicite** du locuteur.
- Le clonage non consenti d'une personne identifiable peut être **illégal**.
- Signalez clairement tout audio synthétique comme généré par IA.

## Modèle LLM

VoxTria se connecte à un serveur LLM externe (LM Studio, llama.cpp, ou une API).
Le **modèle que vous chargez** possède sa propre licence (ex. LFM2.5 est sous
« LFM Open License v1.0 »). Vérifiez-la selon votre usage.

## Conséquence pratique pour la distribution

- Vous pouvez publier **le code VoxTria** sous MIT sans problème.
- Vous **ne redistribuez aucun poids de modèle** dans ce dépôt (ils sont
  téléchargés par l'utilisateur), ce qui simplifie la conformité.
- Indiquez clairement à vos utilisateurs que **l'usage des modèles** (surtout
  Supertonic/OpenRAIL-M et le LLM choisi) est soumis aux licences respectives.
