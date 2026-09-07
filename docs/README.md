# Documentation

Ce répertoire est le point d'entrée documentaire du projet **EA** — *Enterprise
Architecture*. Le projet n'a pas encore tout écrit ; ce qui suit décrit ce qui
existe, ce qui est décidé, et ce qui reste à faire, pour qu'un lecteur n'ait pas
à deviner.

## Que lire en premier

| Document | Rôle |
|---|---|
| [`../README.md`](../README.md) | Démarrage local : prérequis, `make`, ports, URLs |
| [`../CLAUDE.md`](../CLAUDE.md) | Conventions de la pile, TDD, règles de sécurité, définition de « fait » |
| [`../Makefile`](../Makefile) | Le seul point d'entrée des commandes de développement local |
| [`adr/`](adr/) | Les décisions d'architecture et leurs conséquences |
| [`adr/0001` – `0005`](adr/) | Orchestration, frontend, outiling, base graphe, métamodèle |
| [`sdlc/`](sdlc/) | Cycle de vie : branches, commits, PR, définition de « fait » |
| [`tdd/`](tdd/) | Boucle TDD : rouge/vert/refactor, trois niveaux de test |
| [`togaf/`](togaf/) | TOGAF 10 — le process d'architecture que le référentiel sert |

`CLAUDE.md` est à lire entièrement : c'est la charte d'ingénierie du projet.
Ce fichier ne la répète pas, il la résume en lien.

## Ce qu'est le projet

EA est un **référentiel d'architecture d'entreprise**. Il stocke un modèle
d'architecture — applications, capacités, services, flux, propriétaires, nœuds
techniques, reliés par une dizaine de types de liens — et répond à des
questions d'**analyse d'impact** :

- « si ce serveur tombe, quels processus métier s'arrêtent ? » ;
- « quelles applications réalisent cette capacité, directement ou non ? » ;
- « qu'est-ce qui est contenu dans ce regroupement, à tous les niveaux ? ».

Le modèle **est** un graphe, et les questions qu'on lui pose sont des parcours de
longueur variable. C'est la raison du choix de Neo4j — voir
[`adr/0004`](adr/0004-neo4j-pour-le-graphe-d-architecture.md).

### Le métamodèle : ArchiMate 3.2

Le référentiel ne réinvente pas son vocabulaire : il adopte **ArchiMate 3.2
complet**, norme de l'Open Group — 61 types d'éléments, 11 types de relations,
avec validation des couples autorisés. Voir
[`adr/0005`](adr/0005-archimate-3-2-comme-metamodele.md).

La matrice des relations autorisées (61 × 61 cases, annexe B de la
spécification) **n'est pas recopiée** : elle est **déduite** des règles
structurelles du métamodèle, codées dans `domain/archimate/rules.py`. Un refus
est donc *explicable* (il nomme la règle enfreinte), et les 3 721 cases ne
peuvent pas diverger entre elles car elles n'existent pas comme données. Les
couples que la norme autorise sans qu'ils découlent des règles générales sont
listés dans `_EXTRA_ALLOWED` — c'est le point d'extension.

Les couches et aspects d'ArchiMate sont les coordonnées dont dépendent toutes les
règles :

- **Couches** (`domain/archimate/taxonomy.py`) : *motivation*, *strategy*,
  *business*, *application*, *technology*, *physical*, *implementation &
  migration*, *other*.
- **Aspects** : *active structure*, *behavior*, *passive structure*,
  *motivation*, plus des raffinements — *composite*, *connector*, *interface*,
  *service*, *event*.
- **Le sens de la flèche n'est pas uniforme.** Un service *sert* un processus :
  perdre le service casse le processus, donc l'impact suit la flèche. Un tout
  *compose* une partie : perdre la partie casse le tout, donc l'impact revient
  contre la flèche. L'analyse d'impact doit parcourir chaque saut dans le bon
  sens, ce que fait `domain/archimate/relations.py` via
  `impact_follows_direction`.

## Architecture

### Le backend : `api → services → domain ← repositories`

