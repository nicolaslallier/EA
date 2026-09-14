---
titre: PostgreSQL seul — le graphe d'architecture en tables relationnelles
date: 2026-09-14
statut: Proposition
affects: backend/src/ea/repositories/architecture_store.py, backend/src/ea/db/models/architecture.py, backend/migrations/versions/0005_architecture_graph.py, backend/migrations/versions/0006_attachments_follow_elements.py, backend/src/ea/services/architecture.py, backend/src/ea/domain/ports.py, Makefile, docker-compose.yml, deploy/, .github/workflows/ci.yml
---

# 33. PostgreSQL seul : le graphe d'architecture en tables relationnelles

## Contexte

L'ADR [0004](0004-neo4j-pour-le-graphe-d-architecture.md) a mis le graphe dans
Neo4j et laissé à PostgreSQL « ce qui n'est pas un graphe ». Depuis, PostgreSQL
porte les documents ([0017](0017-documents-markdown-attaches-aux-elements.md)),
leur index vectoriel ([0019](0019-recherche-semantique-sur-les-documents.md)) et
les diagrammes ([0031](0031-diagrammes-enregistres.md)) : chacun désigne un
élément qu'aucune clé étrangère ne pouvait atteindre. Le prix annoncé par 0004
est arrivé en entier : deux bases à exploiter, deux sauvegardes — dont une hors
ligne seulement ([0025](0025-sauvegardes-des-deux-bases.md)) —, deux conteneurs
jetables ([0024](0024-tests-d-integration-sur-des-bases-jetables.md)), un port
`ElementAttachments` et des lignes orphelines acceptées faute de transaction
commune.

0004 avait écarté le graphe relationnel sur un seul motif : la lisibilité de
l'analyse d'impact en SQL récursif.

## Décision

**Le graphe d'architecture est stocké dans PostgreSQL. Neo4j quitte le projet.**

- `elements` et `relationships` (révision `0005`) ; les attributs utilisateur
  en `jsonb`, sans préfixe ; `UNIQUE (element_type, name)`.
- Les deux règles de l'IPAM ([0020](0020-adressage-ip-en-proprietes-du-graphe.md))
  deviennent les index uniques partiels `uq_elements_vrf_ip_address` et
  `uq_elements_vrf_cidr` sur `properties ->> 'vrf'` et la clé concernée, avec
  `WHERE properties ? 'vrf' AND properties ? '<clé>'` : un élément sans l'une
  des deux clés n'est pas concerné, comme sous Neo4j. Un refus est traduit
  d'après le **nom** de la contrainte, plus d'après le texte du message.
- `relationships.source_id` / `target_id`, `element_documents.element_id`
  et `diagram_nodes.element_id` sont des clés étrangères `ON DELETE CASCADE`
  (révision `0006`). Supprimer un élément est une transaction ; le port
  `ElementAttachments` disparaît.
- Voisinage, analyse d'impact et détection de cycle sont trois CTE
  `WITH RECURSIVE` en SQLAlchemy Core dans `repositories/architecture_store.py`.
  La profondeur est un paramètre lié : la dérogation « trois sites Cypher
  composés » de `CLAUDE.md` disparaît.
- `EA_LOG_CYPHER` disparaît ; `EA_LOG_SQL` couvre toutes les requêtes.

Le coût de lisibilité reconnu par 0004 est accepté : trois requêtes, un fichier,
couvertes par les tests d'intégration du graphe et de l'IPAM écrits contre
Neo4j et conservés tels quels comme contrat.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder Neo4j | Cypher lisible | deux bases, pas de transaction commune, orphelins | écarté |
| Apache AGE | Cypher conservé dans PostgreSQL | image à construire portant pgvector *et* AGE ; intégration SQLAlchemy artisanale | écarté |
| Adjacence en `jsonb` | une seule table | ni clé étrangère ni contrainte d'unicité | écarté |
| Tables + CTE récursives | une base, une chaîne Alembic, FK, contraintes nommées | SQL récursif moins lisible | **retenu** |

## Conséquences

- **Bascule.** L'image applique `alembic upgrade head` au démarrage. `0006`
  refuse de tourner si `elements` est vide alors que des documents ou des
  boîtes existent, pour qu'un déploiement arrivé avant l'import ne les efface
  pas comme orphelines. Procédure : `make pg-backup` et dump Neo4j hors ligne ;
  `make app-down` ; `make graph-import CONFIRM=yes` (`0005`, copie en une
  transaction, vérification des ids, `0006`) ; fusion sur `main` et
  `make app-up` ; vérification dans le SPA. Retour arrière : `make pg-restore`,
  le graphe Neo4j n'ayant pas été touché.
- Dump Neo4j hors ligne, à faire une dernière fois avant la bascule, sur le Mac
  de la stack Infra :
  `docker stop infra-neo4j-1 && docker run --rm --volumes-from infra-neo4j-1 -v "$dir":/backups <image> neo4j-admin database dump neo4j --to-path=/backups && docker start infra-neo4j-1`.
- Le service `neo4j` de la stack Infra reste en place comme filet jusqu'à son
  retrait dans ce dépôt-là. `scripts/import_neo4j.py`, `ea.graph_import` et la
  cible `graph-import` sont supprimés une fois la bascule confirmée ; cet ADR
  passe alors à *Accepté*.
- Une seule base à sauvegarder : `make pg-backup` couvre tout le modèle
  (amende 0025). Un seul conteneur jetable et plus
  d'`EA_ALLOW_DESTRUCTIVE_TESTS` (amende 0024). La recherche du catalogue est
  un parcours complet de `elements` ; un index trigramme sera à ajouter
  au-delà de ~100 000 éléments.
- Aucun contrat visible ne change : schéma OpenAPI, outils MCP, SPA et
  `pipelines/` sont identiques.

## Références

- Remplace : [0004](0004-neo4j-pour-le-graphe-d-architecture.md), [0030](0030-neo4j-dans-la-stack-infra.md)
- Amende : [0017](0017-documents-markdown-attaches-aux-elements.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0021](0021-des-logs-que-quelqu-un-peut-lire.md), [0024](0024-tests-d-integration-sur-des-bases-jetables.md), [0025](0025-sauvegardes-des-deux-bases.md), [0031](0031-diagrammes-enregistres.md)
- Spec : `docs/superpowers/specs/2026-09-14-postgresql-seul-design.md`
