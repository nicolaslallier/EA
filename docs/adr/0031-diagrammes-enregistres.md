---
titre: Les diagrammes enregistrés — des vues ArchiMate stockées dans PostgreSQL
date: 2026-09-13
statut: Accepté
affects: backend/src/ea/domain/diagrams.py, backend/src/ea/db/models/diagram.py, backend/migrations/versions/0004_diagrams.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/repositories/archimate_graph.py, backend/src/ea/services/diagrams.py, backend/src/ea/services/architecture.py, backend/src/ea/api/diagrams.py, backend/src/ea/api/schemas.py, backend/src/ea/main.py
---

# 31. Les diagrammes enregistrés

Date : 2026-09-13
Statut : Accepté

## Contexte

Le SPA sait déjà *dessiner* le graphe : le voisinage d'un élément
([`0010`](0010-dessiner-le-voisinage-d-un-element.md)) et l'analyse d'impact
([`0013`](0013-ecran-d-analyse-d-impact.md)). Mais ces dessins sont calculés :
ce sont des anneaux autour d'un élément, que personne ne choisit ni ne range.
Ce qui manque est l'autre geste d'un architecte — composer un schéma : prendre
les éléments dans le catalogue, les poser sur une toile, tirer des liens entre
eux, et retrouver ce schéma le lendemain.

Deux questions font la décision. **Qu'est-ce qu'un diagramme possède ?** Si un
diagramme porte ses propres boîtes et ses propres flèches, il devient un second
modèle, libre de contredire le graphe. Et **où le ranger**, alors que tout ce
qu'il montre vit dans Neo4j ?

## Décision

**Un diagramme est une vue ArchiMate : il ne possède aucun fait. Il retient
quels éléments sont dessinés et où, dans PostgreSQL.**

| Point | Choix |
|---|---|
| Ce qu'il retient | Un nom, une description, et pour chaque boîte `(element_id, x, y)` |
| Emplacement | PostgreSQL, tables `diagrams` et `diagram_nodes` — migration `0004` |
| Lien vers l'élément | `diagram_nodes.element_id`, **sans clé étrangère**, indexé |
| Lien vers le diagramme | `diagram_nodes.diagram_id`, clé étrangère `ON DELETE CASCADE` |
| Unicité | Le nom du diagramme ; un élément au plus une fois par diagramme (clé primaire) |
| Relations dessinées | Les vraies relations du graphe, créées par `POST /relationships` |
| Dessin | SVG écrit à la main, aucune bibliothèque de graphe |

### Une vue ne possède rien

Les éléments et les relations restent dans le graphe. Retirer une boîte d'un
diagramme ne supprime jamais l'élément ; tirer un lien entre deux boîtes crée
une relation dans le graphe, validée par le métamodèle comme n'importe quelle
autre ([`0009`](0009-association-des-elements-dans-le-spa.md)). Il n'existe pas
de flèche « propre au diagramme ».

En lecture, `GET /diagrams/{id}` renvoie les boîtes, les éléments qu'elles
montrent (`ElementRead`) et **les relations dont les deux extrémités sont sur
le diagramme** (`RelationshipRead`) — une seule requête Cypher,
`view_of($ids)`, où les identifiants sont un paramètre lié. Une relation créée
ailleurs entre deux éléments déjà posés apparaît donc d'elle-même.

### Pourquoi PostgreSQL

C'est « tout ce qui n'est pas un graphe »
([`0015`](0015-socle-postgresql-sqlalchemy-alembic.md)) : un nom, une liste de
positions, une date. Les ranger comme propriétés ou nœuds dans Neo4j ferait
porter au graphe d'architecture des coordonnées d'écran, que chaque parcours
croiserait sans en avoir l'usage.

Une disposition se remplace en entier (`PUT /diagrams/{id}/layout`), dans une
transaction : les anciennes boîtes sont effacées et les nouvelles écrites
ensemble. Le SPA enregistre après chaque dépôt, déplacement ou retrait ; il n'y
a pas de bouton « Enregistrer ».

### La cascade passe par `ElementAttachments`

`element_id` désigne un nœud Neo4j, exactement comme
`element_documents.element_id` ([`0017`](0017-documents-markdown-attaches-aux-elements.md)) :
PostgreSQL n'a rien à référencer. Le dépôt de diagrammes implémente donc le
même port étroit, `discard_for_element`, et la suppression d'un élément garde
**une seule** cascade : `ArchitectureService.delete_element` reçoit un
`AllAttachments(documents, diagrammes)` qui la distribue. Chaque magasin est
sollicité même si un autre échoue, puis l'échec remonte, et la journalisation
des lignes orphelines (`action=attachments_orphaned`) reste celle d'avant.

Deuxième ligne de défense : à la lecture, une boîte dont l'élément n'existe
plus est ignorée. Une écriture, elle, est refusée (422) si un élément posé
n'existe pas ou apparaît deux fois.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker les diagrammes dans Neo4j (nœud `:Diagram`, arêtes vers les éléments) | La cascade est un `DETACH DELETE` | Des coordonnées d'écran dans le graphe d'architecture ; chaque parcours doit apprendre à ignorer ces nœuds | Écarté |
| Des relations propres au diagramme | Dessiner sans contrainte du métamodèle | Un second modèle, libre de contredire le graphe — exactement ce qu'ArchiMate appelle une vue pour l'éviter | Écarté |
| Garder la disposition dans le navigateur (`localStorage`) | Aucune table, aucune API | Perdue d'un poste à l'autre, invisible pour un collègue | Écarté |
| Une bibliothèque de graphe (Cytoscape, JointJS…) | Glisser-déposer et routage des liens fournis | Une dépendance lourde pour des boîtes et des segments ; [`0013`](0013-ecran-d-analyse-d-impact.md) dessine déjà à la main | Écarté |
| Un second mécanisme de cascade à côté de `ElementAttachments` | Aucun changement du service d'architecture | Deux chemins de suppression à garder cohérents | Écarté — un composite suffit |

## Conséquences

- **Deux stockages peuvent diverger**, comme pour les documents : une panne
  entre la suppression du nœud et celle des boîtes laisse des lignes
  orphelines. Elles sont journalisées, et ignorées à la lecture.
- **La cascade ne suit pas une base fermée.** Avec `EA_POSTGRES_ENABLED=false`,
  `/diagrams` répond 500 (erreur de câblage), jamais une liste vide.
- **500 boîtes au plus par diagramme, coordonnées dans ±100 000.** Des bornes
  déclarées une fois dans `api/schemas.py` ; au-delà, c'est un export, pas une
  vue qu'on lit.
- **Pas d'outil MCP pour les diagrammes, pas de pagination de `/diagrams`.**
  Ni l'un ni l'autre n'a encore d'usage ; les deux s'ajoutent sans toucher au
  stockage.
- **À surveiller :** l'enregistrement complet de la disposition à chaque geste
  est simple et sans conflit pour un seul auteur ; deux personnes éditant le
  même diagramme se remplaceront l'une l'autre, en silence.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0009](0009-association-des-elements-dans-le-spa.md),
  [0013](0013-ecran-d-analyse-d-impact.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0017](0017-documents-markdown-attaches-aux-elements.md)
- Code concerné : `backend/src/ea/domain/diagrams.py`,
  `backend/src/ea/services/diagrams.py`,
  `backend/src/ea/repositories/diagram_store.py`,
  `backend/src/ea/db/models/diagram.py`,
  `backend/src/ea/api/diagrams.py`,
  `backend/migrations/versions/0004_diagrams.py`
