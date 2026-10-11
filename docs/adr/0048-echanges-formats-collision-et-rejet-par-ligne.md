---
titre: Échanges — formats, export en instantané, identifiants uniques, collision et rejet par ligne
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/exchange.py, backend/src/ea/formats/archimate_xml.py, backend/src/ea/services/exchanges.py, backend/src/ea/repositories/exchange_store.py, backend/src/ea/api/exchanges.py
---

# 48. Échanges — formats, export en instantané, identifiants uniques, collision et rejet par ligne

Date : 2026-10-11
Statut : Proposition

## Contexte

Le catalogue doit s'ouvrir à Sparx EA, Archi et aux autres outils ArchiMate. Deux formats sont visés : le
*ArchiMate Model Exchange File Format* de l'Open Group (XML, espace de noms
`http://www.opengroup.org/xsd/archimate/3.0/`, commun à 3.0, 3.1 et 3.2) et, aux lots suivants, le CSV à la
disposition d'Archi. Un échange est une opération ponctuelle : pas d'historique, pas de passage par MinIO (le
bucket est la source de vérité des *fichiers* du catalogue, ADR 0036, pas un journal d'échanges).

Ce premier lot ne livre que l'**export XML du modèle entier** (`GET /exchanges/export`). L'import, le CSV, les
vues, les paquets, les outils MCP et l'écran viennent ensuite ; les décisions de ce document qui les concernent
sont posées ici pour que les lots suivants convergent, et seront confirmées ou corrigées quand elles seront
écrites.

Depuis la rédaction du plan, deux sous-projets ont changé le sol : le journal d'audit obligatoire sur toute
écriture (ADR 0044) et l'approbation et le verrouillage des éléments (ADR 0046).

## Décision

**L'export est en lecture seule.** Il n'écrit ni dans PostgreSQL ni dans MinIO : il n'a donc ni événement d'audit
ni garde d'approbation, et un lecteur peut l'appeler (`require_caller()` en première instruction de
`ExchangeService.export`, vérifié par `tests/unit/test_service_guards.py`). `PostgresExchangeRepository` n'ouvre
aucune écriture, ce que `tests/unit/test_audit_coverage.py` constate (aucun module de dépôt n'écrit sans audit).

**Un instantané cohérent.** L'export lit dans une seule transaction `REPEATABLE READ` en lecture seule,
ouverte avant la première page (`SELECT 1` y fixe l'instant), par pages de 1 000 lignes, `OFFSET/LIMIT` *dans*
l'instantané : une écriture validée entre deux pages est invisible. Ordre déterministe : éléments par (type, nom,
identifiant), relations par (type et nom de la source, de la cible, type de la relation, nom, identifiant, UUID).
Le coût O(n²/page) est accepté pour un catalogue de quelques dizaines de milliers de lignes (`# ponytail:` dans le
code). Le temps est borné à 300 s : au-delà, `TimeoutError`, la réponse est tronquée et l'incident est journalisé.

**Génération en flux.** `render_model` produit des morceaux de texte, `buffered` les groupe en UTF-8 sans jamais
couper un caractère : le modèle n'est jamais en mémoire en entier (seule la carte UUID → identifiant l'est,
bornée par la taille du catalogue). Aucune bibliothèque d'écriture XML : des chaînes échappées par un seul point,
`_Writer`.

**Le XML sortant est sûr pour n'importe quel texte.** `&`, `<`, `>` sont échappés partout (donc `]]>` aussi) ;
dans un attribut, `"`, les retours à la ligne et la tabulation le sont en références numériques, car un analyseur
les normaliserait en espace ; `\r` l'est aussi dans le texte. Les caractères que XML 1.0 interdit (U+0000-U+001F
sauf tabulation, LF, CR ; U+FFFE et U+FFFF ; les demi-paires de substitution isolées) sont **retirés et comptés**
— jamais en silence : le service journalise un avertissement `action=export_adjusted` avec le nombre de
caractères retirés et les identifiants des objets touchés, jamais le texte.

**Les identifiants d'un fichier exporté sont uniques par construction** (`IdentifierRegistry`). Un `xs:ID` est
unique dans le fichier et `source`/`target` d'une relation y renvoient par nom : deux objets sous le même
identifiant feraient pointer un lien vers le mauvais objet. Le `source_id` d'un objet n'est donc utilisé que s'il
est un nom XML sûr (NCName ASCII), n'a pas la forme d'un identifiant généré `id-<uuid>` (sauf si c'est celui de
l'objet lui-même), n'est ni l'identifiant du modèle ni préfixé `propid-`, et n'a pas déjà été attribué. Sinon
l'objet reçoit `id-<uuid>`, qui ne peut pas entrer en collision (un UUID est unique et aucun autre objet ne peut
prendre cette forme), et un avertissement est émis. Le premier arrivé dans l'ordre déterministe garde le
`source_id`. Une seule passe, une consultation d'ensemble par objet. Un identifiant Archi (`id-5f3a…`) reste donc
intact ; un identifiant venu d'un autre outil mais qui n'est pas un NCName (`1`, `a b`) est remplacé à l'export,
et son `source_id` d'origine ne fait plus l'aller-retour.

