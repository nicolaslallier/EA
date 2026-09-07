# Cycle de vie (SDLC)

Ce dossier cadre le **cycle de vie du développement** du projet EA : comment une
brique passe de l'idée au merge, et ce qui fait qu'elle est « réellement faite ».

Il ne réécrit pas la charte d'ingénierie — la source de vérité est
[`../README.md`](../README.md) puis [`../../CLAUDE.md`](../../CLAUDE.md), dont
ce fichier résume la section *SDLC*.

## Où trouver quoi

| Sujet | Référence |
|---|---|
| Branches, Conventional Commits, `pre-commit`, définition de « fait » | [`../../CLAUDE.md`](../../CLAUDE.md), section *SDLC* |
| Orchestration locale (Makefile) | [adr/0001](../adr/0001-orchestration-locale-via-makefile.md) |
| Boucle TDD (tests d'abord, à trois niveaux) | [`../tdd/README.md`](../tdd/README.md) |
| Cadre TOGAF 10 (le process d'architecture piloté) | [`../togaf/README.md`](../togaf/README.md) |

## Ce qui est cadré et ce qui reste à brancher

**Cadré dans `CLAUDE.md` :** branches depuis `main` (`feat/`, `fix/`, `chore/`,
`docs/`), Conventional Commits, `pre-commit` (ruff, mypy, détection de secrets),
PR avec la description *quoi / pourquoi*.

**Décidé mais pas encore mis en place** (ne pas supposer que cela existe) :

- `pre-commit` (hook + configuration `.pre-commit-config.yaml`) — à créer.
- CI (lint, types, tests, porteur de couverture, scans de sécurité, contrôle du
  client généré).
- `bandit`, `pip-audit`, `npm audit --audit-level=high` en CI.
- Changelog et *version bump* dérivés des Conventional Commits.
- `Renovate` / `Dependabot` sur les lockfiles.

Le jour où l'une de ces briques arrive, un ADR est ouvert dans
[`../adr/`](../adr/) et `CLAUDE.md` est mis à jour **dans le même commit**.

## À écrire ici au fil de l'eau

- `template-pr.md` : gabarit de description de PR (quoi, pourquoi, ADR associé,
  tests ajoutés, client régénéré ?).
- `release.md` : comment un tag naît des Conventional Commits.
- Une vue sur `branching` si le schéma change de `main` unique.

Un ADR n'ouvre ce dossier qu'une fois la décision stabilisée ; en attente, les
notes vivent dans l'ADR concerné, pas ici.
