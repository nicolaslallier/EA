---
titre: Les métadonnées des fichiers du bucket, dans PostgreSQL
date: 2026-09-16
statut: Proposition
affects: backend/src/ea/domain/files.py, backend/src/ea/domain/errors.py, backend/src/ea/domain/ports.py, backend/src/ea/db/models/file.py, backend/migrations/versions/0008_file_metadata.py, backend/src/ea/repositories/file_metadata_store.py, backend/src/ea/repositories/object_store.py, backend/src/ea/services/files.py, backend/src/ea/api/files.py, backend/src/ea/api/schemas.py, backend/src/ea/mcp/server.py, backend/src/ea/files_reconcile.py, backend/src/ea/main.py, frontend/src/features/files, Makefile, CLAUDE.md
---

# 39. Les métadonnées des fichiers du bucket, dans PostgreSQL

Date : 2026-09-16
Statut : Proposition

Complète [`0036`](0036-fichiers-dans-minio.md) : les octets restent dans MinIO,
et rien de ce que cet ADR ajoute ne les touche.

## Contexte

Depuis `docs/adr/0036`, un fichier de n'importe quel type se dépose dans le
bucket `ea-catalogue` et rien d'autre n'est su de lui. Ce que le SPA affiche
d'un fichier, c'est ce que S3 rapporte : un chemin, une taille, une date. Or
la question que pose une personne devant `rapport-q3.pdf` n'est aucune des
trois — c'est « qu'est-ce que c'est, qui l'a mis là, et pourquoi ». MinIO ne
sait répondre à rien de cela : un bucket ne connaît ni l'utilisateur Keycloak
qui a fait l'envoi, ni la phrase qu'il aurait écrite à côté.

Les métadonnées utilisateur de S3 (`x-amz-meta-*`) auraient pu porter une
partie de cela, et ne conviennent pas : elles sont figées à l'écriture — les
changer veut dire réécrire l'objet — elles ne se cherchent pas, et un fichier
de 50 Mo retraverserait le réseau pour qu'on corrige une faute dans sa
description.

PostgreSQL, lui, tient déjà tout le modèle (`docs/adr/0033`), il est fatal au
démarrage (`docs/adr/0037`), et il sait indexer, filtrer et joindre.

## Décision

**Une table `file_metadata`, une ligne par objet, `object_key` unique.** Elle
porte ce que le bucket ne sait pas — `uploaded_by_subject` et `uploaded_by`
(le sujet Keycloak et le nom lisible), `created_at` (quand ce catalogue a vu
le fichier pour la première fois), `title`, `description`, `tags`, et
`sha256` — et recopie ce qu'il sait : `byte_size`, `content_type`, `etag`,
`last_modified`. La recopie n'est pas une redondance gratuite : elle est ce
qui fait qu'un dossier de mille fichiers se rend avec **une** requête au lieu
de mille `stat`.

**MinIO dit ce qui existe, PostgreSQL dit ce qu'on en sait.** Le listage, le
téléchargement et la suppression vont au bucket ; un fichier qu'il détient est
un fichier, que PostgreSQL en ait entendu parler ou non. Un objet sans ligne
est donc listé **sans fiche** (`metadata: null`), jamais absent. C'est la
seule répartition qui laisse la porte du pipeline `alimenter-catalogue`
ouverte : il dépose dans `inbox/` sans rien savoir de cette table, et ce qu'il
y dépose reste visible.

**Une lecture n'écrit jamais.** Lister un dossier ne crée pas de lignes pour
ce qu'on y trouve. Le rattrapage est explicite — `POST /files/reconcile`, ou
`make files-reconcile` — et c'est lui qui donne une fiche aux fichiers arrivés
par une autre porte et qui retire les fiches dont l'objet a disparu.

**L'objet d'abord, la ligne ensuite.** Un arrêt entre les deux laisse un
fichier dans le bucket sans rien d'enregistré — exactement l'état d'un fichier
écrit par n'importe quelle autre porte, et exactement ce que le rapprochement
répare. L'ordre inverse laisserait une fiche décrivant un fichier qui n'a
jamais été stocké, que rien ne répare.

**Écraser un fichier n'efface pas ce qu'on a écrit sur lui.** Les deux écritures
du dépôt sont `INSERT ... ON CONFLICT DO UPDATE`, et leur liste `SET` ne
contient ni `title`, ni `description`, ni `tags`, ni `created_at`. Donner des
métadonnées au moment du dépôt est une seconde instruction, délibérée ; ne rien
donner garde ce qui était là. `describe` est le seul chemin qui les change, et
il remplace les trois ensemble — envoyer l'une est ce qui vide les deux autres,
parce qu'une fusion côté client serait une seconde réponse à « qu'est-ce qu'une
fiche ».

**`record` et `note_seen` sont deux méthodes et non un drapeau.** Un dépôt a lu
les octets : il a une empreinte et un appelant. Un rapprochement a un listage :
une taille et une date, et rien sur qui a écrit l'objet. Les confondre voudrait
dire qu'un rapprochement efface l'auteur de chaque fichier que l'API a stocké.