**Tout motif de validation du module d'échange est ancré `\A…\Z`** (jamais `$`, qui accepte un retour à la ligne
final, ni `re.match` seul). `tests/unit/test_exchange_anchors.py` lit les sources de `domain/exchange.py` et de
`formats/` et échoue sur un motif non ancré. **Cette règle vaut pour l'analyseur d'import du lot 2.**

**La correspondance de noms est dérivée du métamodèle.** `snake_case` → `PascalCase` pour les 60 types
modélisables et les 11 relations (`business_actor` → `BusinessActor`), construite en parcourant `ElementType` et
`RelationshipType` : un type ajouté au métamodèle est exporté sans autre geste, et les tests paramétrés sur le
métamodèle échouent si un type n'a pas de correspondance. `access_type` ↔ `Access|Read|Write|ReadWrite`.

**Ce qui n'est pas exporté.** L'état d'approbation, la révision, le verrou, `approved_by`, `approved_at` et
`last_modified_by_sub` n'ont pas d'équivalent dans Exchange : ils ne sortent pas, et un fichier réimporté ne
porte donc ni approbation ni verrou (un élément importé est un brouillon). Ces colonnes sont lues avec la ligne
mais l'export ne prend que `properties`, qui ne contient que ce qu'une personne a déclaré : aucune colonne interne
ne fuit dans les `<property>`. `source_id` n'est jamais exporté comme propriété mais comme `identifier` ;
la description voyage comme propriété déclarée `ea.description` (Exchange n'a pas de champ « courte description »).
Les relations d'accès, d'association orientée et d'influence portent leurs attributs natifs (`accessType`,
`isDirected`, `modifier`).

**Les jonctions ne sont pas des éléments du catalogue** : `Element.create` refuse un connecteur, aucune jonction
n'existe en base, donc l'export n'en produit pas. À l'import (lot 2) une jonction du fichier sera ignorée avec
un message d'information et les relations qui la touchent rejetées. Une décision future autorisera les jonctions ;
elle est hors de ce sous-projet.

**Le nom du fichier téléchargé est fabriqué par le serveur** : `ea-<périmètre>-<AAAA-MM-JJ>.xml`, à partir de mots
fixes et de la date de l'horloge injectée, jamais d'une entrée ; l'en-tête est celui de `attachment()` (RFC 6266),
avec `X-Content-Type-Options: nosniff` et `Content-Type: application/xml`. Un périmètre inconnu (ou une valeur de
paquet/requête que ce lot ne sert pas) est un `422 unknown_scope` typé, levé avant le premier octet.

**Décisions annoncées pour les lots suivants** (non implémentées ici) : l'import passe par `ArchitectureService`
(donc toutes les règles ArchiMate, IPAM, d'audit et d'approbation) dans une transaction, un `SAVEPOINT` par
ligne et un verrou consultatif, `dry_run` vrai par défaut ; clé de collision `(type, nom)`, un élément existant
est réutilisé sans que ses propriétés soient jamais écrasées ; un refus de ligne est une ligne de rapport, sauf
avec `strict` ; plafond de 20 Mo coupé pendant la réception.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Utiliser le `source_id` tel quel, normalisé | aller-retour exact des identifiants | deux objets peuvent obtenir le même `xs:ID` ; un lien pointe vers le mauvais | écarté |
| Dédoublonner par un suffixe `-2`, `-3` | garde une trace du `source_id` | boucle à bornes incertaines, identifiants instables d'un export à l'autre | écarté |
| Un curseur à clé composée plutôt que `OFFSET` | linéaire | SQL plus lourd, même cohérence | écarté tant que le catalogue reste petit |
| Construire un arbre XML (`ElementTree`) | échappement fourni | tient le modèle en mémoire | écarté |
| Retirer les caractères interdits sans rien dire | simple | corruption invisible d'un nom | écarté |
| Exporter l'état d'approbation en propriétés `ea.*` | aller-retour de la revue | une approbation importée sans lecteur n'a aucun sens (ADR 0046 : on approuve ce qu'on a lu) | écarté |
| Historique des échanges dans MinIO | trace | bucket qui grossit sans lecteur | écarté |

## Conséquences

- La conformité de l'export au XSD de l'Open Group n'a **pas** été vérifiée : le schéma n'est pas dans le dépôt
  (ADR 0049). Elle est vérifiée par des contrôles structurels écrits à la main, qui s'exécutent toujours, et par un
  test XSD qui se saute sans `EA_EXCHANGE_XSD`.
