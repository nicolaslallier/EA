# Cycle de vie du logiciel (SDLC)

Guide de travail au quotidien pour une brique — d'une idée sur une branche
jusqu'à sa merge et sa publication. Il complète [`CLAUDE.md`](../../CLAUDE.md)
(qui décrit la *pile*, le TDD et la sécurité) et les
[`ADR`](adr/) (qui enregistrent les *décisions* structurantes). Celui-ci décrit
le *parcours* d'un changement : dans quel ordre, avec quelles commandes, et ce qui
bloque la merge.

Un changement se lit d'un bout à l'autre en une page ; chaque point de
contrôle a une commande d'arrêt franche.

---

## 1. Le cycle, d'un coup d'œil

```mermaid
flowchart LR
    A[Idée / bug] --> B[Branche<br/>feat|fix|chore|docs]
    B --> C[TDD<br/>test qui échoue]
    C --> D[Implémentation]
    D --> E[Contrôles locaux<br/>make check]
    E --> F[Conventional Commit]
    F --> G[Pull Request]
    G --> H[CI verte<br/>lint+types+tests+scan]
    H --> I[Revue]
    I --> J[Merge sur main]
    J --> K[Changelog / release]
    D -. change le schéma OpenAPI .-> O[make openapi<br/>régénère le client]
    O -.-> E
    D -. décision structurante .-> P[ADR<br/>docs/adr]
    P -.-> G
```

Deux flux quittent le cœur du chemin et *re-entrent* avant la PR :

- **Le contrat front/back.** Toute modif qui déplace le schéma OpenAPI régénère
  `backend/openapi.json` *et* `frontend/src/api/`, sinon la PR est incomplète
  (voir [`ADR 0007`](adr/0007-client-openapi-genere-pour-le-spa.md)). On n'écrit
  jamais d'interface TypeScript à la main.
- **Les décisions structurantes.** Une nouvelle dépendance, un nouveau contexte
  borné, un changement d'auth ou de stockage, une nouvelle entrée dans une
  allowlist exige une ADR *dans le même commit* qui change le code.

---

## 2. Ramification

On part de `main`, qui reste reléasable à tout moment — on ne merge que du vert.

| Préfixe | Pour quoi | Exemple |
|---|---|---|
| `feat/` | Nouvelle fonctionnalité | `feat/edge-impact-traversal` |
| `fix/` | Correction de bug (précédée d'un test de régression) | `fix/cors-allow-list` |
| `chore/` | Outillage, config, dépendances | `chore/bump-neo4j-driver` |
| `docs/` | Documentation, ADR | `docs/sdlc` |

Règles :

- Une branche = un objectif. Pas de « fix » et « refactor » entremêlés sur la
  même branche : on fait deux PR.
- On rebase sur `main` avant d'ouvrir la PR, pour garder l'historique linéaire.
- Les branches ne vivent pas : elles se mergent vite, `main` reste le tronc stable.

---

## 3. TDD — le mode de travail par défaut

On écrit le test qui échoue *d'abord*, on le voit échouer pour la bonne raison,
puis on l'amène au vert. Par couche, d'avant en arrière :

1. **Unit** (`backend/tests/unit`, zéro I/O) : règles de domaine, validation,
   fonctions pures. En millisecondes.
2. **Intégration** (`backend/tests/integration`) : repositories et services
   *contre un vrai Neo4j et un vrai PostgreSQL* — les conteneurs jetables de
   `docker-compose.yml`, jamais le cluster. Jamais de driver mocké, jamais
   d'enregistrement factice — là, on prouve que le Cypher et le SQL marchent.
3. **API** (`backend/tests/e2e`) : `httpx.AsyncClient` contre l'application,
   couvrant les codes de statut et l'enveloppe d'erreur (et l'auth, quand elle
   existera).

Aucun test ne sort de la machine : une fixture automatique de
`backend/tests/conftest.py` lève `NetworkAccessInTestError` à toute connexion
qui n'est pas locale.

Un test qui a besoin d'une base de données n'est pas un test unit — on le déplace
en `tests/integration`.

```bash
make test-unit            # boucle rapide, sans base, millisecondes
make test                 # unit + API, toujours sans base
make test-fe              # Vitest du frontend, une passe
```

Les tests d'intégration vident le graphe entre chaque cas (Neo4j Community ne
sert qu'une seule base, il n'y a ni schéma de test séparé ni transaction à
annuler) et annulent la chaîne de migrations. Ils tournent donc sur un Neo4j et
un PostgreSQL **jetables et locaux**, publiés sur 127.0.0.1 ; les fixtures
refusent tout autre hôte et sautent le test en le nommant
(`backend/tests/integration/throwaway.py`). Le graphe exige en plus
`EA_ALLOW_DESTRUCTIVE_TESTS=1`. Voir
[`ADR 0024`](../adr/0024-tests-d-integration-sur-des-bases-jetables.md).

