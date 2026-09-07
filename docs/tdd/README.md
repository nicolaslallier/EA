# Tests d'abord (TDD)

Ce dossier cadre **la boucle TDD** comme mode de travail par défaut du projet —
la source de vérité est la section *TDD est le mode de travail par défaut* de
[`../../CLAUDE.md`](../../CLAUDE.md), dont ce fichier résume et complète le
rituel.

## La boucle

Pour tout changement — fonctionnalité, évolution, *bugfix* :

1. **Rouge** — écriture du test qui échoue, *pour la bonne raison*
   (pas une faute de frappe, pas un import manquant).
2. **Vert** — la plus petite implémentation qui passe.
3. **Refactor** — sans altérer le vert, on nettoie.

Un *bugfix* commence **toujours** par un test de régression qui le reproduit,
avant le correctif — sinon on ne prouve pas qu'il a été corrigé.

## Les trois niveaux de test

| Niveau | Dossier | Ce qui y vit | Coût |
|---|---|---|---|
| Unitaire | `backend/tests/unit/` | Règles de domaine, validation, fonctions pures. **Aucune I/O.** | Millisecondes |
| Intégration | `backend/tests/integration/` | Repositories et services **contre un vrai Neo4j**. Pas de pilote mocké, pas de double factice — ces tests *prouvent* le Cypher. | Lente, destructive |
| API | `backend/tests/e2e/` | `httpx.AsyncClient` contre l'app : auth, codes HTTP, enveloppes d'erreur. | Intermédiaire |
| Frontend | `frontend/tests/` | Vitest + Testing Library ; Playwright pour l'E2E (**pas encore configuré**). | — |

**Règle de placement :** un test qui a besoin d'une base n'est pas un test
unitaire — il va en `tests/integration/`. `domain/` n'importe ni FastAPI, ni
SQLAlchemy, ni `api/` ; les tests unitaires ne doivent pas non plus.

## Isolation des tests d'intégration

Neo4j Community ne sert qu'une seule base, donc l'isolation est **« vider le
graphe entre chaque cas »**, pas une transaction annulée. C'est destructeur, donc
limité par la variable `EA_ALLOW_DESTRUCTIVE_TESTS=1`, que
**seule** `make test-integration` positionne. Un `uv run pytest` nu **saute**
ces tests — il ne faut pas compter sur eux dans le green CI local.

## Bonnes pratiques déjà énoncées

- Les assertions portent sur le **comportement** via les entrées publiques, pas
  sur des attributs privés.
- Les fixtures **construisent des objets par leurs usines**
  (`polyfactory` / `factory_boy`) — ajouter un champ ne casse pas une centaine de
  tests.
- **Pas de `time.sleep`** : on injecte une horloge.
- Plancher de couverture : **90 %** sur `backend/src`. Une ligne couverte qui ne
  *prouve* rien est une échec, quel que soit le chiffre.

## À écrire ici au fil de l'eau

- `test-naming.md` : convention de nommage des tests (ex.
  `test_<sujet>_<scenario>_<résultat>`).
- `fixtures.md` : catalogue des usines partagées, avec exemples.
- `flaky-tests.md` : registre des tests instables et leur traitement.
- Un ADR si on introduit un **quatrième** niveau ou un runner de tests
  supplémentaire — `CLAUDE.md` l'exige.
