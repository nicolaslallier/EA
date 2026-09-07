# 4. Neo4j pour le graphe d'architecture, aux côtés de PostgreSQL

Date : 2026-09-07
Statut : Accepté

## Contexte

`CLAUDE.md` verrouillait PostgreSQL comme unique base de données, avec
SQLAlchemy 2 et Alembic. Cette décision précédait tout modèle de domaine.

Le domaine est maintenant connu : un référentiel d'architecture d'entreprise.
Ses données sont un graphe — des applications, des capacités, des nœuds
techniques, reliés par une dizaine de types de liens — et les questions qu'on
lui pose sont des parcours de longueur variable :

- « si ce serveur tombe, quels processus métier s'arrêtent ? » ;
- « quelles applications réalisent cette capacité, directement ou non ? » ;
- « qu'est-ce qui est contenu dans ce regroupement, à tous les niveaux ? ».

En relationnel, chacune de ces questions est une CTE récursive : la profondeur
n'est pas connue à l'écriture, chaque saut est une jointure de plus, et le
filtrage par type de lien doit être répété à chaque niveau. Le SQL devient long
avant de devenir lent, et il devient illisible avant les deux.

Trois options ont été pesées : Neo4j, Apache AGE (openCypher dans PostgreSQL), et
un graphe modélisé en tables PostgreSQL parcouru par CTE récursives.

## Décision

**Le modèle d'architecture est stocké dans Neo4j.** PostgreSQL reste dans la
pile pour ce qui n'est pas un graphe.

Partage des rôles :

| Donnée | Base |
|---|---|
| Éléments d'architecture, relations, parcours et analyses d'impact | Neo4j |
| Authentification, utilisateurs, journal d'audit, traitements planifiés | PostgreSQL |

PostgreSQL n'est référencé par aucun code aujourd'hui : rien de non-graphe
n'existe encore. `docker-compose.yml` le déclare sous le profil `full`
(`make db-up-all`) pour que la cible soit visible, mais `make db-up` ne démarre
que Neo4j. Les dépendances SQLAlchemy et Alembic seront ajoutées avec la
première table, pas avant.

### Pourquoi pas Apache AGE

AGE aurait gardé une seule base et laissé Alembic aux commandes. Il impose en
contrepartie une image Docker construite à la main, une intégration SQLAlchemy
artisanale, et un écosystème dont la maturité n'est pas comparable. Le coût
d'une seconde base est réel mais borné ; celui d'une extension peu outillée ne
l'est pas.

### Pourquoi pas un graphe relationnel

C'était l'option la moins chère en infrastructure et la seule à ne demander
aucune dérogation. Elle a été écartée sur la lisibilité des requêtes : l'analyse
d'impact, qui est la raison d'être de l'outil, s'écrit en une dizaine de lignes
de Cypher et en plusieurs dizaines de SQL récursif dont chaque évolution est
risquée.

## Conséquences

- **Il n'y a pas d'Alembic pour le graphe.** Un graphe n'a pas de schéma à
  migrer : il a des contraintes et des index. `backend/src/ea/db/schema.py`
  les déclare avec `IF NOT EXISTS` et l'application les applique au démarrage.
  Ajouter une contrainte, c'est ajouter une ligne à `SCHEMA_STATEMENTS`. Une
  migration de *données* — renommer un type d'élément, par exemple — devra être
  un script Cypher versionné ; ce cas ne s'est pas encore présenté.
- **Le domaine ne connaît pas Neo4j.** `domain/` n'importe pas le pilote ;
  `domain/ports.py` déclare le port, `repositories/archimate_graph.py`
  l'implémente. Remplacer le moteur reste un travail borné à un fichier.
- **Les valeurs variables sont toujours des paramètres Cypher.** Deux choses ne
  peuvent pas l'être : le type d'une relation et la borne d'un chemin de
  longueur variable. Les deux sont construites à partir d'une énumération fermée
  ou d'un entier borné, jamais d'une saisie utilisateur, et les trois endroits
  concernés portent un commentaire qui le dit.
- **Deux bases à exploiter le jour où PostgreSQL sera utilisé** : deux
  sauvegardes, deux jeux de migrations, et aucune transaction commune. Une
  écriture qui devrait toucher les deux devra être conçue pour tolérer un échec
  partiel. C'est le prix accepté ici ; le partage des rôles ci-dessus est fait
  pour qu'aucune opération courante n'ait besoin des deux.
- **L'isolation des tests d'intégration change de nature.** Neo4j Community ne
  sert qu'une seule base, donc pas de schéma de test séparé ni de transaction
  englobante à annuler : les tests vident le graphe entre chaque cas. C'est
  destructeur, donc explicite — `EA_ALLOW_DESTRUCTIVE_TESTS=1`, que seule la
  cible `make test-integration` positionne. Un `uv run pytest` nu les saute.
- **`CLAUDE.md` est modifié dans le même commit**, conformément à sa propre
  règle.
