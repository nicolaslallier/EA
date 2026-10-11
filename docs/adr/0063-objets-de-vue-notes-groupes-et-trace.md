---
titre: Objets de vue — notes, groupes, tracés de liens — et concurrence optimiste des diagrammes
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/diagram_objects.py, backend/src/ea/domain/diagrams.py, backend/src/ea/db/models/diagram.py, backend/migrations/versions/0015_diagram_scene.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/services/diagrams.py, backend/src/ea/api/diagrams.py, backend/src/ea/api/schemas.py, backend/src/ea/mcp/server.py, frontend/src/features/diagrams/
---

# 63. Objets de vue : notes, groupes, tracés — et révision

Date : 2026-10-11
Statut : Proposition

## Contexte

[`0031`](0031-diagrammes-enregistres.md) a posé qu'un diagramme est une *vue* : il retient quels
éléments sont dessinés et où, et ne possède aucun fait. Un schéma d'architecte dit plus que des boîtes :
une note qui explique un choix, un cadre « Domaine Paiement » autour de trois applications, un lien qui
contourne un obstacle. Rien de cela n'est un élément ni une relation. En parallèle, `0031` laissait
une dette en « À surveiller » : l'enregistrement complet de la disposition à chaque geste est sans
conflit pour un seul auteur, mais deux personnes se remplacent l'une l'autre en silence.

## Décision

**Notes et groupes sont des objets de vue dans une table polymorphe ; un groupe n'est pas un élément du
graphe et ne crée jamais de relation ; un lien dont le tracé change garde sa relation et ne retient que
ses coudes ; chaque enregistrement est comparé à une révision.**