La dépendance va **dans un seul sens**. `domain/` n'importe ni FastAPI, ni
SQLAlchemy, ni `api/` ; `repositories/` dépend du port déclaré dans
`domain/ports.py` et rien d'autre.

```
backend/src/ea/
  api/            Routers FastAPI, schémas de requête/réponse, dépendances
  domain/         Entités et valeurs — AUCUN framework
    archimate/    Le métamodele ArchiMate 3.2 : taxonomie, relations, règles
  services/       Cas d'usage ; orchestrent le domaine + les ports, possèdent les transactions
  repositories/   Implémentations Cypher des ports déclarés dans domain/
  db/             Cycle de vie du pilote Neo4j et schéma (contraintes + index)
  core/           Config (pydantic-settings), logging, erreurs
```

C'est un **port-adapter** : `domain/ports.py` déclare
`ArchitectureRepository` (un `Protocol` structuré), et
`repositories/archimate_graph.py` l'implémente sur Neo4j sans hériter d'une
classe de base. Remplacer le moteur reste un travail borné à un fichier.

### Les deux bases

Neo4j et PostgreSQL jouent des rôles distincts
([`adr/0004`](adr/0004-neo4j-pour-le-graphe-d-architecture.md)) :

| Donnée | Base |
|---|---|
| Éléments d'architecture, relations, parcours, analyses d'impact | **Neo4j** |
| Authentification, utilisateurs, journal d'audit, traitements planifiés | **PostgreSQL** |

**Postgres n'est référencé par aucun code aujourd'hui.** Il est déclaré dans
`docker-compose.yml` sous le profil `full` pour que la cible
`make db-up-all` soit visible, mais `make db-up` ne démarre que Neo4j. Les
dépendances SQLAlchemy et Alembic seront ajoutées avec la première table, pas
avant.

**Il n'y a pas d'Alembic pour le graphe.** Neo4j n'a pas de schéma à migrer ; il
a des contraintes et des index. `db/schema.py` les déclare avec
`IF NOT EXISTS` et l'application applique toute la liste au démarrage, donc
ajouter une contrainte, c'est ajouter une ligne à `SCHEMA_STATEMENTS`. Une
migration de *données* — renommer un type d'élément — sera un script Cypher
versionné ; ce cas ne s'est pas encore présenté.

### Le stockage du graphe

Trois choix portent de la performance et de la lisibilité
(`db/schema.py`, [`adr/0004`](adr/0004-neo4j-pour-le-graphe-d-architecture.md)) :

- **Un seul label `:Element`** pour tous les nœuds ; le type ArchiMate est la
  propriété indexée `element_type`, pas un label. Cela garde 61 labels — et le
  Cypher dynamique nécessaire pour les écrire — hors du codebase.
- **Chaque relation porte son type ArchiMate comme vrai type de relation
  Neo4j**, car c'est sur cela qu'un patron de parcours fait correspondre. Il y en
  a onze, en nombre fermé, donc elles sont listées littéralement.
- **Les attributs utilisateur sont stockés à plat sous un préfixe `p_`**, pour
  rester interrogeables (`MATCH (e:Element) WHERE e.p_owner = 'finance'`) sans
  dépaqueter du JSON.

## L'API

Servie par `create_app` (`main.py`), sur `http://127.0.0.1:8000`.

| Route | Ce qu'elle fait |
|---|---|
| `GET /health` | État de santé du backend (consommé par le frontend) |
| `GET /metamodel` | Les 61 types d'éléments, les 11 relations, les couches |
| `GET /metamodel/relationships?source=&target=` | Les liens légaux entre deux types donnés, les plus forts d'abord |
| `POST /elements` | Ajoute un élément d'architecture (201 / 409) |
| `GET /elements` | Parcourt le catalogue (filtres type, couche, recherche, pagination) |
| `GET /elements/{id}` | Lit un élément |
| `PATCH /elements/{id}` | Modifie un élément — **son type est figé après création** |
| `DELETE /elements/{id}` | Supprime un élément et toutes ses relations |
| `POST /relationships` | Relie deux éléments, si ArchiMate l'autorise (201 / 404 / 409 / 422) |
| `GET /relationships` | Liste les liens, éventuellement autour d'un élément |
| `GET /elements/{id}/relationships` | Les liens directs d'un élément **et les éléments aux deux bouts** |
| `DELETE /relationships/{id}` | Supprime un lien |
| `GET /elements/{id}/neighbourhood` | Le sous-graphe autour d'un élément, dans les deux sens |
| `GET /elements/{id}/impact` | Ce qui dépend d'un élément, transitivement |

