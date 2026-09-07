---
titre: Un serveur MCP monté sur l'API, pour que les agents lisent et écrivent le référentiel
date: 2026-09-07
statut: Accepté
affects: backend/src/ea/mcp/, backend/src/ea/main.py, backend/src/ea/api/schemas.py, route /mcp
---

# 14. Un serveur MCP monté sur l'API

Date : 2026-09-07
Statut : Accepté

## Contexte

Le référentiel se remplit à la main, un élément à la fois, dans le catalogue du
SPA. Or la matière première est ailleurs : dans des inventaires, des schémas,
des comptes rendus. Un assistant sait lire tout cela et en tirer « ce composant
applicatif réalise ce service » ; il ne sait pas l'écrire ici, parce qu'il n'a
aucune prise sur le référentiel.

Le protocole MCP est cette prise. La question n'est donc pas *s'il faut* un
serveur MCP, mais **où il se branche**, et là il y a deux tentations.

**Le brancher sur l'API REST**, c'est-à-dire écrire un serveur qui fait des
requêtes HTTP à notre propre backend. C'est séduisant — « l'API décide » — et
c'est un piège : chaque forme de charge utile existerait deux fois, une fois en
Pydantic et une fois dans le client de l'agent, exactement la duplication que
`adr/0007` a bannie côté SPA. S'y ajoute un saut réseau qui ne peut que tomber,
et une invitation, à la première fonctionnalité pressée, à contourner le
service.

**Le brancher sous le service**, directement sur le dépôt Neo4j, ferait pire :
les règles qui demandent plus d'un objet — l'élément doit exister avant d'être
lié, la composition ne doit pas boucler — vivent dans `services/`, et un agent
qui écrit sous cette ligne écrit un graphe que l'API refuserait.

Restait à trancher l'exposition. Un serveur `stdio`, lancé par le client de
l'agent, n'a aucune surface réseau et n'a donc pas besoin d'authentification —
ce qui compte, puisque l'auth n'est pas encore écrite. Un transport HTTP monté
sur l'application est joignable par n'importe quelle machine qui atteint
l'hôte. Le choix a été fait en connaissance de cause : **HTTP monté**, parce
qu'un agent qui tourne ailleurs que sur le poste du développeur doit pouvoir
s'y connecter, et que `make run` sert alors les deux adaptateurs d'un coup.

## Décision

**Le serveur MCP est un adaptateur frère de `api/`, pas un client de `api/`.**
`ea/mcp/` occupe la même place que `ea/api/` dans la flèche
`api -> services -> domain <- repositories` : il traduit une demande en un
appel de `ArchitectureService` et retraduit la réponse. Toute règle qu'un agent
doit respecter — la matrice ArchiMate, la boucle de composition, et les
contrôles de permission quand l'auth arrivera — est appliquée sous cette ligne,
donc appliquée pour lui sans qu'une seule ligne soit recopiée.

**Les outils rendent les modèles de lecture de l'API**, `ElementRead`,
`GraphRead`, `MetamodelRead` et les autres. Un agent et le SPA s'entendent donc
dire la même chose du même élément. C'est un import de `mcp/` vers
`api/schemas.py`, donc d'un adaptateur vers l'autre, et c'est assumé : ces
modèles ne décrivent pas HTTP, ils décrivent la *forme sur le fil* du domaine.
Le jour où un troisième adaptateur les demande, ils déménagent — pas avant.

**L'assemblage du métamodèle descend dans les schémas.** `MetamodelRead.snapshot()`
et `RelationshipMatrixRead.for_source()` remplacent les corps de route de
`api/metamodel.py`, que `describe_metamodel` et `relationship_matrix_row`
appellent aussi. Les 61 types ne sont énumérés qu'une fois, comme
`adr/0012` l'exige déjà du SPA.

**Les quatorze outils sont annotés.** `read_only_hint`, `destructive_hint`,
`idempotent_hint` : c'est ce qu'un client montre à la personne derrière l'agent
avant de laisser passer un appel. `delete_element` — qui emporte les relations
de l'élément — et `disconnect_elements` sont marqués destructifs.

**Un échec prévu devient un `ToolError`, le reste reste dans les logs.**
`mcp/errors.py` est le pendant de `api/errors.py` dans l'autre protocole : là
où celui-ci associe une `DomainError` à un statut HTTP, celui-là la rend au
modèle avec son message, pour qu'il se corrige. Une `ValueError` du domaine
compte comme un refus d'entrée, comme elle vaut un 422 côté HTTP. Tout le reste
n'arrive au modèle que sous la forme `Error executing tool <nom>`.