| Point | Choix |
|---|---|
| Objets de vue | Une table `diagram_objects` (migration `0015`), `kind` ∈ `note`/`group` par contrainte `CHECK` ; même géométrie, même empilement `z`, même imbrication `parent_id` (clé étrangère `SET NULL`) |
| Une table, pas deux | Les deux genres ont la même lecture, le même remplacement transactionnel et la même détection de cycle ; seul le sens de `text` change (titre d'un groupe, corps d'une note) |
| Groupe ≠ élément | Aucune ligne dans `elements`, aucune relation. Supprimer un groupe remonte ses enfants au parent du groupe et ne touche aucun élément. La composition se tire à la main (`POST /relationships`, métamodèle consulté) ; « Relier le contenu… » ouvre le formulaire de lien existant pour une paire choisie, une relation à la fois |
| Boîte dans un groupe | `diagram_nodes.parent_object_id` (`SET NULL`) et `z` ; profondeur ≤ `MAX_GROUP_DEPTH = 5`, refusée par `check_scene` (cycle, parent qui n'est pas un groupe, sixième niveau) |
| Ancre d'une note | `anchor_element_id` vers `elements` (`SET NULL`), trait pointillé, jamais une relation ; contrainte `CHECK` : seul une note est ancrée |
| Tracé d'un lien | `diagram_edges(diagram_id, relationship_id, waypoints jsonb)`, au plus 20 coudes ; pas de ligne = segment droit rogné aux bords. Une relation supprimée emporte son tracé (cascade) |
| Concurrence | `diagrams.revision` ; `PUT /diagrams/{id}/layout` reçoit la révision lue, compare sous `SELECT … FOR UPDATE`, répond `409 diagram_revision_conflict` si elle diffère, sinon l'incrémente dans la même transaction et la renvoie dans `X-Diagram-Revision` |
| Corps du `PUT` | `{revision, nodes, objects, edges}`, tous requis, listes éventuellement vides : un champ optionnel dédoublerait le schéma en `-Input`/`-Output` (`0035`) |
| Conflit côté SPA | Rien n'est rejoué seul. Le SPA propose de recharger la version de l'autre, puis de recoller sa propre scène sur la nouvelle révision, ou d'abandonner. L'historique d'annulation est vidé |
| Lecture MCP | `list_diagrams` et `get_diagram`, lecture seule. Aucun outil n'écrit une coordonnée : la géométrie n'est pas le travail d'un agent |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Deux tables, `diagram_notes` et `diagram_groups` | Schéma « pur » | Duplique la lecture, le remplacement transactionnel et la détection de cycles | Écarté |
| Un groupe est un élément ArchiMate `grouping` | Une seule notion | Un `grouping` est un fait du graphe : il porte des relations, apparaît dans le voisinage et l'impact ; un cadre de dessin ne doit pas | Écarté |
| Créer une composition automatique pour chaque boîte glissée dans un groupe | « Ça se fait tout seul » | Écrit des faits que personne n'a décidés, et pour une paire que le métamodèle peut refuser | Écarté |
| Un verrou d'édition par diagramme | Aucun conflit | Verrou oublié, session morte ; l'autosave rend le verrou invisible | Écarté |
| Fusion automatique des deux scènes | Pas de perte | Une fusion de géométrie est illisible à relire ; le cas est rare (un rédacteur à la fois) | Écarté |
| Un lien routé stocké sur la relation | Pas de table de plus | Le tracé est de la vue : deux diagrammes montrent le même lien différemment | Écarté |

## Conséquences

- **Retirer une boîte du diagramme élague** les tracés et détache les notes ancrées sur son élément
  (`useDiagram.prune`) ; sinon le serveur répondrait 422 pour un tracé de lien dont une extrémité a quitté la vue.
- **Supprimer un élément du catalogue** emporte sa boîte (clé étrangère), laisse le groupe et ses autres
  enfants, et détache une note ancrée sur lui.
- **Le `PUT` est un changement de contrat** : un onglet resté ouvert sur l'ancien SPA pendant un
  déploiement voit son enregistrement refusé (422) jusqu'au rechargement.
- **Le 409 ne transporte pas la révision courante dans un champ** : l'enveloppe d'erreur n'a que `error`
  et `detail`, qui la nomme ; le SPA relit le diagramme.
- **Journal** : un seul événement d'audit `diagram.layout_replaced` par enregistrement (un autosave
  n'écrit pas un événement par boîte), avec des comptes - révision, boîtes, objets, liens, boîtes
  ajoutées, retirées, déplacées - jamais une coordonnée ni le texte d'une note. Il est écrit par le dépôt
  dans la transaction du `PUT`, sous le verrou de la ligne du diagramme.
- **Restaurer une baseline** qui rétablit des boîtes fait avancer la révision des diagrammes touchés : un
  éditeur resté ouvert sur l'ancienne scène reçoit un 409 au lieu d'écraser la restauration. Les notes,
  groupes et tracés ne sont pas capturés par les baselines (comme les documents) ; une boîte rétablie
  perd son groupe.
- **Chaque enregistrement présente la révision sur laquelle ses modifications ont été faites** : le SPA lit
  le jeton en demandant l'enregistrement, avec le contenu, et n'hérite que de la réponse à ses propres
  enregistrements - jamais d'une relecture arrivée entre-temps (elle ferait écraser la version d'un collègue).
- Le texte d'une note est du texte : le SPA ne l'insère jamais comme HTML, l'export SVG l'échappe.
- Complète [`0031`](0031-diagrammes-enregistres.md) sans le supplanter ; solde sa dette « deux auteurs ».

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0031](0031-diagrammes-enregistres.md),
  [0033](0033-postgresql-seul-pour-le-graphe.md), [0035](0035-taille-des-boites-de-diagramme.md)
- Code concerné : `backend/src/ea/domain/diagram_objects.py`, `backend/src/ea/services/diagrams.py`,
  `backend/src/ea/repositories/diagram_store.py`, `backend/migrations/versions/0015_diagram_scene.py`,
  `frontend/src/lib/sceneObjects.ts`, `frontend/src/lib/waypoints.ts`
