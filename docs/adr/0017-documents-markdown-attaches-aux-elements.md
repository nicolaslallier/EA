---
titre: Les documents markdown attachés aux éléments — la première table du socle relationnel
date: 2026-09-07
statut: Accepté
affects: backend/src/ea/domain/documents.py, backend/src/ea/db/models/, backend/src/ea/repositories/document_store.py, backend/src/ea/services/documents.py, backend/src/ea/api/documents.py, backend/migrations/versions/0002_element_documents.py, backend/src/ea/core/config.py, frontend/src/features/documents/
---

# 17. Les documents markdown attachés aux éléments

Date : 2026-09-07
Statut : Accepté

Réalise ce que [`0015`](0015-socle-postgresql-sqlalchemy-alembic.md) avait
préparé : la première table du socle relationnel, et le basculement de
`EA_POSTGRES_ENABLED` qu'il annonçait.

## Contexte

Un élément d'architecture porte déjà un champ `documentation` : un paragraphe
saisi dans un formulaire. Ce n'est pas ce dont un modélisateur a besoin quand
la vraie documentation existe déjà — une procédure d'exploitation, un contrat
d'interface, une note de décision — écrite en markdown, versionnée ailleurs,
et qu'il veut simplement rattacher à l'élément qu'elle décrit. Recopier ce
texte dans un champ de formulaire, c'est en perdre le nom, l'unité et la
possibilité de le remplacer par sa version suivante.

Deux questions se posent en même temps, et c'est ce qui fait la décision.
**Où stocker un fichier**, alors que l'élément qu'il décrit est un nœud Neo4j ?
Et **sous quelle forme**, alors qu'un fichier téléversé est, à ce stade, une
suite d'octets dont personne n'a vérifié qu'elle est du texte ?

## Décision

**Un document est du markdown, stocké comme du texte, dans PostgreSQL, et
rattaché à un élément par son identifiant.**

| Point | Choix |
|---|---|
| Emplacement | PostgreSQL, table `element_documents` — première table du socle |
| Forme | Colonne `TEXT`, jamais `bytea` |
| Lien vers l'élément | Une colonne `element_id`, **sans clé étrangère** |
| Unicité | `(element_id, filename)` — un nom de fichier par élément |
| Transport | `multipart/form-data`, la seule route de cette API qui ne soit pas du JSON |
| Taille | 1 Mo, mesurée en octets |

### Le markdown est du texte, et le refus est en amont

`TEXT` plutôt que `bytea` parce que le markdown est de la prose : on la lit, on
la cherche, on la compare. Une colonne binaire ferait de chacune de ces
opérations un décodage, et laisserait entrer un fichier qui n'est pas du texte
du tout.

Ce choix a un prix immédiat, et il est payé dans `domain/documents.py` : un
fichier qui n'est pas de l'UTF-8, ou qui contient un octet NUL, est refusé à
l'entrée. PostgreSQL ne sait pas stocker un NUL dans une colonne `TEXT` — sans
cette règle, un binaire renommé `.md` n'échouerait qu'à l'`INSERT`, sous la
forme d'une erreur de pilote, dans un journal, longtemps après l'envoi. Le
marqueur d'ordre des octets d'un éditeur Windows est retiré pour la même
raison : il deviendrait sinon un caractère parasite en tête de chaque document.

Le nom du fichier est validé aussi : il doit finir par `.md` ou `.markdown`, et
ne peut pas porter de séparateur de chemin. Le contrôle porte sur le **nom** et
non sur le `Content-Type`, parce que les navigateurs annoncent le markdown
tantôt `text/markdown`, tantôt `text/x-markdown`, tantôt
`application/octet-stream` : s'y fier reviendrait à refuser des fichiers au
hasard.

### Il n'y a pas de clé étrangère, et la cascade est du code

L'élément est un nœud `:Element` dans Neo4j ([`0004`](0004-neo4j-pour-le-graphe-d-architecture.md)) ;
PostgreSQL n'a rien à référencer. Les deux moitiés de ce qu'une clé étrangère
aurait donné gratuitement sont donc écrites :

- **« l'élément doit exister »** est vérifié par `DocumentService`, qui demande
  l'élément au service d'architecture avant d'écrire quoi que ce soit ;
- **« supprimer l'élément supprime ses documents »** est fait par
  `ArchitectureService.delete_element`, à travers un port volontairement étroit,
  `ElementAttachments`, qui ne déclare que `discard_for_element`. Le service du
  graphe ne connaît donc pas le dépôt de documents : il connaît une cascade.

Il n'y a pas de transaction entre les deux bases. Le graphe est supprimé
d'abord : une panne entre les deux laisse des lignes que personne ne peut
atteindre — invisibles, et jamais héritées par un autre élément puisque les
identifiants sont aléatoires. L'ordre inverse laisserait un élément dont la
documentation aurait discrètement disparu, ce qui est pire.

### Lister n'est pas lire

