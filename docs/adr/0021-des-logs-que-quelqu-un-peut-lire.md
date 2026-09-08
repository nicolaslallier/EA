---
titre: Des logs que quelqu'un peut lire — un socle, un identifiant de requête, trois robinets
date: 2026-09-08
statut: Accepté
affects: backend/src/ea/core/logging.py, backend/src/ea/core/config.py, backend/src/ea/api/middleware.py, backend/src/ea/main.py, backend/src/ea/mcp/errors.py, backend/src/ea/services/, backend/src/ea/repositories/, frontend/src/lib/logging.ts, frontend/src/lib/api.ts
---

# 21. Des logs que quelqu'un peut lire

Date : 2026-09-08
Statut : Accepté

## Contexte

Jusqu'ici **rien ne configurait le logging**. Six modules appelaient
`logging.getLogger(__name__)`, uvicorn configurait ses trois loggers à lui, et
tout ce que notre code écrivait retombait sur un logger racine sans handler :
`logger.info("element rejected by a uniqueness constraint")` n'apparaissait
nulle part, à aucun niveau. Les lignes existaient ; le lecteur, non.

Ce qui manquait, concrètement, quand quelque chose se passe mal :

- **Au démarrage**, on ne savait pas ce que le processus avait ouvert. Neo4j,
  PostgreSQL et le service d'embeddings sont trois machines distinctes sur le
  cluster ; un démarrage réussi ne le disait pas, et un démarrage raté le
  disait par une exception.
- **Par requête**, la seule trace était la ligne d'accès d'uvicorn : ni durée,
  ni identifiant, donc aucun moyen de rattacher les douze lignes qu'un
  traitement écrit à la requête qui les a provoquées.
- **Par écriture**, rien. `POST /elements -> 201` dit qu'un élément a été créé,
  pas *lequel* — et `/mcp` atteint les mêmes cas d'usage sans passer par HTTP.
- **Côté agent**, rien non plus. `/mcp` écrit dans le graphe et, tant que l'auth
  n'existe pas, n'authentifie personne (`docs/adr/0014`) : un appel d'outil
  sans trace est la seule écriture de ce catalogue dont personne ne peut rendre
  compte après coup.
- **Côté navigateur**, rien. Le SPA est ouvert depuis une autre machine aussi
  souvent que depuis celle qui fait tourner `make run` (`docs/adr/0019`), et
  « regarde le terminal » n'est alors pas une réponse.

## Décision

Un module, `core/logging.py`, et cinq choix.

**`logging_config(settings)` est une fonction pure.** Elle rend le dictionnaire
que `dictConfig` appliquerait et ne touche à rien ; `configure_logging` est la
ligne qui l'applique, appelée depuis le **point d'entrée du processus**
(`main.__getattr__` pour `uvicorn ea.main:app`, `main()` pour
`python -m ea.reindex`) et **pas** depuis `create_app`. Un test qui construit
une application ne reconfigure donc pas le logging du processus qui le fait
tourner — et une fixture autouse de `tests/conftest.py` remet tout en place
pour les deux tests qui appliquent une vraie configuration.

**L'identifiant de requête est un `ContextVar`, posé par un middleware et lu
par un filtre.** Un site d'appel écrit ce qu'il a à dire ; savoir quelle
requête il servait n'est pas son affaire, et l'alternative était de faire
passer un identifiant dans toutes les signatures. Le middleware est un
middleware ASGI nu et non un `BaseHTTPMiddleware`, parce que `/mcp` répond en
streaming et que `BaseHTTPMiddleware` le met en tampon.

**Chaque flux bruyant a son nom et son interrupteur.** `EA_LOG_LEVEL` est le
niveau de *notre* raisonnement ; le Cypher (`EA_LOG_CYPHER`), le SQL
(`EA_LOG_SQL`) et les allers-retours vers le service d'embeddings
(`EA_LOG_EMBEDDINGS`) sont trois lances à incendie qu'on ouvre une à la fois.
`EA_LOG_LEVEL=DEBUG` ne les ouvre volontairement pas : vouloir voir notre
propre code en détail n'est pas demander toutes les requêtes de trois systèmes
à la fois. Les noms sont des constantes (`CYPHER_LOGGER`, …) importées par les
modules qui écrivent dedans, pour qu'un logger et l'interrupteur censé
l'ouvrir ne puissent pas diverger.

**Texte pour un humain, JSON pour un collecteur**, et le format suit `EA_DEBUG`
sauf mention contraire. Les champs passés par `extra=` sont rendus **dans les
deux** : en JSON ils sont des champs interrogeables, en texte ils sont
concaténés en `clé=valeur` à la fin de la ligne — une durée visible seulement
en production est une durée que personne ne lit jamais.