```bash
make test-integration     # démarre les deux conteneurs au besoin, et pointe dessus
make test-postgres        # seulement les tests PostgreSQL
```

Règles qui tiennent ici :

- Toute correction de bug commence par un **test de régression** qui le
  reproduit.
- Les tests affirment le comportement *par ses entrées publiques*, pas par des
  attributs privés.
- Les objets de test se construisent par de petites fonctions écrites à la
  main (`an_element`, `a_document`) : ajouter un champ ne casse pas cent tests.
- Jamais de `time.sleep` — on injecte une horloge.
- Le plancher de couverture est de **90 %** sur `backend/src`, imposé par
  `fail_under = 90` dans `backend/pyproject.toml` (`make test` passe `--cov`),
  mais *une ligne couverte qui ne prouve rien est un échec*, quel que soit le
  nombre.

---

## 4. Contrôles locaux

Avant de préparer un commit, on tourne ce que la CI vérifiera. `make check` ne
modifie aucun fichier : il échoue là où `make lint` corrigerait.

```bash
make check                # lint-check, ESLint, types, client généré, tests sans base (BE + FE)
make audit                # bandit, pip-audit, npm audit (réseau requis)
```

Détaillé, pour cibler :

| Commande | Effet |
|---|---|
| `make lint` | Corrige : `ruff format .` puis `ruff check --fix .` |
| `make lint-check` | Vérifie sans rien modifier : `ruff format --check`, `ruff check` |
| `make lint-fe` | ESLint sur le frontend |
| `make typecheck` | `mypy --strict` sur `backend/src` et `migrations`, puis `vue-tsc` sur le frontend |
| `make openapi-check` | Échoue si `openapi.json` / `src/api/` ne sont plus en phase |
| `make test` / `make test-fe` | Les suites sans base ; `make test` échoue sous 90 % de couverture |
| `make test-integration` | La suite contre les bases jetables locales (voir §3) |

Quand le schéma a bougé, on régénère *avant* de valider :

```bash
make openapi              # réécrit backend/openapi.json ET frontend/src/api/
```

`make check` doit être vert à la fin, pas juste « les tests passent ».

---

## 5. Commit