Le frontend **ne redéclare jamais les 61 types ni les règles** : il interroge
`GET /metamodel` et `GET /metamodel/relationships`, servis par **le même code qui
valide les écritures**. Un lien interdit est refusé en 422 avec la règle
enfreinte :

```
access: business_process -> application_service is not permitted
— the ArchiMate 3.2 metamodel does not allow this relationship
```

Les parcours bornent leur profondeur à `MAX_TRAVERSAL_DEPTH = 10` : un parcours
plus profond que cela revient à un parcours complet du graphe déguisé en filtre.

## Le frontend

Vue 3 + TypeScript + Vite, en `<script setup>`
([`adr/0002`](adr/0002-frontend-vue-3-plutot-que-react.md)).

**État actuel :** une coquille routée dont le menu est engendré par
`src/router/sections.ts` ([`adr/0008`](adr/0008-menu-et-routage-du-spa.md)), avec
deux sections construites :

- **Éléments** — parcourir, créer, modifier, supprimer
  ([`adr/0007`](adr/0007-client-openapi-genere-pour-le-spa.md)). Le nom d'une
  ligne est un bouton : il ouvre le détail de l'élément — documentation,
  aspect, attributs libres, identifiant, dates — sous `?element=<id>`, donc la
  vue s'envoie par lien ([`adr/0011`](adr/0011-detail-d-un-element-dans-le-catalogue.md)) ;
- **Relations** — choisir un élément, lister ses liens, en ajouter et en retirer
  ([`adr/0009`](adr/0009-association-des-elements-dans-le-spa.md)). Le même
  panneau s'ouvre depuis n'importe quelle ligne du catalogue. Le formulaire ne
  connaît aucune règle : il demande à `GET /metamodel/relationships` ce que le
  couple autorise et n'offre que la réponse ;
- **Voisinage** — le sous-graphe autour d'un élément, *dessiné* : des anneaux
  concentriques, un par saut, aux couleurs de couche d'ArchiMate
  ([`adr/0010`](adr/0010-dessiner-le-voisinage-d-un-element.md)). Un clic sur un
  voisin déplace le centre. La question — quel élément, quelle profondeur,
  quelle relation suivie — vit dans l'URL, donc la vue s'envoie par lien et le
  bouton *Précédent* remonte l'exploration.

`BackendStatus.vue` affiche l'état du backend. Le métamodèle et le parcours
d'impact (`/impact`) sont annoncés *à venir* dans le menu et n'ont pas d'écran ;
`/impact` répond le même `GraphRead` que le voisinage et se dessinera avec les
mêmes composants.

**Contrat front/back** ([`CLAUDE.md`](../CLAUDE.md)) : le schéma OpenAPI du
backend est la source unique de vérité. On ne rédige jamais à la main une
interface TypeScript qui reflète un modèle Pydantic ; on régénère
`frontend/src/api/` (`npm run generate:api`, ou `make openapi` à la racine) et on
importe de là. Un changement backend qui fait bouger le schéma et ne régénère pas
le client est un changement inachevé — `make openapi-check` le vérifie.

## Les tests

Le **TDD est le mode de travail par défaut** (`CLAUDE.md`). Par changement :

1. **Unitaire** (`tests/unit`, sans I/O) : règles de domaine, validation,
   fonctions pures. Millisecondes.
2. **Intégration** (`tests/integration`) : repositories et services contre un
   vrai Neo4j. Pas de pilote mocké, pas de double factice — ces tests
    *prouvent* le Cypher.
3. **API** (`tests/e2e`, côté backend) : `httpx.AsyncClient` contre l'app.

