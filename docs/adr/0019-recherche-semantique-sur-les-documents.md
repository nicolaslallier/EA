---
titre: Recherche sémantique sur les documents — découpage par titres, vecteurs dans PostgreSQL
date: 2026-09-08
statut: Accepté
affects: backend/src/ea/domain/chunking.py, backend/src/ea/domain/search.py, backend/src/ea/domain/ports.py, backend/src/ea/db/models/chunk.py, backend/src/ea/repositories/document_store.py, backend/src/ea/repositories/embeddings.py, backend/src/ea/services/indexing.py, backend/src/ea/services/documents.py, backend/src/ea/mcp/server.py, backend/src/ea/reindex.py, backend/migrations/versions/0003_document_chunks.py, backend/src/ea/core/config.py, docker-compose.yml, Makefile
---

# 19. Recherche sémantique sur les documents

Date : 2026-09-08
Statut : Accepté

Prolonge [`0017`](0017-documents-markdown-attaches-aux-elements.md), qui a
attaché des fichiers markdown aux éléments, et
[`0018`](0018-documents-exposes-aux-agents-via-mcp.md), qui les a offerts aux
agents. Un fichier était devenu lisible ; il n'était pas encore *trouvable*.

## Contexte

`0018` a donné à un agent `list_documents` et `read_document`. Cela suffit
quand on sait déjà où regarder. Cela ne suffit pas pour la question qu'on pose
réellement à un référentiel d'architecture — « où est-il écrit ce qui se passe
si la facturation tombe ? » — et à laquelle on ne peut répondre aujourd'hui
qu'en lisant tous les documents l'un après l'autre. Un document pèse jusqu'à un
mégaoctet ; dix documents, c'est une fenêtre de contexte entière dépensée pour
retrouver un paragraphe.

Trois choses rendent le problème plus étroit qu'il n'y paraît, et donc
soluble.

**Le corpus est du markdown écrit par des humains**, structuré par ses titres.
L'auteur a déjà découpé son texte ; il n'y a pas à deviner où couper.

**Le corpus est petit et il est déjà dans PostgreSQL.** Quelques milliers de
passages, à côté des documents dont ils sont issus. Introduire un moteur de
recherche vectorielle séparé, ce serait un troisième magasin, une troisième
cohérence à tenir, pour un volume qu'une extension PostgreSQL traite sans
transpirer.

**Un passage isolé ne dit pas de quoi il parle.** « Relancer le conteneur puis
vérifier `/health` » est une phrase sans sujet. Ce qui la qualifie — le nom du
fichier, la section, la sous-section — est justement ce qui *n'est pas* dans
son texte. C'est la tension centrale de cet ADR.

## Décision

Une recherche sémantique sur les *passages* des documents, indexés dans
PostgreSQL avec pgvector, et interrogeables par un agent via un vingtième outil
MCP.

### 1. On découpe aux titres, et on ré-injecte le fil des titres

`domain/chunking.py` coupe un document à ses titres ATX : une section — un
titre et le texte en dessous, jusqu'au titre suivant quel que soit son niveau —
devient un passage. Le texte réellement envoyé au modèle d'embedding n'est pas
le passage seul, mais **le fil des titres puis le passage** :

```
runbook-facturation.md > Incidents > Escalade

Si le service ne repart pas après deux tentatives, appeler l'astreinte de niveau 2.
```

Le nom du fichier est la racine du fil : c'est le seul titre que tout document
possède. Le texte *stocké*, lui, est le passage sans le fil — le fil est du
contexte pour le modèle, et le redire dans le passage rendu au lecteur serait
le dire deux fois. Les deux sortent de la même fonction (`heading_trail`), donc
un résultat affiche son fil exactement tel qu'il a été indexé.

C'est la décision qui donne son nom à cet ADR, et elle a été mesurée. Sur cinq
questions en français contre cinq passages de prose d'exploitation, avec le fil
des titres :

| Modèle | top-1 | MRR |
|---|---|---|
| `text-embedding-mxbai-embed-large-v1` | 4/5 | 0,850 |
| `text-embedding-nomic-embed-text-v1.5` | 2/5 | 0,617 |

