# 6. Le graphe Neo4j tourne sur le cluster Docker, pas sur le poste

Date : 2026-09-07
Statut : Accepté
Supersédé en partie par : [`0024`](0024-tests-d-integration-sur-des-bases-jetables.md) — le volet « tests » ; le graphe applicatif reste sur le cluster.

Complète [`0004`](0004-neo4j-pour-le-graphe-d-architecture.md), qui a choisi
Neo4j sans dire où il tourne.

## Contexte

Jusqu'ici, `docker-compose.yml` déclarait un Neo4j démarré par `make db-up` sur
la machine de chaque développeur. Un poste, un graphe. Tant que le graphe était
vide, c'était sans conséquence ; il ne l'est plus.

Un référentiel d'architecture d'entreprise n'a d'intérêt que partagé. Le modèle
ArchiMate est la donnée du projet, pas un cache reconstructible : deux
développeurs qui modélisent chacun sur leur graphe produisent deux référentiels
qui ne se rejoindront jamais, et rien dans l'outillage ne signale la
divergence. Personne ne peut non plus regarder le modèle sans installer le
projet.

Un hôte Docker existe déjà sur le réseau (192.168.1.252), piloté par Portainer
(endpoint 9, Docker standalone). Il est allumé en permanence.

## Décision

**Le graphe est une instance unique, déployée sur le cluster Docker depuis
[`deploy/neo4j.stack.yml`](../../deploy/neo4j.stack.yml).** Aucun Neo4j ne
tourne en local : `docker-compose.yml` n'en déclare plus.

Conséquences dans l'outillage :

| Avant | Maintenant |
|---|---|
| `make db-up` démarrait le conteneur | rien à démarrer ; `make db-stack` rappelle comment déployer la stack |
| `make db-logs` | l'onglet du conteneur dans Portainer |
| — | `make db-ping` vérifie que le graphe répond |
| `make db-shell` via `docker compose exec` | `cypher-shell` dans un conteneur jetable, pointé sur le cluster |
| `make db-reset` supprimait les volumes | `MATCH (n) DETACH DELETE n` sur le graphe partagé, derrière `CONFIRM=yes` |
| `make db-up-all` | `make pg-up` (PostgreSQL reste local, aucun code ne s'y connecte) |

L'adresse `bolt://192.168.1.252:7687` devient la valeur par défaut de
`neo4j_uri` : un développeur sans `.env` atteint le graphe partagé plutôt qu'un
`localhost` qui ne répond pas. Ce n'est pas un secret, contrairement au mot de
passe, qui n'a toujours aucune valeur par défaut nulle part — ni dans le code,
ni dans `.env.example`, ni dans la stack, qui le déclare avec `:?` pour que le
déploiement échoue plutôt que d'inventer un mot de passe.

## Conséquences

**Ce que ça coûte.** Il faut être sur le réseau pour travailler sur le graphe :
plus de développement hors ligne. Une modélisation malheureuse est visible par
tout le monde immédiatement. Et surtout, `make test-integration` vide les
`:Element` du graphe partagé entre chaque cas : la cible le dit maintenant en
rouge, mais l'avertissement ne remplace pas une base dédiée. C'est la dette
principale ouverte par cette décision.

**Ce que ça règle.** Un seul modèle, consultable dans le navigateur Neo4j sans
rien installer, sauvegardable au même endroit que le reste du cluster, et qui
survit au `make clean` de n'importe qui.

## Options écartées

**Garder un Neo4j local en plus du cluster.** Deux bases, donc la question
« laquelle fait foi ? » à chaque instant, et l'oubli garanti d'un
`EA_NEO4J_URI` qui envoie un test destructeur sur la mauvaise. Le choix a été
de n'en avoir qu'une, quitte à en assumer la fragilité.

**Un second Neo4j sur le cluster, dédié aux tests.** C'est la vraie réponse au
problème d'isolation ci-dessus, et elle reste à faire. Elle n'a pas été prise
ici pour ne pas mélanger deux décisions : d'abord sortir le graphe du poste,
ensuite lui donner un jumeau de test.

**Neo4j en cluster (plusieurs instances).** L'URL de Portainer parle de
« cluster » au sens de l'hôte Docker, pas de Neo4j. Le clustering Neo4j exige
l'édition Enterprise ; l'édition Community sert une instance et une base, ce
qui suffit très largement au volume d'un référentiel d'architecture.
