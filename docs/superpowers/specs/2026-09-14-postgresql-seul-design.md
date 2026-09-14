# PostgreSQL seul : le graphe d'architecture en tables relationnelles

Date : 2026-09-14
Statut : design validé, en attente de relecture de la spec

## Objectif

Retirer Neo4j du projet. Le graphe d'architecture (éléments, relations,
traversées, IPAM) est stocké dans le PostgreSQL partagé qui porte déjà les
documents, leur index et les diagrammes. Les données existantes du graphe
partagé sont migrées, ids conservés.

**Critère de succès** : après bascule, l'API, le SPA, les 28 outils MCP et
`pipelines/` fonctionnent sans changement de contrat (`make openapi-check`
vert, schéma OpenAPI identique), le graphe importé a les mêmes éléments et
relations que Neo4j (comptes et ensembles d'ids égaux), et aucun code ni outil
du dépôt ne référence plus Neo4j hormis le script d'import ponctuel.

## Approche retenue

Tables relationnelles + CTE récursives (option « graphe relationnel » écartée
par l'ADR 0004 pour sa lisibilité). Écartées : Apache AGE (image custom
pgvector+AGE, intégration SQLAlchemy artisanale), adjacence en `jsonb` (perte
des FK et des contraintes d'unicité).

Le coût de lisibilité reconnu par 0004 est accepté : il est borné à trois
requêtes récursives dans un seul fichier, couvertes par les 42 tests
d'intégration existants du graphe et de l'IPAM, qui deviennent le contrat du
nouveau repository.

## 1. Schéma

### Révision `0005` — tables du graphe

**`elements`**

| Colonne | Type | Note |
|---|---|---|
| `id` | `uuid` PK | |
| `element_type` | `text NOT NULL` | valeur de `ElementType` |
| `layer` | `text NOT NULL` | dérivée du type, stockée pour le filtre |
| `aspect` | `text NOT NULL` | idem |
| `name` | `text NOT NULL` | |
| `description` | `text NOT NULL DEFAULT ''` | |
| `documentation` | `text NOT NULL DEFAULT ''` | |
| `properties` | `jsonb NOT NULL DEFAULT '{}'` | attributs utilisateur, **sans** préfixe `p_` |
| `created_at`, `updated_at` | `timestamptz NOT NULL` | |

- `uq_elements_element_type_name` : `UNIQUE (element_type, name)` — l'ancien
  `element_name_unique_per_type`, sur lequel `pipelines/` détecte les doublons.
- Index `ix_elements_element_type`, `ix_elements_layer`.
- `uq_elements_vrf_ip_address` : index unique partiel
  `((properties->>'vrf'), (properties->>'ip_address')) WHERE properties ? 'vrf' AND properties ? 'ip_address'`.
- `uq_elements_vrf_cidr` : idem sur `cidr`.
- Le `WHERE` reproduit la sémantique Neo4j : un élément sans l'une des deux
  propriétés n'est pas concerné. Le service continue d'écrire `vrf` à côté de
  chaque adresse et préfixe (`validate_ipam_properties`), en graphie canonique.

**`relationships`**

| Colonne | Type | Note |
|---|---|---|
| `id` | `uuid` PK | |
| `relationship_type` | `text NOT NULL` | valeur de `RelationshipType` |
| `source_id`, `target_id` | `uuid NOT NULL REFERENCES elements(id) ON DELETE CASCADE` | |
| `source_type`, `target_type` | `text NOT NULL` | comme aujourd'hui |
| `name` | `text NOT NULL DEFAULT ''` | |
| `access_type` | `text NULL` | |
| `directed` | `boolean NOT NULL DEFAULT false` | |
| `properties` | `jsonb NOT NULL DEFAULT '{}'` | |
| `created_at` | `timestamptz NOT NULL` | |

- Index `ix_relationships_source_id`, `ix_relationships_target_id`.

Les noms suivent la convention de nommage figée de `db/base.py`.

### Révision `0006` — FK des attachements

1. Si `elements` est vide **et** que `element_documents` ou `diagram_nodes`
   contient une ligne : lever une erreur « importe le graphe d'abord
   (`make graph-import`) ». Aucune suppression.
2. Sinon : supprimer les lignes de `element_documents` et `diagram_nodes` dont
   l'`element_id` n'existe pas dans `elements` (orphelins acceptés par l'ADR
   0017), en affichant le nombre supprimé par table.
3. Ajouter `element_documents.element_id` et `diagram_nodes.element_id`
   `REFERENCES elements(id) ON DELETE CASCADE`.

`downgrade` retire les deux FK (les lignes supprimées ne reviennent pas).
Le downgrade de `0005` supprime les deux tables.

Garde-fou : l'image applique `alembic upgrade head` à chaque démarrage
(`backend/Dockerfile`) ; le point 1 empêche qu'un déploiement avant l'import
efface documents et diagrammes.

## 2. Repository et traversées

### Fichiers

- `db/models/architecture.py` : `ElementRecord`, `RelationshipRecord` ; importé
  par `db/models/__init__.py`.
- `repositories/architecture_store.py` : `PostgresArchitectureRepository(sessions)`,
  satisfait `ArchitectureRepository` et `IpamRepository` (structurel). Même
  motif que `diagram_store.py` : session factory, une unité de travail par appel.
- `MAX_TRAVERSAL_DEPTH = 10` et `_clamp_depth` y sont déplacés.

### Opérations

- CRUD : constructions SQLAlchemy directes. `save_element` remplace toute la
  ligne (dont `properties`), comme `SET e = $properties`. `list_elements` trie
  par `name, id`, `list_relationships` par `created_at, id`.
- Recherche du catalogue : `strpos(lower(name), lower(:search)) > 0` —
  sémantique littérale du `CONTAINS` Cypher (`%` et `_` restent des
  caractères). Pas d'index trigramme (`ponytail:` à ajouter au-delà de ~100k
  éléments).
- IPAM : `networks` → `WHERE properties ? 'cidr' ORDER BY properties->>'cidr', name` ;
  `addressed_elements` → `WHERE properties ? 'ip_address' ORDER BY name` ;
  `element_at` → égalité sur `vrf` et `ip_address`.
- `delete_element` : un `DELETE` ; relations, documents et boîtes suivent par FK.

### Refus

`IntegrityError` → lecture de `constraint_name` sur l'exception asyncpg
d'origine :

| Contrainte | Erreur de domaine |
|---|---|
| `uq_elements_element_type_name` | `DuplicateElementError` |
| `uq_elements_vrf_ip_address` | `AddressAlreadyAssignedError` |
| `uq_elements_vrf_cidr` | `DuplicateNetworkError` |

Les messages restent ceux de `_rejected` aujourd'hui. Une contrainte inconnue
est relancée telle quelle (500 typé), pas convertie en doublon de nom.

### Traversées

Toutes en SQLAlchemy Core (`cte(recursive=True)`), paramètres liés, profondeur
comprise. Forme commune : CTE `reached(id, hops)` initialisée à l'élément de
départ (`hops = 0`), étape récursive tant que `hops < :depth` ; en sortie
`GROUP BY id`. Réponse : les éléments atteints, puis les relations dont les
deux extrémités sont atteintes (filtrées par `relationship_types` si non vide).
Un élément inexistant donne un `GraphView` vide.

| Méthode | Étape récursive |
|---|---|
| `neighbourhood` | relation où le nœud courant est `source_id` ou `target_id`, type dans le filtre ; on avance vers l'autre extrémité |
| `impacted_by` | type dans `along_the_arrow` : `source_id` courant → `target_id` ; sinon `target_id` courant → `source_id` ; type dans le filtre. `along_the_arrow` = types où `impact_follows_direction` |
| `would_close_a_containment_cycle` | depuis `target`, suivre `COMPOSITION`/`AGGREGATION` de `source_id` vers `target_id` ; vrai si `source` est atteinte ou `source = target` |

Sans récursion :
- `relations_of` : l'élément en tête, puis les autres extrémités de ses
  relations (filtrées), sans doublon en cas d'auto-association.
- `view_of` : éléments dont l'id est dans la liste, relations dont les deux
  extrémités y sont ; un id absent est ignoré.

Équivalence : Neo4j renvoie les nœuds atteignables par au moins un chemin
valide de longueur ≤ N ; un parcours en largeur à profondeur minimale donne le
même ensemble.

### Services

- `ArchitectureService` perd `attachments` ; suppression de `AllAttachments`,
  du port `ElementAttachments`, de `discard_for_element` dans
  `document_store.py` et `diagram_store.py`, et du log `attachments_orphaned`.
- `DocumentService` et `DiagramService` gardent la lecture préalable de
  l'élément (404 lisible).
- Aucune règle métier ne change ; `require_caller`/`require_editor` inchangés.

### Journalisation

`EA_LOG_CYPHER`, `CYPHER_LOGGER` et les loggers `neo4j` disparaissent ;
`EA_LOG_SQL` couvre toutes les requêtes.

## 3. Migration des données et bascule

### Script `backend/scripts/import_neo4j.py` (`make graph-import`)

Lancé par `uv run --with neo4j` ; `neo4j` quitte `pyproject.toml`.

1. `alembic upgrade 0005`.
2. Refuse si `elements` n'est pas vide.
3. Lit tous les `:Element` et toutes les relations de Neo4j
   (`EA_NEO4J_URI`/`EA_NEO4J_PASSWORD` lus dans l'environnement par le script
   seul, pas par `Settings`).
4. Conversion : retrait du préfixe `p_`, valeurs en `str`, dates Neo4j en
   `datetime` avec fuseau, ids conservés.
5. Écriture dans **une transaction** (éléments puis relations).
6. Vérification : comptes et ensembles d'ids égaux des deux côtés, sinon échec.
7. `alembic upgrade head` (`0006`).

### Runbook

| # | Étape | Retour arrière |
|---|---|---|
| 1 | `make pg-backup` + dump Neo4j hors ligne (procédure de l'actuel `make db-backup-howto`, recopiée dans l'ADR 0033) | — |
| 2 | `make app-down` | `make app-up` |
| 3 | `make graph-import` depuis le Mac, sur la branche de la PR 1 | `make pg-restore FILE=… CONFIRM=yes` ; Neo4j intact |
| 4 | fusion de la PR 1 sur `main`, `make app-up` | redéployer le commit précédent + restaurer le dump PG |
| 5 | vérifier dans le SPA : catalogue, voisinage, impact, IPAM, un document, un diagramme | — |

Le service `neo4j` de la stack Infra reste en place comme filet jusqu'à sa
suppression dans le dépôt Infra.

## 4. Suppressions et documentation

### Backend

- Supprimés : `db/neo4j.py`, `db/schema.py`, `repositories/archimate_graph.py`,
  dépendance `neo4j`, `Settings.neo4j_*` et son validateur, `log_cypher`.
- `main.py` : le lifespan construit `PostgresArchitectureRepository` sur la
  session factory existante.
- `reindex.py` : plus de pilote.
- Marqueur pytest `integration` : ne mentionne plus Neo4j.

### Tests

- Supprimés : `test_neo4j_driver.py`, `test_schema.py`, `test_cypher_tracing.py`.
- Réécrits : `test_ipam_constraint_translation.py` (aiguillage sur
  `constraint_name`), `test_architecture_delete.py` (devient un test
  d'intégration de la cascade FK : relations, documents, boîtes),
  `tests/integration/conftest.py` (`graph_service`/`graph_repository` sur
  `postgres_engine`, `TRUNCATE elements CASCADE` entre tests),
  `test_config.py`, `test_logging.py`, `test_deploy_stack.py`,
  `test_application_boot.py`, `throwaway.py` et `test_throwaway_guards.py`
  (garde Bolt retiré).
- Nouveaux : tests d'intégration des révisions `0005`/`0006` (refus si
  `elements` vide avec attachements, purge des orphelins sinon) ; test
  d'intégration de la recherche littérale (`%`, `_`).
- `EA_ALLOW_DESTRUCTIVE_TESTS` disparaît ; le garde PostgreSQL (loopback, pas
  5432) reste.

### Outillage

- `docker-compose.yml` : service `neo4j` retiré.
- `.github/workflows/ci.yml` : service `neo4j` et `EA_NEO4J_*` retirés du job
  d'intégration, renommé.
- `Makefile` : retrait de `db-stack`, `db-ping`, `db-shell`, `db-reset`,
  `db-backup-howto`, `db-test-up`, `require-neo4j-password`, `NEO4J_*` ;
  `test-integration` et `compose-*` sur PostgreSQL seul ; ajout de
  `graph-import`.
- `deploy/ea.stack.yml`, `deploy/ea.env.example`, `backend/.env.example` :
  lignes `EA_NEO4J_*` retirées.

### Inchangé

Schéma OpenAPI et client généré, SPA, outils MCP, `pipelines/`, Keycloak.

### Documentation

- **ADR 0033** « PostgreSQL seul : le graphe en tables relationnelles »,
  statut *Proposition* jusqu'à la bascule. Remplace 0004, 0006, 0030 (leur
  statut devient « Remplacé par 0033 », corps inchangé). Amende 0017, 0020,
  0021, 0024, 0025, 0031.
- **`CLAUDE.md`** réécrit dans le même commit : tableau du stack, arborescence,
  commandes, suppression de « The graph has no Alembic » et de la règle des
  trois sites Cypher, sections IPAM, documents et diagrammes ajustées.
- **README** mis à jour.

## Découpage

1. **PR 1** : tout ce qui précède (y compris le script d'import).
2. **Bascule** selon le runbook.
3. **PR 2** : suppression de `scripts/import_neo4j.py` et `graph-import` ;
   ADR 0033 passe à *Accepté*.
4. **Dépôt Infra** : retrait du service `neo4j`.

## Hors périmètre

Retrait de Neo4j de la stack Infra ; index trigramme ; toute évolution
fonctionnelle du catalogue, des traversées ou de l'IPAM.