Deux détails de découpage sont des règles et non des réglages. Les blocs de
code délimités sont respectés : `# redémarrer le service` est la première ligne
d'un exemple shell sur deux, et la lire comme un titre couperait une procédure
au milieu de la commande qu'elle donne. Et le *front matter* YAML n'est pas
indexé : un résultat sur `owner: nicolas` est un résultat sur un champ de
formulaire, pas sur de la prose.

Une section plus longue qu'une fenêtre (1800 caractères) est découpée en
fenêtres qui se recouvrent de 200 caractères, sur les frontières de
paragraphes ; toutes gardent le fil de titres de leur section.

### 2. pgvector, dans la base qui tient déjà les documents

`document_chunks` est la deuxième table du socle relationnel, et **la première
qui peut porter une clé étrangère**. `element_documents.element_id` désigne un
nœud Neo4j et ne référence rien — d'où la cascade écrite à la main dans
`ArchitectureService.delete_element` (`0017`). Un passage, lui, désigne un
document : une ligne d'à côté. Sa cascade est une ligne de DDL, et supprimer un
élément déclenche les deux à la suite.

Deux colonnes portent une décision.

`embedding` est un `vector(1024)`, de largeur fixe, indexé en HNSW avec
`vector_cosine_ops` — la même distance que celle par laquelle le dépôt trie. Un
index construit pour une autre classe d'opérateurs n'échoue pas : il n'est
simplement jamais utilisé, et la recherche devient un parcours complet sans
rien en dire. La largeur est `EMBEDDING_DIMENSIONS`, importée par le modèle, la
migration et les réglages, jamais retapée. **Ce n'est pas une variable
d'environnement** : en changer est une migration plus un réindex complet.

`model` est stocké à côté de chaque vecteur, et chaque recherche filtre dessus.
La distance cosinus entre deux vecteurs de deux modèles différents est un
nombre qui ne veut rien dire. Un corpus à moitié réindexé ne doit donc pas
renvoyer des résultats un peu moins bons : il renvoie *trop peu*, visiblement,
jusqu'à ce que `make docs-reindex` soit passé.

Un document et ses passages sont écrits **dans une seule transaction** — ce qui
est possible ici et nulle part ailleurs dans ce dépôt, puisque les deux tables
sont dans la même base. L'appel au service d'embedding a lieu *avant* que la
transaction s'ouvre : tenir une transaction ouverte pendant un appel réseau à
une autre machine, c'est transformer un service lent en table verrouillée.

### 3. LM Studio du cluster, derrière une forme d'API et non un fournisseur

`repositories/embeddings.py` parle `/v1/embeddings`, la forme OpenAI. LM Studio
la sert, sur la même machine que les deux bases (192.168.1.252:1234), et Ollama,
text-embeddings-inference et les fournisseurs hébergés la servent aussi :
changer de fournisseur est une URL de base et un nom de modèle, jamais un
second client. Le texte des documents ne sort pas du réseau.

Le port `Embedder` a **deux** méthodes, pas une : plusieurs familles de modèles
sont entraînées avec une instruction différente sur un passage stocké et sur
une question. `mxbai` en veut une sur la question et aucune sur le passage ; la
famille e5 veut `passage: ` et `query: ` ; `bge-m3` n'en veut aucune. Les
inverser ne se voit nulle part et coûte du rappel, ce qui est précisément
pourquoi c'est un choix que quelqu'un doit poser, dans `.env`.

Le démarrage interroge le service, comme il interroge les deux bases, plus une
vérification que lui seul peut faire : que le modèle configuré répond bien des
vecteurs de la largeur de la colonne. Sans elle, l'erreur arriverait à
l'`INSERT`, sous forme d'erreur de driver, dans un log, après que le fichier a
été accepté.

### 4. Un outil MCP, pas d'endpoint REST ni d'écran

`search_documents` est le vingtième outil : une question, un `element_id`
facultatif, et des passages en réponse — chacun avec son fil de titres et l'id
de l'élément qui le porte. C'est là que l'index paie le plus : `/mcp` existe
déjà, et un agent qui lisait dix documents pour vérifier une phrase en lit
maintenant un.

Il n'y a **pas** d'endpoint REST ni de section dans le SPA. `PassageRead` vit
tout de même dans `api/schemas.py`, avec les autres modèles de lecture : c'est
la forme qu'a un passage, et l'y garder est ce qui empêche l'adaptateur MCP de
se fabriquer un rendu privé de la même chose le jour où l'endpoint arrivera.