- La compatibilité réelle avec Sparx EA et Archi n'est pas testée : à vérifier à la main une fois, résultat à
  consigner ici.
- Un export de plus de 300 s est tronqué (200 déjà parti) : le fichier n'est alors pas bien formé, ce que tout
  lecteur rejettera. Le plafond de volume (50 000 éléments et relations) n'est pas appliqué à l'export ; il le sera
  à l'import.
- Une valeur de `modifier` qui n'est pas un modificateur Exchange valide est exportée telle quelle.
- L'export ne verrouille rien : il n'empêche pas une écriture concurrente, il l'ignore.

## L'import est synchrone : délai du proxy, déconnexion, rejeu

`POST /exchanges/imports` répond quand l'import est fini, sans tâche de fond. Un fichier de 50 000 lignes dure
plusieurs minutes, au-delà du `proxy_read_timeout` de 60 s du NGINX de l'Infra : le client reçoit alors un **504**
alors que l'import continue. C'est supportable parce que l'import est **idempotent par construction** : un élément
de même (type, nom) est réutilisé, une relation de même (type, source, cible) aussi. Rejouer le même fichier ne
crée aucun doublon, et le rapport du rejeu (`created: 0`, `reused: n`) dit que le premier est passé.

- **Quand le client se déconnecte, l'import continue et valide.** Mesuré contre un uvicorn réel
  (`tests/unit/test_exchange_disconnect.py`) : le client coupe la connexion pendant que le serveur lit le fichier,
  la tâche de la requête n'est pas annulée, l'import va à son terme et sa transaction est validée. Il n'y a pas
  de retour arrière à la déconnexion ; seule une erreur (verrou perdu, 409 rejouable) annule tout. Un client qui a
  reçu un 504 ne sait donc pas si l'import est passé : il rejoue, ou il ouvre le journal d'audit.
- **Estimer d'abord** : `dry_run=true`, la valeur par défaut, exécute les mêmes écritures puis annule ; sa durée
  (`duration_ms` du rapport) est celle de l'import réel, à la contention près. C'est le chemin recommandé avant
  d'écrire. Le rapport, lui, est borné (1 000 lignes par catégorie, comptes exacts, `issue_count`).
- **À faire hors de ce repo** (Infra, `nginx/conf.d/ea.conf`), rien n'est créé ici : pour
  `location = /api/exchanges/imports`, `proxy_read_timeout 600s;` et `client_max_body_size 21m;` (20 Mo de fichier
  et l'enveloppe multipart ; la `location /api/` porte déjà `51m`, que cette ligne resserre). Aucun fichier de ce
  dépôt ne définit `client_max_body_size` ni `proxy_read_timeout`.
- **Le plafond d'octets est coupé pendant la réception** (`api/body_limit.py`), avant que Starlette ne mette le
  corps multipart en tampon : un `Content-Length` annoncé trop grand est refusé sans lire un octet, un corps
  `chunked` est compté au fil de l'eau. `EA_EXCHANGE_MAX_BYTES` (1 Kio à 100 Mo, 20 Mo par défaut) est lu une fois
  par `Settings` ; le middleware n'est posé que sur cette route et est le plus intérieur de la pile, de sorte que
  son 413 porte les en-têtes CORS et `X-Request-Id`.
- **Un seul fichier est analysé à la fois** par processus, avec une file d'attente d'un (un troisième reçoit 409
  `import_in_progress`) : un fichier de 20 Mo coûte quelques centaines de Mo pendant l'analyse. Le lecteur est
  un flux borné en mémoire (nœuds par objet, attributs par nœud, jetons sans fin) ; l'analyse dans son thread
  n'est pas annulable, au plus quelques secondes.
- **Limites connues** : le catalogue entier est chargé en mémoire au début de l'import (ordre de grandeur : le
  nombre d'éléments, quelques centaines de Mo au pire pour des centaines de milliers d'éléments) ; une adresse
  ou un préfixe en doublon dans le fichier est refusé au plan, et si le premier porteur est ensuite refusé par la
  base, ses suivants restent refusés (conservateur).

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0017](0017-documents-markdown-attaches-aux-elements.md), [0031](0031-diagrammes-enregistres.md), [0033](0033-postgresql-seul-pour-le-graphe.md), [0036](0036-fichiers-dans-minio.md), [0044](0044-journal-d-audit-append-only.md), [0046](0046-approbation-et-verrouillage-des-elements.md), [0049](0049-defusedxml-et-validation-xsd.md)
- Code concerné : `backend/src/ea/domain/exchange.py`, `backend/src/ea/formats/archimate_xml.py`, `backend/src/ea/services/exchanges.py`, `backend/src/ea/repositories/exchange_store.py`, `backend/src/ea/api/exchanges.py`
