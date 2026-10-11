---
titre: Plateaux, écart entre deux plateaux et feuille de route — calculés, bornés, sans table
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/timeline.py, backend/src/ea/services/timeline.py, backend/src/ea/api/timeline.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/mcp/server.py, frontend/src/features/roadmap, frontend/src/lib/roadmapLayout.ts
---

# 61. Plateaux, écart entre deux plateaux et feuille de route — calculés, bornés, sans table

Date : 2026-10-11
Statut : Proposition

## Contexte

Avec le temps de validité (ADR 0059) et `as_of` (ADR 0060), il reste à modéliser l'évolution : des états As-Is et
To-Be, ce qui change de l'un à l'autre, et une frise qui montre quand. Le métamodèle porte déjà `plateau`,
`work_package`, `deliverable` et `gap` ; *As-Is* et *To-Be* ne sont pas des types, ce sont deux plateaux pris à des
dates différentes.

## Décision

- **Rien n'est stocké.** Un plateau contient ce qu'il **compose ou agrège** (`composition`, `aggregation` partant de
  lui), calculé à chaque lecture, comme l'appartenance à un sous-réseau (ADR 0020). Une proposition automatique
  « les éléments actifs à la date du plateau » reste une suggestion, jamais un calcul d'office : le choix du
  contenu d'un plateau est celui de l'architecte.
- **Un écart est un élément `gap` ordinaire**, relié par `association` (source le `gap`, cible chacun des deux
  plateaux). `POST /plateaus/gap` le crée ; aucune table d'écarts. Le deuxième écart pour le même couple, dans
  l'un ou l'autre ordre, est un `409 duplicate_gap`.
- **Une écriture, une transaction.** Le `gap` et ses deux associations sont écrits par `ArchitectureService`
  (`create_element`, `connect` ×2) dans la transaction partagée de l'ADR 0052 : chaque règle ArchiMate, de revue
  (`guard_element_write` sur chaque plateau lié : un plateau verrouillé refuse), d'audit (trois événements) et
  d'IPAM s'applique sans être recopiée, et un échec au milieu ne laisse rien. Le contrôle d'unicité du couple
  est **sérialisé** par un verrou consultatif de transaction sur la paire non ordonnée : deux éditeurs simultanés
  obtiennent un écart et un 409, jamais deux écarts.
- **Chaque lecture est bornée avant d'être faite.** Une fenêtre de feuille de route de plus de 20 ans (en jours :
  `MAX_ROADMAP_DAYS = 7 320`) ou qui finit avant de commencer est refusée **avant la première requête**
  (`422 invalid_timeline`). Chaque ensemble est lu en SQL avec un `LIMIT` d'**un de plus** que son plafond
  (500 plateaux, 5 000 membres par plateau, 1 000 éléments de frise, 5 000 liens) et refusé s'il le dépasse
  (`422 scope_too_large`), jamais tronqué : une frise partielle se lirait comme complète. Une borne déduite des
  données (pas demandée) est rognée à 20 ans, pas refusée.
- **Le calcul est hors de la boucle d'événements.** La comparaison et la construction de la frise tournent dans un
  thread (`off_the_loop`), sous une place de l'admission des analyses (`Admission`, globale et par appelant,
  `lease.hold` jusqu'à la fin réelle du thread) : plus de places que le plafond, `429 analysis_busy`.
- **Aucune valeur de l'utilisateur dans une erreur ni un journal.** Les refus nomment des clés et des plafonds,
  jamais un nom d'élément ni une valeur de date reçue.
- **Lectures `require_caller()`, `POST /plateaus/gap` `require_editor()`**, en tête de méthode (garde AST).
- **MCP** : `list_plateaus` (lecture) et `gap_between(a, b, create_gap=False)`. Le protocole n'a qu'un jeu
  d'annotations par outil : `gap_between` porte celle d'une écriture, et sa description dit qu'il ne lit que tant
  que `create_gap` est faux. La frise n'a pas d'outil : un agent lit les dates des éléments avec `list_elements`.
- **SPA** : section « Feuille de route » (`/analyse/feuille-de-route`) — frise SVG dessinée à la main par un module pur
  (`lib/roadmapLayout.ts`), panneau « Comparer deux plateaux » (`?a=`, `?b=`), « Créer l'écart » caché à un lecteur
  (l'API décide). Aucune règle de métamodèle ni de calcul d'écart en TypeScript.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Écart calculé, `gap` ordinaire | Aucune table ; l'écart est dans le catalogue, le voisinage, l'impact | Le contenu d'un plateau est un choix à saisir | Retenu |
| Table `plateau_gaps` | Contrainte d'unicité native | Un second modèle de l'écart, une cascade, un dépôt | Écarté |
| Compensation (supprimer le `gap` si une association échoue) | Pas de transaction partagée | Fenêtre où un `gap` orphelin est visible et audité, deux événements de trop | Écarté |
| Plateau défini par ses dates | Automatique | Faux : ce qui est « dans » le To-Be est un choix | Écarté |
| Tronquer à N éléments | Réponse toujours obtenue | Frise ou écart faux sans le dire | Écarté |

## Conséquences

- Le verrou consultatif ne protège que les écritures de `create_gap` ; un `gap` créé à la main par
  `POST /elements` puis associé n'est pas empêché de doubler un écart (c'est un élément ordinaire).
- Le contenu d'un plateau est borné à 5 000 éléments pour être comparé : au-delà, `scope_too_large`.
- L'admission est en mémoire, par processus (comme celle des analyses) : plusieurs workers multiplient les plafonds.
- La frise ne montre que plateaux, work packages et livrables ; les liens `triggering` et `realization` entre eux ;
  la composition est rendue par `plateau_ids`.

## Références

- ADR liés : [0059](0059-temps-de-validite-dans-les-proprietes.md), [0060](0060-le-parametre-as-of-des-lectures.md),
  [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0052](0052-session-partagee-entre-cas-d-usage.md),
  [0057](0057-controles-de-qualite-du-modele.md), [0048](0048-approbation-et-verrouillage-des-elements.md)
- Code concerné : `backend/src/ea/services/timeline.py`, `backend/src/ea/domain/timeline.py`,
  `backend/src/ea/api/timeline.py`