[Conventional Commits](https://www.conventionalcommits.org/) — le changelog et le
bump de version en sont *dérivés*, ils ne s'écrivent pas à la main.

```
<type>(<scope>): <objets du message, à l'infinitif>

<corps : le pourquoi, en option, qui tient sur deux lignes>

<footer : BREAKING CHANGE:, ref à une issue, en option>
```

- `feat`, `fix`, `chore`, `docs`, `refactor`, `test` sont les types attendus ;
  `<scope>` nomme l'implanté (`be`, `fe`, `db`, `config`) quand il est utile.
- Un commit = un changement atomique et rejouable.
- On n'utilise *jamais* `--no-verify`, on n'amende pas d'histoire partagée, on ne
  force pas sur `main`.

Exemple, tiré de l'historique réel :

```
feat(fe): écran de CRUD des éléments d'architecture
fix(config): read EA_CORS_ORIGINS as a plain string from the environment
```

---

## 6. Hook pre-commit

Le hook `pre-commit` (`.pre-commit-config.yaml`) fait tourner, *localement*,
une partie de ce que vérifie la CI : ruff (format + check), mypy, `vue-tsc`,
ESLint, et gitleaks pour les secrets. Aucun crochet ne modifie de fichier. Il
existe pour ne jamais envoyer de vert qui ne le sera pas en CI. On ne l'évite
pas avec `--no-verify`.

Il est **à installer soi-même** : `make hooks`. Rien n'écrit dans `.git/hooks`
à la place du développeur.

---

## 7. Pull Request

Chaque PR :

- **Petite** — reviewable en une séance : si la PR touche 5 fichiers de
  domaines différents, la fractionner.
- **Verte en CI** — *voir* §8.
- **Décrit le *quoi* et le *pourquoi*** dans le corps, pas seulement le *que*.
- **Écrit ses tests d'abord** — la régression est là *avant* le fix, pas après.
- **Rétablit le contrat** — schéma OpenAPI régénéré et `make openapi-check`
  vert si le schéma a bougé.
- **Documente ses décisions** — la branche `docs/…` et l'ADR sont dans la PR
  quand ce que la PR fait mérite d'être enregistré.

Checklist, à coller dans le template de PR :

```
- [ ] Tests écrits avant, et en vert
- [ ] make check vert (lint, types, client généré, tests)
- [ ] make test-integration vert quand le graphe l'exige
- [ ] make openapi-check vert (ou schéma non touché)
- [ ] ADR ajouté si la décision est structurante
- [ ] Changelog / version mis à jour (ou dérivé du commit)
- [ ] Comportement visible : exercé par un test E2E
```

---

## 8. CI

La CI (`.github/workflows/ci.yml`, sur chaque push vers `main` et chaque pull
request) rejoue les cibles du Makefile en quatre jobs — voir
[`ADR 0026`](../adr/0026-la-barriere-qualite.md) :

| Job | Contenu |
|---|---|
| `backend` | `make lint-check`, `typecheck-be`, `openapi-check`, `test` (couverture ≥ 90 %) |
| `frontend` | `make typecheck-fe`, `lint-fe`, `test-fe`, `npm run build` |
| `audit` | `make audit` : `bandit`, `pip-audit --skip-editable`, `npm audit --audit-level=high` |
| `integration` | `pytest tests/integration` contre des conteneurs de service Neo4j et PostgreSQL jetables |

> **Encore à poser** : rien n'*impose* encore une CI verte avant la merge. C'est
> une règle de protection de branche sur GitHub, à activer.

Deux garde-fous :

- Les tests d'intégration visent des **bases jetables, jamais le cluster
  partagé** : « vider entre les cas » ne peut pas toucher la modélisation de
  l'équipe.
- Dependabot (`.github/dependabot.yml`) ouvre chaque semaine les mises à jour
  des actions, de `uv`, de `npm` et des images compose, qui passent par la même
  CI.

---

## 9. Definition of done

Un changement est **fait** quand *tout* tient :

- Tests écrits d'abord, et en vert.
- `ruff`, `mypy --strict` et les scans de sécurité propres.
- Toute nouvelle contrainte de graphe ajoutée à `SCHEMA_STATEMENTS`
  (`backend/src/ea/db/schema.py`) et appliquée proprement *sur une base qui en
  avait déjà* — Neo4j n'a pas d'Alembic, une rename de valeur stockée est une
  migration *de données* en script versionné, pas une altération de schéma.
- Client OpenAPI régénéré si le schéma a bougé.
- Doc / ADR mis à jour.
- Tout comportement visible est exercé par un test E2E.

Si un point est en suspens, la PR n'est pas « presque prête » : elle n'est pas
prête.

---

## 10. État actuel de l'outillage

Honnêteté sur ce qui existe déjà — ce guide décrit le *parcours cible*, pas
toujours l'état du dépôt aujourd'hui :

| Élément | État | Où |
|---|---|---|
| Ramification + Conventional Commits | En place | Cette doc, `main` |
| `make check` (lint, types, client, tests) | En place | `Makefile` |
| TDD, suites unit/intégration/API | En place | `backend/tests/`, `frontend/tests/` |
| ADR pour les décisions structurantes | En place | `docs/adr/` |
| Plancher de couverture 90 % | En place | `fail_under = 90`, `backend/pyproject.toml` |
| Hook `pre-commit` | En place, à installer soi-même | `.pre-commit-config.yaml`, `make hooks` |
| Sécurité : `bandit`, `pip-audit`, `npm audit` | En place | `make audit`, job `audit` |
| ESLint (`make lint-fe`) | En place | `frontend/eslint.config.js` |
| Pipeline CI | En place | `.github/workflows/ci.yml` |
| Dependabot | En place | `.github/dependabot.yml` |
| SQLAlchemy / Alembic | En place | `backend/migrations/`, `element_documents`, `document_chunks` |
| CI verte obligatoire avant merge | **À poser** | Protection de branche GitHub |
| Tests E2E frontend (Playwright) | **À poser** | Non configuré |
| Auth | **À poser** | — |

---

## 11. Sécurité & secrets (rappels opérationnels)

Ces règles sont détaillées dans [`CLAUDE.md`](../../CLAUDE.md) ; on les répète ici
parce que c'est là qu'elles coûtent cher :

- La config n'arrive que par l'environnement (`pydantic-settings`). **Aucun
  secret, DSN ou clé dans le code ou les tests.** On commite `.env.example`,
  jamais `.env`. `Settings` refuse de s'instancier sans mot de passe Neo4j sauf
  en `EA_DEBUG`.
- L'authorisation est décidée *dans `services/`*, jamais seulement dans le routeur
  ni dans le SPA (le SPA masque l'UI, l'API tranche).
- Cypher et SQL suivent la même règle : toutes les valeurs runtime sont *liées*.
  Seuls trois emplacements Cypher construisent une chaîne — les trois partagent
  un type archi fermé (type de relation, bord d'un chemin variable) et chacun le
  justifie dans un commentaire. Un quatrième emplacement doit justifier de même.
- Les retours d'erreur à l'utilisateur sont typés et génériques ; les traces en
  pile vont dans les logs structurés (le `logging` de la bibliothèque standard, par `core/logging.py`, avec un *request id*),
  jamais dans le corps de réponse.
- On ne logge jamais de token, mot de passe ou PII — on masque au niveau du
  process de logging, pas à chaque appel.
- Frontend : aucun `dangerouslySetInnerHTML` non nettoyé ; les tokens vivent en
  mémoire ou en cookie httpOnly, jamais dans `localStorage`.

---

## 12. ADR & décisions structurantes

Quand une décision touche la structure — nouvelle dépendance, nouveau contexte
borné, changement d'authentification, changement de stockage, nouvelle entrée dans
une allowlist — on écrit une ADR dans `docs/adr/NNNN-titre.md`, **dans le même
commit que le code qu'elle motive**.

Format : `Date`, `Statut`, `Contexte`, `Décision`, `Conséquences`. On *supersède*
une ADR, on n'édite pas son historique — une décision annulée laisse une trace
(`Statut : Supersédé par NNNN`).

Si une décision de ce guide ou de [`CLAUDE.md`](../../CLAUDE.md) se révèle fausse,
on change la doc *dans le même commit* que le code, et on l'enregistre en ADR.

---

## 13. Release & changelog

- Le changelog et le bump de version sont **dérivés des Conventional Commits** :
  on n'écrit pas le changelog à la main, un `Bump` de `feat:` le produit.
- `main` reste reléasable à tout instant : on ne merge que du vert (lint, types,
  tests, scans, client généré).
- Une PR qui casse le contrat front/back sans régénérer le client est
  **incomplète**, pas juste mal notée : c'est une raison d'un `make openapi-check`
  rouge.
