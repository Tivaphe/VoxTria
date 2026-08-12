# Intégration continue

Le workflow GitHub Actions est fourni ici plutôt que dans `.github/workflows/`
car le jeton utilisé pour pousser cette branche n'a pas la permission
`workflows` (GitHub refuse alors le push).

**Pour l'activer**, copie le fichier à son emplacement définitif :

```bash
mkdir -p .github/workflows
cp ci/github-actions-ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml && git commit -m "Active la CI"
```

Le workflow lance `ruff check .` puis `pytest -q` sur Python 3.10, 3.11 et 3.12.
Il n'installe ni `faster-whisper` ni `supertonic` : les tests couvrent l'API,
la validation de configuration et la logique du pipeline, sans inférence.