L'isolation des tests d'intégration est « vider le graphe entre chaque cas » et
non une transaction annulée, car Neo4j Community ne sert qu'une seule base. C'est
destructeur, donc limité par `EA_ALLOW_DESTRUCTIVE_TESTS=1`, que
seule `make test-integration` positionne. Un `uv run pytest` nu les saute.

Plancher de couverture : **90 %** sur `backend/src`.

## Sécurité

Récapitulatif — voir `CLAUDE.md` pour le détail :

- Configuration par l'environnement via `pydantic-settings`, **jamais** de secret
  en dur. `Settings` refuse de démarrer sans mot de passe Neo4j hors `EA_DEBUG`.
- Autorisation appliquée dans `services/`, **jamais** seulement dans le router
  ni dans le frontend.
- **Cypher : toute valeur runtime est un paramètre lié.** Trois choses ne peuvent
  pas l'être — le type d'une relation, la borne d'un chemin variable, et un type
  de relation dans `WHERE type(r)` ; les trois sont construites à partir d'une
  énumération fermée ou d'un entier borné, et chaque site porte un commentaire
  qui le dit.
- CORS : liste explicite depuis les settings, **jamais** `*` avec credentials.
- Erreurs renvoyées au client : typées et génériques ; traces et messages de base
  vont dans les logs structurés, pas dans le corps de réponse.
- `bandit`, `pip-audit` et `npm audit` tournent en CI — **pas encore mis en
  place.**

## État et chemin restant

**Existe :** domaine ArchiMate complet, repository Neo4j, deux parcours
(neighbourhood + impact), l'API entière, et trois écrans — catalogue, relations,
voisinage.

**Décidé mais pas encore écrit** (ne pas supposer que cela existe) :

- Authentification (OAuth2 / JWT + `argon2`), `SQLAlchemy`, `Alembic`, la première
  table PostgreSQL.
- `bandit`, `pip-audit`, `ESLint` (`npm run lint`), `npm run generate:api`,
  Playwright, `pre-commit`, CI, et l'écran d'analyse d'impact.
- L'export vers le format d'échange ArchiMate (Open Exchange File) n'est pas
  implémenté, mais rien ne s'y oppose — la taxonomie est complète.

Le jour où l'une de ces briques arrive, ou qu'une décision change de cap, un
nouvel ADR est créé dans [`adr/`](adr/) — contexte, décision, conséquences — et
`CLAUDE.md` est mis à jour **dans le même commit**.

## Index des ADR

| N° | Titre | Statut |
|---|---|---|
| [0001](adr/0001-orchestration-locale-via-makefile.md) | Orchestration du développement local via un Makefile | Accepté |
| [0002](adr/0002-frontend-vue-3-plutot-que-react.md) | Frontend en Vue 3 plutôt qu'en React | Accepté |
| [0003](adr/0003-uv-comme-chaine-outils-python.md) | `uv` comme chaîne d'outils Python | Accepté |
| [0004](adr/0004-neo4j-pour-le-graphe-d-architecture.md) | Neo4j pour le graphe d'architecture | Accepté |
| [0005](adr/0005-archimate-3-2-comme-metamodele.md) | ArchiMate 3.2 comme métamodèle du référentiel | Accepté |
| [0006](adr/0006-neo4j-sur-le-cluster-docker.md) | Neo4j sur le cluster Docker | Accepté |
| [0007](adr/0007-client-openapi-genere-pour-le-spa.md) | Client OpenAPI généré, et premier écran de CRUD | Accepté |
| [0008](adr/0008-menu-et-routage-du-spa.md) | Menu de sections et routage du SPA | Accepté |
| [0009](adr/0009-association-des-elements-dans-le-spa.md) | Associer deux éléments depuis le SPA | Accepté |
| [0010](adr/0010-dessiner-le-voisinage-d-un-element.md) | Dessiner le voisinage d'un élément | Accepté |
| [0011](adr/0011-detail-d-un-element-dans-le-catalogue.md) | Le détail d'un élément, ouvert depuis le catalogue | Accepté |
| [TEMPLATE](adr/TEMPLATE.md) | Gabarit d'ADR à copier pour toute nouvelle décision | — |
