---
titre: Sauvegarder les deux bases du cluster, et savoir les restaurer
date: 2026-09-13
statut: Proposition
affects: Makefile, .gitignore, deploy/neo4j.stack.yml, deploy/postgres.stack.yml
---

# 25. Sauvegardes des deux bases

Date : 2026-09-13
Statut : Proposition

Complète [`0006`](0006-neo4j-sur-le-cluster-docker.md) et
[`0015`](0015-socle-postgresql-sqlalchemy-alembic.md), qui ont mis le graphe
puis la base relationnelle sur le cluster sans dire comment on les récupère.

## Contexte

Les deux bases du projet sont chacune **une instance unique, sur un seul hôte**
(192.168.1.252), dans un **volume Docker nommé** : `neo4j-data` pour le graphe,
`postgres-data` pour les documents et leur index. Aucune n'a de réplique, et
jusqu'ici aucune n'avait de sauvegarde.

`0006` affirmait que le graphe était « sauvegardable au même endroit que le
reste du cluster ». C'était une possibilité, pas un fait : rien ne le
sauvegardait. Ce qui peut arriver est banal — un disque qui lâche, un
`docker volume prune`, un `make db-reset CONFIRM=yes` sur la mauvaise
fenêtre, une migration qui détruit une colonne, un agent MCP qui supprime ce
qu'il n'aurait pas dû. Dans tous ces cas, le modèle d'architecture, qui est la
donnée du projet et pas un cache reconstructible, est perdu.

Les deux bases ne se sauvegardent pas de la même façon :

- **PostgreSQL** a `pg_dump`, qui lit la base *en ligne* dans un instantané
  cohérent. Rien n'a besoin d'être arrêté, et le client tourne depuis n'importe
  quel poste qui joint le port 5432.
- **Neo4j Community n'a pas de sauvegarde à chaud.** `neo4j-admin database
  backup` est réservé à l'édition Enterprise. Ce qui reste est
  `neo4j-admin database dump`, qui exige une base arrêtée — et en Community on
  n'arrête pas une base, on arrête le serveur. Cela se fait **sur l'hôte**,
  contre le volume, pas depuis un poste par Bolt.

## Décision

**Deux procédures, une par base, et une règle commune : une sauvegarde qui n'a
pas quitté l'hôte n'en est pas une.**

### PostgreSQL : `make pg-backup` et `make pg-restore`

| Cible | Effet |
|---|---|
| `make pg-backup` | `pg_dump --format=custom` de la base du cluster dans `backups/postgres-<base>-<horodatage UTC>.dump`, sur ce poste |
| `make pg-restore FILE=… CONFIRM=yes` | `pg_restore --clean --if-exists --no-owner --single-transaction` du fichier dans la base du cluster |

Points de conception :

- **Le client est celui du Mac s'il y en a un, sinon celui de l'image**, comme
  `pg-ping` et `pg-shell` depuis `8f4c142`. Le client du Mac (libpq 18) est
  plus récent que le serveur (17), ce que `pg_dump` exige. Le repli en
  conteneur prend `pgvector/pgvector:pg17` : il sait sauvegarder la base, mais
  un `pg_restore` 17 ne relit pas une archive écrite par un `pg_dump` 18 — un
  dump se restaure avec un client au moins aussi récent que celui qui l'a
  écrit.
- **Le format `custom`** est compressé, se relit sélectivement, et se vérifie :
  `pg-backup` écrit dans un `.partial`, le renomme seulement si `pg_dump` a
  réussi, puis relit la table des matières (`pg_restore --list`). Un fichier
  tronqué ne porte donc jamais le nom d'une sauvegarde.
- **`backups/` est ignoré par git, créé en `700`, et les fichiers en `600`.**
  Un dump contient chaque document attaché au modèle : des runbooks, des
  adresses, des noms de machines.
- **La restauration est une transaction.** `--single-transaction` fait qu'une
  erreur au milieu annule tout : la base est restaurée ou inchangée, jamais à
  moitié. `CONFIRM=yes` est obligatoire, comme pour `db-reset`, parce que c'est
  la base partagée qui est remplacée.
- **L'index des passages est dans le dump.** `document_chunks` et ses vecteurs
  reviennent avec les documents : pas de `make docs-reindex` après une
  restauration, sauf si le modèle d'embedding a changé depuis.
- **La révision Alembic aussi.** Un dump plus ancien que `head` ramène
  `alembic_version` à sa révision ; `make pg-migrate` remonte ensuite la
  chaîne.

### Neo4j : `make db-backup-howto`, une procédure hors ligne sur l'hôte

Rien dans le Makefile ne sauvegarde le graphe, et c'est voulu : la seule
méthode disponible arrête le serveur, et elle se lance sur l'hôte. La cible
**rappelle** la procédure, comme `make db-stack` rappelle le déploiement, et le
nom dit qu'elle ne fait que la rappeler — une cible `db-backup` qui imprime du
texte et sort en 0 serait une sauvegarde qu'on croit avoir faite.

Sauvegarde, sur l'hôte (SSH ou console de Portainer) :

```sh
img=$(docker inspect -f '{{.Config.Image}}' ea-neo4j)   # la version exacte du serveur
dir=/srv/backups/ea-neo4j/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$dir" && chown 7474:7474 "$dir"               # l'utilisateur neo4j de l'image
docker stop ea-neo4j
docker run --rm --volumes-from ea-neo4j -v "$dir":/backups "$img" \
  neo4j-admin database dump neo4j --to-path=/backups