Deux modèles de lecture, et c'est délibéré. `DocumentSummaryRead` donne le nom,
la taille et les dates ; `DocumentRead` ajoute le texte. Un client qui
téléchargerait dix corps d'un mégaoctet pour afficher dix noms de fichiers est
exactement l'erreur que la séparation rend impossible — un `content` parfois
absent l'aurait cachée derrière un champ optionnel. La taille est calculée par
le serveur (`octet_length`) plutôt que stockée : dérivée, elle ne peut pas
diverger du texte qu'elle mesure.

### Le dépôt prend la fabrique de sessions

`PostgresDocumentRepository` reçoit l'`async_sessionmaker`, comme son voisin
Neo4j reçoit le pilote : construit une fois pour le processus, il ouvre une
unité de travail par appel. Chaque cas d'usage ici est une écriture unique,
donc une session par appel *est* une transaction par cas d'usage — la règle de
`CLAUDE.md`. Le jour où un cas d'usage en couvre deux, il prendra une
`AsyncSession` en argument et `api.dependencies.get_session` deviendra la
couture prévue pour ça.

### `EA_POSTGRES_ENABLED` passe à `true`

C'est la conséquence que [`0015`](0015-socle-postgresql-sqlalchemy-alembic.md)
annonçait : à partir de la première table, un déploiement sans PostgreSQL est
une erreur de configuration, et l'API le dit au démarrage plutôt qu'au premier
envoi de fichier — la même règle que pour le graphe.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker le markdown dans une propriété du nœud Neo4j | Aucune seconde base, aucune cascade à écrire | Le graphe sert à traverser des relations, pas à porter des mégaoctets de texte ; chaque parcours ramènerait les documents | Écarté |
| Une colonne `bytea` | Accepte n'importe quel fichier, sans règle à écrire | Chercher, lire et comparer deviennent des décodages, et « n'importe quel fichier » n'est pas ce qu'on veut attacher | Écarté |
| Un stockage objet (S3, disque) avec un chemin en base | Tient des fichiers de toute taille et de tout type | Une seconde infrastructure, des URL signées, une sauvegarde de plus — pour 1 Mo de texte que la base tient très bien | Écarté, à rouvrir le jour où on attachera des PDF ou des images |
| Remplacer silencieusement un document du même nom | Un envoi ne peut jamais échouer | Écrase sans le dire le fichier d'un autre ; le catalogue refuse déjà un nom d'élément dupliqué, la cohérence vaut mieux | Écarté — c'est un 409, et le SPA reconnaît le cas avant d'envoyer |
| Rendre le markdown en HTML dans le SPA | Plus agréable à lire | Un analyseur, un assainisseur, et un ADR pour les deux ; une procédure lue en texte brut reste une procédure lue | Écarté pour l'instant |
| Exposer les documents comme outils MCP | Un agent lirait la documentation attachée | Téléverser un fichier n'est pas une forme MCP naturelle, et la lecture seule est un ajout séparé qui ne dépend pas de celui-ci | Reporté, volontairement hors de ce changement |

## Conséquences

- **`python-multipart` devient une dépendance.** Starlette n'analyse un
  `multipart/form-data` que s'il est installé ; sans lui, la route d'envoi
  répond 500 au lieu d'accepter un fichier.
- **Deux stockages peuvent diverger.** Aucune transaction ne les couvre. Le
  scénario admis est celui de lignes orphelines après une panne entre les deux
  suppressions ; il est invisible pour l'utilisateur, mais une requête de
  ménage (`element_documents` dont l'`element_id` n'est plus dans le graphe)
  sera utile le jour où quelqu'un voudra compter.
- **`EA_POSTGRES_ENABLED=true` par défaut.** Un poste hors de portée du cluster
  ne démarre plus l'API sans surcharger le réglage. C'est le comportement
  voulu, et c'est un changement pour qui travaillait jusqu'ici sans PostgreSQL.
- **La limite d'un mégaoctet est arbitraire.** Elle tient dans une colonne, dans
  un corps de requête et dans la mémoire du processus. Le jour où elle gêne, ce
  sera le signe qu'on n'attache plus de la documentation mais des exports — et
  la ligne du tableau ci-dessus sur le stockage objet se rouvrira.
- **Le SPA reconnaît le doublon avant de l'envoyer.** Il a déjà la liste, donc
  un fichier dont le nom y figure part en `PUT` plutôt qu'en `POST`. Si la
  liste est périmée, l'API répond 409 et le panneau l'affiche : la règle reste
  celle du serveur.
- **À surveiller :** la première fois qu'un élément portera des dizaines de
  documents, l'ordre de la liste (le plus ancien d'abord) et l'absence de
  pagination se remarqueront.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0011](0011-detail-d-un-element-dans-le-catalogue.md)
- Code concerné : `backend/src/ea/domain/documents.py`,
  `backend/src/ea/services/documents.py`,
  `backend/src/ea/repositories/document_store.py`,
  `backend/src/ea/db/models/document.py`,
  `backend/src/ea/api/documents.py`,
  `frontend/src/features/documents/`