Un passage nomme son élément par id et non par nom. Les noms vivent dans le
graphe, et décorer une requête relationnelle d'un appel à Neo4j ferait payer à
chaque recherche un champ que l'appelant ne veut pas forcément — `get_element`
est à un appel de là quand il le veut.

### 5. `make docs-reindex`, parce qu'un index peut prendre du retard

Un index écrit à chaque envoi peut malgré tout diverger de son magasin, de deux
façons, et les deux sont de la configuration : un document attaché pendant que
`EA_EMBEDDINGS_ENABLED` était à `false`, et un corpus dont le modèle a changé.
`python -m ea.reindex` recoupe et réembarque tout, document par document. Ce
n'est volontairement pas une transaction sur tout le corpus : interrompu à
mi-chemin, il laisse la moitié du corpus réindexée, ce qu'un second passage
répare.

## Conséquences

- **pgvector devient une exigence de l'image PostgreSQL**, pas seulement de la
  base : la migration `0003` fait `CREATE EXTENSION vector`, qui échoue sur une
  image qui ne le porte pas. Le conteneur jetable de `docker-compose.yml` passe
  à `pgvector/pgvector:pg17` ; **le stack du cluster doit faire de même**, et
  `make pg-vector-check` le dit avant que la migration ne le découvre.
- `EA_EMBEDDINGS_ENABLED` est à `true` par défaut, comme les deux bases : une
  API qui refuse de démarrer vaut mieux qu'une recherche qui ne renvoie
  silencieusement rien. À `false`, les documents sont stockés et servis
  normalement, et `search_documents` répond une phrase qui dit pourquoi.
- Deux dépendances : `pgvector` (le type de colonne et les opérateurs) et
  `httpx` en production — c'est le seul appel sortant que ce processus fait.
- Un envoi de document coûte maintenant un appel réseau au service d'embedding.
  Il est fait hors transaction, et le tout du document part en un seul appel.
- La largeur des vecteurs est gelée à 1024 par la colonne. Passer à un modèle
  d'une autre largeur est une migration et un réindex complet — c'est le prix
  d'un index qui ne peut pas mentir sur ce qu'il compare.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Un vecteur par document | Simple, une ligne par fichier | Un mégaoctet de prose moyenné donne un vecteur qui ne ressemble à rien, et la réponse est un nom de fichier | Écarté : c'est le problème, pas la solution |
| N'indexer que le titre / front matter | Minuscule, rapide | On ne retrouve un document que par son titre ; le corps n'est jamais cherchable | Écarté |
| Découper à taille fixe, sans les titres | Trivial à écrire | Perd exactement l'information qui qualifie un passage — voir la mesure ci-dessus | Écarté |
| Un moteur vectoriel dédié (Qdrant, Weaviate…) | Fait pour ça, scale loin | Un troisième magasin, une troisième cohérence, pour quelques milliers de passages déjà dans PostgreSQL | Écarté : à reconsidérer si le corpus change d'ordre de grandeur |
| Recherche plein texte PostgreSQL (`tsvector`) | Aucune dépendance, aucun service | Ne répond pas à une question posée autrement que le texte ne l'écrit ; c'est un complément, pas un substitut | Écarté pour l'instant ; un index hybride reste ouvert |
| Un fournisseur hébergé (Voyage, OpenAI) | Meilleure qualité multilingue | Le texte des documents quitte le réseau, et il faut une clé | Écarté : le cluster sert déjà les deux bases, LM Studio y est |
| `nomic-embed-text-v1.5` | Déjà chargé, matriochka | 2/5 contre 4/5 sur la mesure en français ci-dessus, et 768 dimensions | Écarté |
| Indexer de façon asynchrone, après la réponse | L'envoi ne dépend pas du service | Le document est cherchable « bientôt », et il faut une file — pour un corpus de cette taille, c'est de la machinerie sans contrepartie | Écarté |
| Stocker `heading_path` en texte joint | Une colonne de moins | Un titre a le droit de contenir le séparateur ; un chemin qu'on ne sait plus redécouper est un chemin perdu | Écarté : `text[]` |