docker start ea-neo4j
```

puis `make db-ping` depuis un poste. L'arrêt dure le temps du dump — quelques
secondes pour un référentiel de cette taille. `restart: unless-stopped` laisse
un conteneur arrêté par `docker stop` arrêté, donc le serveur ne redémarre pas
sous le dump.

- **`--volumes-from ea-neo4j`** plutôt que le nom du volume : Portainer préfixe
  les volumes du nom de la stack, et l'erreur « mauvais volume » produit un
  dump parfaitement valide d'une base vide.
- **L'image du conteneur en service**, lue par `docker inspect`, et pas le tag
  du Makefile : `neo4j-admin` doit être de la version du format de stockage
  qu'il lit. Depuis que les stacks épinglent un digest (`0026`), les deux
  coïncident, mais la procédure ne repose pas là-dessus.
- **Seule la base `neo4j` est sauvegardée.** La base `system` ne contient que
  l'utilisateur et son mot de passe, que `NEO4J_AUTH` recrée ; les contraintes
  et index sont réappliqués au démarrage de l'API (`db/schema.py`).

Restauration, sur l'hôte — elle **remplace le graphe partagé** :

```sh
docker stop ea-neo4j
docker run --rm --volumes-from ea-neo4j -v <dossier du dump>:/backups "$img" \
  neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
docker start ea-neo4j
```

puis `make db-ping`.

### La copie hors de l'hôte est une exigence, pas une suggestion

Un dump écrit dans `/srv/backups` sur 192.168.1.252 partage le disque, la
machine et l'alimentation du volume qu'il protège : il couvre le
`DETACH DELETE` malheureux, pas la panne. **Chaque sauvegarde du graphe est
copiée hors de l'hôte** (`scp`, NAS, stockage objet) avant d'être considérée
comme faite.

Le dump PostgreSQL naît hors de l'hôte, sur le poste qui lance `pg-backup`.
Mais un poste de développement n'est pas un lieu de conservation : le fichier
de `backups/` rejoint le même endroit que les dumps du graphe.

### Les deux ensemble

Le graphe et les documents se désignent mutuellement : `element_documents`
porte l'identifiant d'un nœud Neo4j, sans clé étrangère possible (`0017`). Deux
sauvegardes prises à des heures différentes restaurent un état où des documents
désignent des éléments qui n'existent plus — les « lignes injoignables » que
`0017` accepte déjà. Pour l'éviter : les deux dans la même fenêtre, sans
écriture entre les deux (API arrêtée, ou personne ne modélise), **graphe
d'abord** — son arrêt est l'étape qui interrompt de toute façon le service.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Neo4j Enterprise, pour `neo4j-admin database backup` à chaud | Sauvegarde sans arrêt, incrémentale | Licence commerciale pour un référentiel qui tient en mémoire ; `0006` a choisi Community | Écarté |
| Export APOC (`apoc.export.cypher.all`) en ligne | Pas d'arrêt, fichier Cypher lisible | APOC n'est pas installé ; l'export n'est pas un instantané cohérent si quelqu'un écrit pendant ; la restauration rejoue des requêtes au lieu de charger un magasin | Écarté pour l'instant |
| Copier le volume (`tar` de `/data`) | Aucun outil Neo4j | Serveur arrêté quand même ; lié à la version exacte du magasin ; plus gros qu'un dump | Écarté : `dump` fait la même chose, en mieux |
| `pg_basebackup` / archivage WAL (restauration à un instant donné) | Perte de données de quelques secondes au plus | Un second stockage à surveiller et un rôle de réplication, pour une base qui change quelques fois par jour | Écarté, à reconsidérer si l'auth ou l'audit y écrivent en continu |
| Planifier les sauvegardes depuis ce dépôt (cron sur un poste) | Plus rien à se rappeler | Un poste éteint est une sauvegarde manquée que personne ne voit ; la planification appartient à l'hôte | Écarté : la procédure est posée ici, la planification reste à faire sur l'hôte |

## Conséquences

- **Rien de tout ceci n'a été exécuté contre le cluster.** Le poste qui a écrit
  ces cibles n'avait ni démon Docker ni accès à 192.168.1.252 : `pg-backup` et
  `pg-restore` ont été relus et passés à `make -n`, la procédure Neo4j a été
  écrite depuis la documentation de `neo4j-admin` 5.x. L'identifiant 7474 de
  l'utilisateur `neo4j` de l'image et le comportement de son point d'entrée
  avec `neo4j-admin` sont à constater à la première exécution. **Le statut
  reste « Proposition » jusqu'à une restauration réussie de chaque base**, sur
  une instance jetable — une sauvegarde jamais restaurée est une hypothèse.
- **Rien n'est planifié.** Une sauvegarde manuelle est une sauvegarde qu'on
  oublie ; la prochaine étape est une tâche planifiée *sur l'hôte*, qui fait
  les deux dans la même fenêtre et pousse le résultat hors de l'hôte, avec une
  rétention.
- **Le graphe est indisponible pendant son dump.** Quelques secondes
  aujourd'hui ; cela croît avec le graphe. Si cela devient gênant, l'export
  APOC est la première alternative à rouvrir.
- **Un dump contient des données sensibles.** Il ne va ni dans le dépôt
  (`.gitignore`), ni dans un partage ouvert, ni dans un ticket.

## Références

- ADR liés : [0006](0006-neo4j-sur-le-cluster-docker.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0017](0017-documents-markdown-attaches-aux-elements.md),
  [0019](0019-recherche-semantique-sur-les-documents.md),
  [0026](0026-la-barriere-qualite.md)
- Code concerné : `Makefile` (`pg-backup`, `pg-restore`, `db-backup-howto`),
  `.gitignore`, `deploy/neo4j.stack.yml`, `deploy/postgres.stack.yml`
