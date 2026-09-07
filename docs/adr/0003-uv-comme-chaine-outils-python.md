# 3. `uv` comme chaîne d'outils Python, plutôt que venv + pip

Date : 2026-09-07
Statut : Accepté

## Contexte

L'analyse fonctionnelle décrivait l'installation backend comme
`python3 -m venv venv` puis `pip install -r requirements.txt`, avec le venv à la
racine du dépôt. `CLAUDE.md` verrouillait de son côté `uv` pour les dépendances
et l'environnement virtuel.

`requirements.txt` ne verrouille pas l'arbre transitif sans outillage
supplémentaire (`pip-compile`), et ne distingue pas proprement les dépendances de
développement de celles d'exécution.

## Décision

La chaîne d'outils Python est **`uv`**, conformément à `CLAUDE.md`.

- Les dépendances sont déclarées dans `backend/pyproject.toml` : exécution dans
  `[project.dependencies]`, outillage dans l'extra `dev`.
- `backend/uv.lock` est versionné et fait foi.
- `make install` exécute `uv sync --all-extras`, qui crée et peuple
  `backend/.venv`. Le Makefile ne manipule jamais un venv à la main.
- Pas de `requirements.txt`, pas de venv à la racine du dépôt.

## Conséquences

- `uv` devient une dépendance système au même titre que `make` et `node`.
  `make install-be` le vérifie et indique `brew install uv` s'il manque.
- Le venv vit dans `backend/.venv` et non `./venv` ; le `.gitignore` couvre déjà
  les deux.
- `ruff`, `mypy`, `pytest` et les scans de sécurité s'invoquent via `uv run`
  depuis `backend/`, sans activation manuelle de l'environnement.
- Un contributeur habitué à `pip install -r requirements.txt` doit apprendre
  `uv sync`. Le `make install` masque cette différence pour le cas nominal.
