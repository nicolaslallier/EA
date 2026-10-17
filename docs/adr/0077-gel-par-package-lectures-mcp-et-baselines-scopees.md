---
titre: Le gel se lit sur chaque élément et diagramme, les baselines scopées se comparent et se restaurent, trois outils MCP
date: 2026-10-17
statut: Proposition
affects: backend/src/ea/domain/package_versions.py, backend/src/ea/domain/approval.py, backend/src/ea/domain/baselines.py, backend/src/ea/domain/ports.py, backend/src/ea/repositories/baseline_store.py, backend/src/ea/repositories/diagram_revision.py, backend/src/ea/services/baselines.py, backend/src/ea/services/packages.py, backend/src/ea/api/schemas.py, backend/src/ea/api/architecture.py, backend/src/ea/api/diagrams.py, backend/src/ea/api/baselines.py, backend/src/ea/mcp/server.py, backend/src/ea/main.py, backend/src/ea/baseline_capture.py
---

# 77. Le gel se lit sur chaque élément et diagramme, les baselines scopées se comparent et se restaurent, trois outils MCP

Date : 2026-10-17
Statut : Proposition

Complète [`0074`](0074-gel-par-package-et-triggers.md) (plan 2). Remplace en partie `0074` sur sa conséquence
« Les baselines scopées ne se comparent ni ne se restaurent » ; le reste de `0074` tient.

## Contexte

`0074` a gelé un sous-arbre publié par la base, mais ne l'a rendu lisible qu'au niveau des packages, et a laissé
les baselines scopées inertes : restaurer l'une comme un modèle entier aurait supprimé tout ce qui est hors du
périmètre. Un lecteur (SPA, agent) ne pouvait donc pas savoir, avant d'écrire, qu'un élément était gelé, et un
sous-arbre publié ne pouvait pas être ramené à sa baseline. Les actions de publication n'existaient qu'en HTTP.

## Décision

**Le gel se lit, il ne se calcule pas par ligne.** Chaque `ElementRead` et `DiagramRead` porte `frozen_by`. La
carte `Freezes` (les publications en vigueur, déjà héritées par les descendants) est lue **une fois par requête**
par `PackageService.freezes()` et passée en argument **obligatoire**, sans défaut, à `of(...)`. Pas de jointure
SQL par ligne : ce serait une CTE récursive par élément sur une page de 500. Pas de défaut : une porte oubliée
dirait « non gelé » sans que rien n'échoue. `allowed_transitions` est vide sous un gel (`domain/approval.py`), pour
que le SPA ne dessine aucun bouton que l'API refuserait.

**Une baseline scopée se compare dans son périmètre.** `restricted_to(current, packages=, diagrams=, known=)`
(`domain/baselines.py`) donne la partie du modèle actuel qu'on lui oppose : les éléments rangés dans le sous-arbre
actuel **ou** connus de la baseline (un élément sorti depuis est modifié, pas supprimé), les liens dont les deux
bouts sont gardés, les boîtes des diagrammes du sous-arbre. Deux baselines ne se comparent que si elles ont le
même périmètre (`BaselineScopedError`, 409 `baseline_scoped`). Restreindre l'autre baseline au périmètre a été
écarté (Q1) : cela comparerait deux copies qui ne disent pas la même chose.

**Une baseline scopée se restaure dans son sous-arbre, jamais au-delà.** Écart avec le plan : la restauration est
planifiée sur le **modèle entier** avec `plan_restore(deletable=…)` : seuls les éléments rangés dans le sous-arbre
aujourd'hui peuvent être supprimés, et les boîtes ne s'écrivent que sur les diagrammes du sous-arbre.
`restricted_to` ne sert qu'au diff. Raison : `restricted_to` écarte les liens qui franchissent la frontière ; supprimer
un élément intérieur en cascaderait pourtant un, et aucun élément approuvé ou verrouillé à l'extérieur ne
bloquerait le plan (ADR 0049).

**Restaurer un sous-arbre gelé est prédit, puis refusé avant d'écrire.** Le dry run renvoie `RestorePlan.frozen_by`
et le plan est bloqué (`blocked`), comme pour les éléments protégés ; l'appliquer est un `409 package_frozen`
avant toute écriture. Les triggers restent l'autorité pour la course entre la lecture et l'écriture.
Écart avec le plan : la prédiction couvre **tout package que le plan écrit**, pas seulement le périmètre
(`RestorePlan.packages_written` : package actuel et cible des éléments modifiés, recréés ou supprimés, les deux
bouts des liens écrits ou cascadés, les packages des diagrammes écrits), parce qu'une prédiction partielle se lirait
comme complète. Elle est comparée à la carte entière via `FreezesLookup` (la carte, pas le `FreezeLookup` par
identifiant du plan), injecté et **obligatoire** dans `BaselineService`. `freeze_of` retient d'abord la publication du
périmètre lui-même, sinon la première par (chemin, id). Un package disparu depuis la capture ne gèle rien.

**Trois outils MCP**, sans dépôt, sur `PackageService` : `list_package_versions`, `publish_package` (dry run tant
qu'on ne dit pas le contraire) et `reopen_package`, bornes importées de `api/schemas.py`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Matérialiser `frozen_by` sur `elements` | Lecture gratuite | Une seconde vérité, déjà écartée par `0074` | Écartée |
| Jointure SQL par ligne | Pas d'argument à passer | Une CTE récursive par élément, 500 par page | Écartée |
| Restreindre l'autre baseline au périmètre (Q1) | Compare toujours | Compare deux copies différentes | Écartée |
| Seuls les triggers refusent (ancien défaut de Q3) | Moins de code | L'utilisateur lit un plan applicable qui échoue | Écartée |
| Prédire aussi pour une baseline globale | Uniforme | Partiel donc trompeur (voir limites) | Écartée |
| Planifier le sous-arbre sur `restricted_to` | Un seul modèle réduit | Cascade de liens non vue (voir plus haut) | Écartée |

## Conséquences

- La carte et l'élément sont lus dans deux transactions : un badge peut retarder d'une publication, jamais le refus.
- Un refus prédit n'écrit pas d'événement `package.write_refused_frozen` (rien n'a été tenté en base), seulement le
  log `restore_blocked_frozen`.
- Limites connues, reportées : un élément recréé dont (type, nom), l'adresse ou le préfixe est maintenant tenu par un
  élément non protégé à l'extérieur n'est pas prédit (la contrainte d'unicité refuse à l'application, comme avec
  `keep_new=True` avant) ; une baseline du modèle entier qui touche un sous-arbre gelé n'est pas prédite (les
  triggers refusent), décision Q3.
- Les tests d'intégration n'ont pas pu tourner en local (pas de Docker) : la CI les exécute pour la première fois.
- Décisions Q1 à Q5 du 2026-10-17 : Q1 comparer deux baselines au même périmètre seulement ; Q3 prédire pour le
  périmètre, pas pour une baseline globale ; les autres sont les choix ci-dessus.

## Références

- ADR liés : [0047](0047-baselines-en-copie-normalisee.md), [0049](0049-protected-ids-remplace-le-point-de-0047.md), [0074](0074-gel-par-package-et-triggers.md)
- Code concerné : `backend/src/ea/domain/baselines.py`, `backend/src/ea/services/baselines.py`,
  `backend/src/ea/repositories/baseline_store.py`, `backend/src/ea/mcp/server.py`
