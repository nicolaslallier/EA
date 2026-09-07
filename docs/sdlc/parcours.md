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
   *contre un vrai Neo4j*. Jamais de driver mocké, jamais d'enregistrement
   factice — là, on prouve que le Cypher marche.
3. **API** (`backend/tests/e2e`) : `httpx.AsyncClient` contre l'application,
   couverture l'auth, les codes de statut, l'enveloppe d'erreur.

Un test qui a besoin d'une base de données n'est pas un test unit — on le déplace
en `tests/integration`.

```bash
make test-unit            # boucle rapide, sans base, millisecondes
make test                 # unit + API, toujours sans base
make test-fe              # Vitest du frontend, une passe
```

Les tests d'intégration effacent les `:Element` du **graphe partagé du cluster**
entre chaque cas (Neo4j Community ne sert qu'une seule base, il n'y a ni schéma
de test séparé ni transaction à annuler). Ils ne s'exécutent que si
`EA_ALLOW_DESTRUCTIVE_TESTS=1`, ce que seule la cible `make test-integration`
positionne ; un `pytest` nu les saute.

```bash
make test-integration     # NE PAS lancer pendant que quelqu'un modélise
```

> Le graphe est celui du cluster, **partagé entre toute l'équipe**. Ne lance pas
> `make test-integration` sans être sûr que personne n'est dessus : chaque cas
> efface tout le monde.

Règles qui tiennent ici :

- Toute correction de bug commence par un **test de régression** qui le
  reproduit.
- Les tests affirment le comportement *par ses entrées publiques*, pas par des
  attributs privés.
- Les fixtures construisent des objets via une fabrique : ajouter un champ ne
  casse pas cent tests.
- Jamais de `time.sleep` — on injecte une horloge.
- Le plancher de couverture est de **90 %** sur `backend/src`, mais *une ligne
  couverte qui ne prouve rien est un échec*, quel que soit le nombre.

---

## 4. Contrôles locaux

Avant de préparer un commit, on tourne le tout ce que la CI vérifiera
(`# Tout ce que la CI vérifiera` est littéralement le commentaire sur la cible
`check` du Makefile) :

```bash
make check                # lint + types + client généré + tests sans base (BE + FE)
```

Détaillé, pour cibler :

| Commande | Effet |
|---|---|
| `make lint` | `ruff format .` puis `ruff check --fix .` |
| `make typecheck` | `mypy --strict` sur `backend/src`, puis `vue-tsc` sur le frontend |
| `make openapi-check` | Échoue si `openapi.json` / `src/api/` ne sont plus en phase |
| `make test` / `make test-fe` | Les suites sans base |
| `make test-integration` | La suite contre le vrai graphe (destructive, voir §3) |

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

Le hook `pre-commit` fait tourner, *localement*, la même chose que la CI : ruff
(format + check), mypy, et la détection de secrets. Il existe pour ne jamais
envoyer de vert qui ne le sera pas en CI. On ne l'évite pas avec `--no-verify`.

> **Encore à poser** (voir §10) : le fichier `.pre-commit-config.yaml` n'existe
> pas encore. En attendant, `make check` est le substitut équivalent à lancer à
> la main avant chaque commit.

---

## 7. Pull Request

Chaque PR :

- **Petite** — reviewable en une séance : si la PR touche 5 fichiers de
  domaines différents, la fractionner.
- **Verte en CI** — *voir* §8 ; tant que la CI n'existe pas, `make check` local
  en fait office.
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

La CI vérifie, à la barre, *exactement* `make check` plus la couverture et les
scans de sécurité :

| Étape | Commande | Bloque la merge si rouge |
|---|---|---|
| Lint | `make lint` | Oui |
| Types | `make typecheck` | Oui |
| Tests sans base | `make test` | Oui |
| Couverture | `pytest --cov=ea --cov-fail-under=90` | Oui, sous 90 % |
| Tests d'intégration | `make test-integration` (sur un graphe dédié) | Oui |
| Client généré en phase | `make openapi-check` | Oui |
| Sécurité Python | `uv run bandit -c pyproject.toml -r src` | Oui |
| Dépendances | `uv run pip-audit` | Oui |
| Sécurité frontend | `npm audit --audit-level=high` | Oui |

> **Encore à poser** (voir §10) : le pipeline lui-même (`.github/workflows/`) et
> les outils de sécurité ne sont pas encore en place. `make check` est le
> contrat : la CI est, par définition, l'automatisation de `make check` plus les
> trois scans.

Deux garde-fous sur l'intégration en CI :

- Le graphe d'intégration y est un **graphe dédié, pas celui du cluster partagé** :
  en CI, « vider entre les cas » ne peut pas toucher la modélisation de l'équipe.
- Les scans de sécurité bloquent la merge ; Dependabot/Renovate maintient les
  lockfiles à jour pour que `pip-audit` / `npm audit` restent stables.

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
| Plancher de couverture 90 % | En place (attendu) | `pytest --cov-fail-under=90` |
| Hook `pre-commit` | **À poser** | `.pre-commit-config.yaml` absent |
| Sécurité : `bandit`, `pip-audit`, `npm audit` | **À poser** | Absents |
| ESLint (`make lint-fe`) | **À poser** | Aucun config |
| Tests E2E frontend (Playwright) | **À poser** | Non configuré |
| Pipeline CI (`.github/workflows/`) | **À poser** | Absent |
| Auth / SQLAlchemy / Alembic | **À poser** | Aucune table Postgres |

Pendant que ces cases sont vides, les lignes « À poser » ci-dessus ont un
substitut à la main : `make check` pour les contrôles, la détection de secrets
*à l'œil* pour les secrets (et on ne committe jamais `.env`, seulement
`*.example`).

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
  pile vont dans les logs structurés JSON (`structlog`, avec un *request id*),
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