**`sha256` est nullable, et `NULL` veut dire « non calculée ».** Elle est connue
d'un fichier reçu par cette API, inconnue d'un fichier ramassé par un listage —
calculer l'empreinte là voudrait dire télécharger l'objet, et un rattrapage qui
lit chaque octet d'un bucket n'est pas un rattrapage. Une chaîne vide dirait
« calculée, et vide », ce qui est l'empreinte d'aucun octet et un autre fait.
Le rapprochement **abandonne** une empreinte que l'`etag` dit périmée : une
empreinte silencieusement fausse répond faux à la seule question qu'elle sert à
poser — « le même fichier sous un autre nom ? ».

**Aucune clé étrangère, et il ne peut pas y en avoir.** L'autre bout de la
relation est un magasin d'objets, que PostgreSQL n'a rien à référencer. C'est
la première table du dépôt dans ce cas, et c'est pourquoi le rapprochement
existe : il fait à la main ce qu'une cascade ferait toute seule.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Les métadonnées utilisateur de S3 (`x-amz-meta-*`) | Un seul magasin, rien à rapprocher | Figées à l'écriture (corriger une phrase réécrit 50 Mo), ni cherchables ni filtrables, et pas de place pour un sujet Keycloak | Écarté |
| PostgreSQL devient le catalogue : `GET /files` lit la table | Plus rapide, filtrable | Un fichier déposé dans `inbox/` par le pipeline est **invisible** jusqu'au prochain rapprochement — la porte que `docs/adr/0036` a ouverte exprès se referme | Écarté |
| Écrire les deux, ne jamais rapprocher | Le plus simple | Un fichier écrit hors de l'API n'a jamais de fiche, et une ligne dont l'objet a disparu reste pour toujours | Écarté |
| Un `element_id` vers le graphe, comme `element_documents` | Un PDF accroché à l'application qu'il documente | Un fichier du bucket n'est pas un document d'élément — `docs/adr/0017` couvre déjà ce besoin pour le markdown, et deux façons d'attacher un fichier à un élément sont deux vérités | Écarté pour l'instant |

## Conséquences

- **Une table de plus dans la sauvegarde**, et c'est tout : `pg_dump -Fc`
  l'emporte comme le reste (`docs/adr/0025`). Elle ne contient aucun octet de
  fichier — le bucket, lui, n'est toujours pas sauvegardé, et cet ADR ne change
  rien à cela.
- **`ObjectStore` gagne une méthode, `walk`.** C'est la seule lecture du port
  sans plafond : un `limit` y ferait silencieusement sauter la fin du bucket au
  rapprochement. `list_folder` garde le sien, parce qu'un listage est un dossier
  qu'une personne regarde.
- **`FileService` prend son dépôt de métadonnées en argument nommé sans valeur
  par défaut.** PostgreSQL étant fatal au démarrage, un `None` y est toujours
  un point de couture de test, et l'écrire au site d'appel est ce qui empêche
  un câblage oublié de devenir un catalogue qui n'enregistre rien.
- **Deux outils MCP de plus** — `describe_file` et `set_file_details` — et trois
  arguments de plus sur `upload_file`. Les `INSTRUCTIONS` disent à un modèle
  d'écrire une fiche quand il dépose un binaire : `rapport-q3.pdf` ne dit rien,
  une phrase à côté dit tout.
- **Une course assumée de plus, la même qu'en `docs/adr/0036`** : entre le `put`
  et le `record`, rien ne tient les deux ensemble. C'est précisément l'état que
  le rapprochement répare, donc elle ne mérite pas de mécanisme.
- **La suite d'intégration de cette table est écrite mais n'a pas encore tourné
  pour de vrai** — aucun Docker n'était disponible pendant ce travail, comme
  pour `docs/adr/0036`. C'est dû.

### À faire hors de ce repo

1. Appliquer la migration `0008` à la base partagée (`make pg-migrate`), puis
   lancer `make files-reconcile` une fois : le bucket contient déjà des objets,
   et aucun n'a de fiche.

## Références

- ADR liés : [0017](0017-documents-markdown-attaches-aux-elements.md),
  [0025](0025-sauvegardes-des-deux-bases.md),
  [0028](0028-pipelines-python-dans-le-depot-ea.md),
  [0033](0033-postgresql-seul-pour-le-graphe.md),
  [0036](0036-fichiers-dans-minio.md),
  [0037](0037-un-magasin-peripherique-degrade-ne-bloque-pas-le-demarrage.md)
- Code concerné : `backend/src/ea/domain/files.py`,
  `backend/src/ea/db/models/file.py`,
  `backend/src/ea/repositories/file_metadata_store.py`,
  `backend/src/ea/services/files.py`, `backend/src/ea/api/files.py`,
  `backend/src/ea/mcp/server.py`, `backend/src/ea/files_reconcile.py`,
  `frontend/src/features/files`