**Les secrets sont masqués au handler**, pas au site d'appel, parce qu'un site
d'appel ne peut pas en répondre : le DSN dans le message d'erreur du driver a
été écrit par SQLAlchemy, pas par nous. `redact` couvre le *userinfo* d'une
URL, un en-tête `Bearer`, et les affectations `password=` / `token=` /
`api_key=` — peu de motifs et grossiers, parce qu'un motif qui rate coûte un
mot de passe et un motif trop large coûte un mot masqué.

Ce que cela produit, en pratique :

| Ligne | Logger | Niveau |
|---|---|---|
| Ce que le démarrage a ouvert, et où | `ea.main` | INFO |
| Une ligne par requête : méthode, chemin, statut, durée, identifiant | `ea.requests` | INFO (DEBUG pour `/health`, WARNING en 4xx, ERROR en 5xx) |
| Une ligne par écriture, avec l'id de ce qui a changé | `ea.services.*` | INFO |
| Un outil MCP appelé, son issue et sa durée | `ea.mcp.tools` | INFO (arguments en DEBUG) |
| Chaque requête Cypher et sa durée | `ea.cypher` | DEBUG, coupé |
| Chaque appel au service d'embeddings | `ea.embeddings` | DEBUG, coupé |
| Chaque appel HTTP du SPA, avec l'identifiant rendu par l'API | console | `VITE_LOG_LEVEL` |

Le SPA lit `X-Request-Id` sur la réponse, ce qui suppose que CORS l'expose —
d'où `expose_headers` dans `main.create_app`. C'est ce qui permet de joindre
une ligne du navigateur à une ligne du serveur.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| `structlog` + JSON, comme `CLAUDE.md` l'annonce | Contexte lié, processeurs, sortie JSON native | Une dépendance de plus pour ce que `dictConfig` + deux filtres font ici ; et `uvicorn`, `neo4j`, `sqlalchemy` écrivent de toute façon dans `logging` | Écarté **pour l'instant** ; la forme retenue (filtres + formatteurs) est celle qu'on remplacerait sans toucher aux sites d'appel |
| `configure_logging` dans `create_app` | Une application construite est toujours configurée | Chaque test qui construit une app reconfigure le logging du processus, et `caplog` ne capture plus rien | Écarté |
| `BaseHTTPMiddleware` pour la ligne d'accès | Plus court à écrire | Met en tampon les réponses en streaming, dont `/mcp` | Écarté |
| Un seul interrupteur `EA_LOG_LEVEL=DEBUG` pour tout | Un réglage au lieu de quatre | Ouvre le Cypher, le SQL et le HTTP en même temps ; illisible exactement au moment où on cherche quelque chose | Écarté |
| Un troisième décorateur sur les 28 outils MCP | Sépare la trace de la traduction d'erreur | Une règle de plus à retenir en ajoutant un outil, et un outil qui l'oublie est un outil sans trace | Écarté : la trace est dans `speaking_plainly` |
| Une dépendance de logging côté SPA | Transports, tampons, niveaux | On veut un des trois | Écarté : `console` et vingt lignes |

## Conséquences

- **Le volume augmente.** Une ligne par requête et une par écriture, c'est le
  prix demandé. `EA_LOG_REQUESTS=false` rend la main au log d'uvicorn ;
  `/health` est déjà en DEBUG parce qu'une sonde l'interroge sans fin.
- **`extra=` fait partie du contrat.** Une clé qui entre en collision avec un
  attribut de `LogRecord` (`args`, `module`, `name`…) lève à l'émission. Les
  champs utilisés ici — `action`, `element_id`, `duration_ms`, `status`,
  `tool` — n'en sont pas.
- **La rédaction est grossière et il faut qu'elle le reste.** Elle masquera
  parfois un mot qui n'était pas un secret. L'inverse est plus cher.
- **`ea.services.*` devient une piste d'audit** sans en être une : rien n'y dit
  *qui* a écrit, parce que rien ne le sait encore. Le jour où l'auth arrive,
  l'identité se pose là, à côté de l'identifiant de requête.
- **La configuration n'est pas rechargeable à chaud** : changer un niveau
  demande un redémarrage. Un endpoint qui la change à chaud serait un endpoint
  d'administration non authentifié de plus, ce qui est exactement ce que
  `docs/adr/0014` évite déjà pour `/mcp`.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md),
  [0016](0016-ecoute-sur-toutes-les-interfaces.md),
  [0019](0019-serveur-vite-sur-toutes-les-interfaces.md)
- Code concerné : `backend/src/ea/core/logging.py`,
  `backend/src/ea/api/middleware.py`, `backend/src/ea/mcp/errors.py`,
  `frontend/src/lib/logging.ts`