**Le transport est *épissé*, pas monté.** Le SDK rend une application Starlette
dont l'unique route est le transport ; ses routes sont ajoutées à celles de
l'application FastAPI plutôt que `mount`ées, parce qu'un `Mount("/mcp", …)` ne
répond qu'à `/mcp/…` et renverrait un 307 sur un `POST /mcp` — l'adresse même
qu'on donne aux clients, et qu'un client n'est pas tenu de suivre. Deux effets
utiles : la route est une `Route` Starlette et non une `APIRoute`, donc **le
schéma OpenAPI ne bouge pas** et le client TypeScript n'est pas à régénérer ;
et le `lifespan` de la sous-application étant perdu, son gestionnaire de
sessions est démarré par celui de l'application principale, qui compose
désormais l'ouverture du pilote Neo4j et celle du transport dans un
`AsyncExitStack`.

**`EA_MCP_ENABLED` existe, et vaut `true`.** Un jeu d'outils que personne ne
peut atteindre n'est pas une fonctionnalité ; mais tant que l'auth n'existe
pas, un déploiement qui ne veut pas de ce chemin d'écriture le coupe ici.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Serveur MCP client HTTP de notre propre API | « L'API décide », une seule frontière | Chaque charge utile décrite deux fois ; un saut réseau de plus ; `adr/0007` interdit déjà ce doublon côté SPA | Écarté |
| Serveur MCP sur le dépôt Neo4j | Le plus court chemin | Contourne les règles multi-objets de `services/` ; un agent écrirait un graphe que l'API refuse | Écarté |
| Transport `stdio`, processus séparé | Aucune surface réseau, donc pas d'auth à attendre | Un seul poste servi, celui qui lance le processus | Écarté, en connaissance de cause |
| Monter le transport avec `app.mount("/mcp", …)` | L'API publique du framework | `Mount` ne filtre que `/mcp/…` : un `POST /mcp` reçoit un 307 | Écarté |
| Outils limités aux seuls éléments | Surface minimale | Sans la palette, l'agent invente des types ; sans les liens, il ne modélise rien | Écarté |

## Conséquences

**`/mcp` est, aujourd'hui, un chemin d'écriture non authentifié sur le graphe
d'architecture**, ouvert à qui atteint l'hôte de l'API. C'est le prix du choix
HTTP, et il est réel : le graphe est partagé (`adr/0006`), `delete_element`
emporte les relations, et rien ne trace qui a appelé quoi. Trois choses en
découlent :

- ne pas exposer l'API hors du réseau de confiance avant que l'auth existe ;
- quand l'auth arrivera, `/mcp` passe derrière elle **en même temps** que le
  reste — le SDK sait porter un `TokenVerifier`, et l'ADR de l'auth devra le
  dire explicitement plutôt que de l'oublier ;
- `EA_MCP_ENABLED=false` est la réponse d'ici là pour tout déploiement qui
  n'accepte pas ce risque.

**La protection anti-*DNS rebinding* du SDK peut surprendre.** Servi sur un
hôte de bouclage, le transport vérifie l'en-tête `Host` et répond `421` à ce
qui ne ressemble pas à `127.0.0.1:*` ou `localhost:*`. Un reverse proxy qui
transmet un `Host` public devant une application servie sur `127.0.0.1`
recevra donc des 421 : le jour où cela arrive, il faudra passer des
`TransportSecuritySettings` explicites plutôt que désactiver la protection.

**Le SDK `mcp` est en 2.x, une majeure jeune.** `FastMCP` y est devenu
`MCPServer` et le module `mcp.server.fastmcp` ne fait plus que lever une erreur
qui pointe le guide de migration. Une montée de version est donc à lire, pas à
appliquer ; `mcp/server.py` est le seul fichier qui touche cette API, ce qui
borne le coût.

**Un outil ajouté sans test échoue.** `tests/unit/test_mcp_server.py` affirme
la liste des quatorze noms *en entier*, et vérifie que chacun porte une
description et une annotation de lecture/écriture. C'est délibéré : la
description d'un outil est ce que le modèle lit pour décider de l'appeler, donc
elle fait partie du comportement.

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0005](0005-archimate-3-2-comme-metamodele.md),
  [0007](0007-client-openapi-genere-pour-le-spa.md),
  [0012](0012-ecran-du-metamodele.md)
- Code concerné : `backend/src/ea/mcp/`, `backend/src/ea/main.py`,
  `backend/src/ea/api/schemas.py`
