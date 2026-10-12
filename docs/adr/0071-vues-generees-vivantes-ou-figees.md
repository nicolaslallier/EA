---
titre: Vues générées — un diagramme qui est « tout ce qui répond à cette requête », vivant ou figé
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/generated_views.py, backend/src/ea/domain/diagrams.py, backend/migrations/versions/0017_generated_views.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/services/diagrams.py, backend/src/ea/services/view_sources.py, backend/src/ea/api/diagrams.py, backend/src/ea/mcp/server.py, frontend/src/lib/autoLayout.ts, frontend/src/features/diagrams/
---

# 65. Vues générées : vivante ou figée

Date : 2026-10-11
Statut : Proposition

## Contexte

Un architecte veut souvent un périmètre — « toutes les applications critiques », « le paquet Paiement et ses
sous-paquets » — sans le dessiner boîte par boîte. Les paquets et les requêtes enregistrées
([`0042`](0042-les-paquets-classement-en-liste-d-adjacence.md), [`0043`](0043-le-filtre-d-elements-objet-de-domaine-pur.md))
savent déjà dire quels éléments répondent. Reste la question du diagramme : est-il un instantané, ou
suit-il la requête ? Un instantané périme ; un suivi interdit de ranger à la main. Et le serveur n'a, par
construction, aucune coordonnée calculée ([`0035`](0035-taille-des-boites-de-diagramme.md) : la géométrie
est du dessin).

## Décision

**Une vue générée est vivante par défaut — aucune boîte stockée, le contenu recalculé à chaque
ouverture, la mise en page calculée par le SPA — et se fige à la demande en un diagramme ordinaire qui
garde la référence à sa source.**

| Point | Choix |
|---|---|
| Source | `diagrams.source_query_id` ou `source_package_id` (+ `source_recursive`), clés étrangères `RESTRICT` : supprimer une requête ou un paquet lu par une vue est un 409 `source_in_use` (la base le refuse, le dépôt traduit la contrainte par son nom) |
| Contraintes | au plus une source ; `generation` est non nul **si et seulement si** une source existe ; `generation` ∈ `live`, `frozen`. Migration `0017` : cinq colonnes nullables ou à défaut, trois `CHECK` que toute ligne existante satisfait — elle ne peut pas échouer sur les données existantes |
| Vivante | `GET /diagrams/{id}` renvoie `nodes = []` et le graphe de la source ; lecture seule (`PUT /layout` → 409 `diagram_is_live`, jugé sous le verrou de la ligne) ; mise en page de `lib/autoLayout.ts`, pure et déterministe, donc stable tant que le contenu l'est |
| Borne avant lecture | La source est lue par `LIMIT 500` et un `COUNT` en SQL, jamais en entier : au-delà de 500 éléments, 422 `generated_view_too_large` avec le décompte réel — on réduit le filtre. La mise en page est faite par le navigateur, sur au plus 500 boîtes |
| Figer | `POST …/freeze` reçoit la mise en page que le SPA vient de calculer **et la révision lue**, la stocke en une transaction (verrou, comparaison de révision, état `live` exigé), passe à `frozen`, garde la source. Le diagramme s'édite alors comme un autre (notes, groupes, coudes, révision). Les boîtes doivent appartenir à la source |
| Rafraîchir | `GET …/source-diff` calcule `{added, removed}` sans rien écrire ; le SPA place les ajoutés sous les survivants (`placeAdded`, aucun survivant ne bouge), retire les disparus **seulement si l'utilisateur le demande**, puis enregistre par le `PUT` habituel |
| Dégeler | `POST …/unfreeze` (révision lue exigée) supprime les boîtes et revient à vivante ; refusé (409 `has_annotations`) s'il existe notes, groupes ou tracés, sauf `discard_annotations=true` après confirmation explicite |
| Filtre | Celui des requêtes enregistrées (`ElementQuery` → `ElementFilter`), jamais recopié. Un seul adaptateur (`services/view_sources.py`) connaît ses noms ; le reste dépend du port `ViewSourceResolver` |
| Journal | `diagram.generated`, `diagram.frozen`, `diagram.unfrozen` : des identifiants et des comptes (la source est un identifiant, jamais un nom) |
| Auto-layout | Rangées par couche (motivation et stratégie, métier, application, technologie avec le physique, migration), tri par barycentre sur deux balayages, ex æquo par nom puis identifiant, rangées repliées à 1 400 unités |
| Agents | `create_generated_diagram(query_id | package_id)` : « pour dessiner un périmètre, génère une vue plutôt que de poser des boîtes » |
| « Figée d'emblée » | `generate` puis `freeze` : le serveur ne calcule aucune coordonnée, donc ne peut pas créer une vue figée de lui-même |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Toujours un instantané | Simple, éditable | Périme au premier élément créé ; l'utilisateur ne sait pas que c'est périmé | Écarté |
| Toujours vivante | Toujours juste | Interdit de ranger, d'annoter, de grouper | Écarté |
| Boîtes stockées et resynchronisées en tâche de fond | Éditable et à jour | Une resynchronisation écrit à l'insu de l'utilisateur et déplace ce qu'il a rangé | Écarté |
| Calculer la mise en page côté serveur | Une vue figée d'emblée | La géométrie n'est pas un fait du serveur ; un calcul de plus sur la boucle d'événements, à borner et à admettre | Écarté |
| `ON DELETE SET NULL` sur la source | Supprimer sans friction | La vue changerait de sens en silence, ou se viderait | Écarté |
| Auto-layout par force | Joli sur un graphe quelconque | Non déterministe ; une vue vivante se brasserait à chaque ouverture | Écarté |

## Conséquences

- **Une requête qui change fait changer une vue vivante sous les yeux de qui l'ouvre** : c'est le sens du mot ;
  une vue figée, elle, ne bouge jamais seule — « Rafraîchir » montre l'écart.
- **Le plafond est une garde de lisibilité** : une source de plus de 500 éléments se découpe en plusieurs
  vues. Une vue vivante dont la source dépasse le plafond *après coup* ne s'ouvre plus (422) jusqu'à ce
  qu'on réduise le filtre.
- **Un 409 nomme sa cause, pas les diagrammes** : refuser la suppression d'une requête ou d'un paquet lu par
  une vue ne cite aucun nom de diagramme (un contenu d'utilisateur n'a rien à faire dans un message d'erreur) ;
  `GET /diagrams` indique quelles vues sont générées.
- **Un déploiement sans le relationnel** répond 503 `generated_views_unavailable` aux routes de
  génération ; le reste des diagrammes n'est pas touché.
- **Les baselines et les échanges** ne portent ni la source ni l'état d'une vue : une vue vivante y a zéro
  boîte, une vue figée ses boîtes comme un diagramme dessiné.
- **Un onglet resté ouvert sur une vue qu'un collègue vient de dégeler ou de figer** reçoit un 409 de
  révision et doit se recharger.

## Références

- ADR liés : [0031](0031-diagrammes-enregistres.md), [0035](0035-taille-des-boites-de-diagramme.md),
  [0069](0069-objets-de-vue-notes-groupes-et-trace.md)
- Code concerné : `backend/src/ea/domain/generated_views.py`, `backend/src/ea/services/diagrams.py`,
  `backend/src/ea/services/view_sources.py`, `frontend/src/lib/autoLayout.ts`
