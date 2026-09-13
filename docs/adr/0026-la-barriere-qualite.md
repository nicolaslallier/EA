---
titre: La barrière qualité — CI, pre-commit, une vérification qui ne corrige rien
date: 2026-09-13
statut: Accepté
affects: .github/workflows/ci.yml, .github/dependabot.yml, .pre-commit-config.yaml, Makefile, backend/pyproject.toml, backend/uv.lock, deploy/*.stack.yml
---

# 26. La barrière qualité

Date : 2026-09-13
Statut : Accepté

Complète [`0001`](0001-orchestration-locale-via-makefile.md), qui laissait
explicitement la CI hors périmètre, et [`0003`](0003-uv-comme-chaine-outils-python.md).

## Contexte

`CLAUDE.md` décrivait une barrière qualité que le dépôt n'avait pas. Elle
promettait une CI verte, `pre-commit`, un plancher de couverture à 90 %,
`bandit`, `pip-audit` et `npm audit`. Une relecture a montré que presque rien
de tout cela n'existait, et que ce qui existait mentait :

- **`make check` ne pouvait pas échouer sur le formatage.** Il appelait `lint`,
  qui lance `ruff format .` et `ruff check --fix .` : la vérification
  *corrigeait* en passant, puis constatait que tout allait bien. Une cible
  « tout ce que la CI vérifiera » réécrivait des fichiers qu'on n'avait pas
  relus.
- **`make check` échouait sur un clone neuf.** `lint`, `test` et `typecheck`
  n'avaient pas la dépendance sur le témoin du venv ; `uv run` construisait
  alors un environnement *sans* l'extra `dev` — ni ruff, ni pytest, ni mypy —
  et l'échec disait « command not found » au lieu de « lance `make install` ».
- **Le plancher de 90 % n'était écrit nulle part où un outil le lit.**
- **`bandit` et `pip-audit` n'étaient pas des dépendances.** Les commandes de
  `CLAUDE.md` échouaient.
- **Aucune configuration `pre-commit`, aucune CI, aucune mise à jour de
  dépendances.** `install-fe` lançait `npm install`, qui réécrit le lockfile.
- **Les images des stacks suivaient un tag mobile.** `neo4j:5.26-community` et
  `pgvector/pgvector:pg17` sont republiés à chaque correctif ; un redéploiement
  depuis Portainer changeait de serveur sans que le dépôt change.

## Décision

**Une seule barrière, décrite par le Makefile, appliquée à trois endroits : le
poste (`make check`), le commit (`pre-commit`) et la pull request (CI).**

### Vérifier n'est pas corriger

| Cible | Modifie des fichiers | Rôle |
|---|---|---|
| `make lint` | oui | `ruff format` puis `ruff check --fix` — le geste du développeur |
| `make lint-check` | non | `ruff format --check` puis `ruff check` — ce que vérifient `check`, pre-commit et la CI |
| `make lint-fe` | selon `npm run lint` | ESLint sur le frontend |
| `make typecheck-be` / `typecheck-fe` | non | mypy --strict sur `src` et `migrations` ; vue-tsc |
| `make test` | non | unitaires et API, `--cov=ea`, plancher de couverture |
| `make audit` | non | `bandit`, `pip-audit`, `npm audit --audit-level=high` |
| `make check` | **non** | `lint-check lint-fe typecheck openapi-check test test-fe` |
| `make hooks` | `.git/hooks` | `pre-commit install`, à lancer soi-même |

Chaque cible qui passe par `uv run` déclare le témoin du venv en prérequis ;
chaque cible qui passe par `npm` déclare `node_modules`. Un clone neuf reçoit
« lance d'abord : make install », jamais un environnement à moitié construit.
`install-fe` passe à `npm ci` : installer n'est pas mettre à jour.

### Le plancher de couverture est une configuration

`[tool.coverage.report] fail_under = 90` dans `backend/pyproject.toml`. Toute
exécution qui passe `--cov` échoue en dessous : `make test`, donc `make check`,
donc la CI. Le chiffre mesuré à l'écriture de cet ADR, unitaires et API
seulement, est 93 %.

### Les scanners sont des dépendances

`bandit[toml]` et `pip-audit` rejoignent l'extra `dev`, donc `uv.lock` : une
version, la même partout. `bandit` lit `[tool.bandit]` dans `pyproject.toml`
et exclut les tests, qui manipulent volontairement des mots de passe et des
adresses d'écoute littéraux. Une trouvaille dans `src` qui est un faux positif
se tait **à la ligne**, par `# nosec BXXX` et un commentaire qui dit pourquoi —
jamais par une exclusion globale, qui tairait aussi la prochaine vraie.
`pip-audit` audite le venv tel qu'il est installé (`--skip-editable` : le
paquet `ea` lui-même n'est pas sur PyPI). `npm audit` bloque à partir de
`high`. `make audit` lance les trois et échoue si l'un échoue, après les avoir
tous lancés.

### pre-commit, sans seconde chaîne d'outils

`.pre-commit-config.yaml` déclare des crochets `local` qui appellent
`uv run --extra dev ruff …`, `uv run --extra dev mypy …` et `npm run
typecheck` / `npm run lint`. Les versions de ruff et de mypy sont donc celles de
`uv.lock` — pas une seconde copie épinglée dans la configuration pre-commit,
qui divergerait du venv, du Makefile et de la CI. Aucun crochet ne modifie de
fichier. Le détecteur de secrets est **gitleaks**, épinglé à une révision : il
n'appartient à aucune des deux chaînes d'outils.

`pre-commit` lui-même n'est pas une dépendance du projet : `make hooks` le
lance par `uvx`, à une version épinglée dans le Makefile. **Rien n'installe les
crochets à la place du développeur** : écrire dans `.git/hooks` est son choix.

### La CI rejoue le Makefile

`.github/workflows/ci.yml`, sur chaque push vers `main` et chaque pull request,
quatre jobs en parallèle :

| Job | Contenu |
|---|---|
| `backend` | `make install`, `lint-check`, `typecheck-be`, `openapi-check`, `test` |
| `frontend` | `make install-fe`, `typecheck-fe`, `lint-fe`, `test-fe`, `npm run build` |
| `audit` | `make install`, `make audit` |
| `integration` | conteneurs de service `pgvector/pgvector:pg17` et `neo4j:5.26-community` sur 127.0.0.1, `EA_ALLOW_DESTRUCTIVE_TESTS=1`, `pytest tests/integration` |

Les jobs appellent les cibles plutôt que de recopier leurs commandes : une
commande qui change dans le Makefile change en CI du même coup. `UV_LOCKED=1`
fait échouer un `uv.lock` périmé au lieu de le réécrire. Les tests
d'intégration visent des bases jetables, jamais le cluster — que la CI ne
joint de toute façon pas — et le Neo4j de test est publié sur 7688, comme en
local (`0024`). Les actions sont épinglées au tag majeur, sauf `setup-uv`, qui
n'en publie pas.

### Dépendances et images : épinglées, et mises à jour

Les deux stacks épinglent leur image **par tag et par digest**
(`neo4j:5.26-community@sha256:…`), le digest étant celui de l'index
multi-architecture. Le tag reste pour qu'on sache ce que c'est.
`test_postgres_stack.py` en fait une garde, comme `test_deploy_stack.py` pour
le graphe.

Un digest épinglé est un correctif de sécurité qu'on ne reçoit plus sans aide :
`.github/dependabot.yml` ouvre des pull requests hebdomadaires pour les
actions, `uv`, `npm` et les fichiers compose, mineures et correctifs groupés.
Chacune passe par la même CI.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder `lint` dans `check`, corriger en CI | Une cible de moins | Une vérification qui corrige ne peut pas échouer ; la CI validerait un code différent de celui du commit | Écarté |
| Crochets pre-commit officiels (`ruff-pre-commit`, `mirrors-mypy`) | Isolés, sans venv | Deuxième version de ruff à tenir en phase avec `uv.lock` ; mypy sans les dépendances du projet ne vérifie pas les types de FastAPI ni de SQLAlchemy | Écarté |
| `detect-secrets` plutôt que gitleaks | Python, donc installable par uv | Exige un fichier de référence versionné à entretenir | Écarté |
| `make test-integration` en CI (docker compose) | Exactement la commande locale | Démarrage et arrêt des conteneurs gérés à la main dans le job ; les conteneurs de service le font nativement | Écarté |
| Épingler les actions par SHA | Immuable | Illisible, et Dependabot met déjà à jour les tags | Écarté pour l'instant |
| Renovate plutôt que Dependabot | Plus configurable, groupe les digests | Une application tierce à installer sur le dépôt | Écarté |

## Conséquences

- **`make check` et `make audit` peuvent désormais échouer, et c'est le but.**
  Leur première exécution a trouvé deux choses, réglées dans la même
  modification : `npm audit` signalait deux vulnérabilités `high` (`js-yaml`,
  par `@redocly/openapi-core`, par `openapi-typescript`), corrigées par
  `npm audit fix` sans changer le client généré ; et `bandit` signalait B104
  sur `host: str = "0.0.0.0"` dans `core/config.py`, voulu (`0016`) et tu par
  un `# nosec B104` à la ligne, la raison dans le commentaire au-dessus.
- **La première exécution de la CI n'a pas eu lieu à l'écriture.** Le fichier
  est validé syntaxiquement ; les conteneurs de service, leurs healthchecks et
  la détection des fichiers `deploy/*.stack.yml` par Dependabot (qui cherche
  des noms de fichier compose) restent à constater sur GitHub.
- **Un commit est plus lent** d'environ le temps de mypy. `--no-verify` reste
  interdit par `CLAUDE.md` ; si cela devient gênant, mypy passe en `pre-push`.
- **`make test` sur un fichier isolé avec `--cov` échoue sous le plancher.** On
  lance `uv run pytest tests/unit/test_x.py` sans `--cov` pour une boucle
  rapide.
- **Changer de version d'image est un commit.** Un correctif de Neo4j ou de
  PostgreSQL arrive en pull request Dependabot, passe les tests d'intégration
  sur la nouvelle image, puis se redéploie depuis Portainer.
- **Les écarts restants à `CLAUDE.md`** : Playwright (E2E navigateur) n'existe
  toujours pas, et rien n'impose encore la CI verte avant fusion — c'est une
  règle de protection de branche sur GitHub, à activer.

## Références

- ADR liés : [0001](0001-orchestration-locale-via-makefile.md),
  [0003](0003-uv-comme-chaine-outils-python.md),
  [0016](0016-ecoute-sur-toutes-les-interfaces.md),
  [0025](0025-sauvegardes-des-deux-bases.md)
- Code concerné : `.github/workflows/ci.yml`, `.github/dependabot.yml`,
  `.pre-commit-config.yaml`, `Makefile` (section *Qualité*),
  `backend/pyproject.toml`, `backend/tests/unit/test_postgres_stack.py`
