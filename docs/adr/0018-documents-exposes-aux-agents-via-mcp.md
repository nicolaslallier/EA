---
titre: Les documents markdown exposés aux agents — le CRUD relationnel passe par MCP
date: 2026-09-07
statut: Accepté
affects: backend/src/ea/mcp/server.py, backend/src/ea/services/documents.py, backend/src/ea/main.py, backend/tests/unit/test_mcp_server.py, backend/tests/e2e/test_mcp_endpoint.py
---

# 18. Les documents markdown exposés aux agents

Date : 2026-09-07
Statut : Accepté

Lève la réserve posée par [`0014`](0014-serveur-mcp-pour-les-agents.md) et par
`CLAUDE.md` : « les documents de `0017` ne sont délibérément **pas** exposés
comme outils, parce que téléverser un fichier n'est pas une forme MCP ».

## Contexte

[`0014`](0014-serveur-mcp-pour-les-agents.md) a donné au graphe un second
adaptateur : quatorze outils, à côté de `api/`, jamais derrière.
[`0017`](0017-documents-markdown-attaches-aux-elements.md) a ensuite ajouté la
seconde moitié du catalogue — le markdown attaché à un élément, dans
PostgreSQL — et ne l'a offerte qu'au SPA.

Un agent voit donc aujourd'hui un catalogue amputé. Il peut lire un élément,
ses liens, son voisinage et son impact, mais pas la procédure d'exploitation
qui explique quoi faire quand cet élément tombe : c'est précisément le document
que la question « qu'est-ce qui casse si ceci tombe ? » appelle. Et il ne peut
pas écrire non plus — alors que rédiger une note de décision à partir du graphe
qu'il vient de parcourir est exactement ce qu'on attend de lui.

La réserve de `0014` portait sur **une seule chose** : le transport. La route
HTTP prend un `multipart/form-data`, et il n'y a pas de fichier dans un appel
d'outil. Ce n'est pas une raison de ne pas exposer le cas d'usage ; c'est une
raison de ne pas le copier tel quel.

## Décision

**Les cinq cas d'usage de `element_documents` deviennent cinq outils MCP, et
un document arrive à l'agent comme du texte, jamais comme un téléversement.**

| Outil | Annotation | Cas d'usage |
|---|---|---|
| `attach_document` | ajoute | `DocumentService.attach_text` |
| `list_documents` | lit | `DocumentService.list_for_element` |
| `read_document` | lit | `DocumentService.get` |
| `revise_document` | modifie, idempotent | `DocumentService.revise_text` |
| `discard_document` | **destructif** | `DocumentService.discard` |

### Le texte est le format, et le décodage est ce qui change

`DocumentService` gagne deux points d'entrée, `attach_text` et `revise_text`,
et les anciens s'y ramènent : `attach(raw=...)` est désormais
`attach_text(content=decode_markdown(raw))`. Les deux chemins ne diffèrent donc
que par **l'étape de décodage**, et rien d'autre — l'élément doit exister, le
nom doit finir en `.md`, un élément ne porte qu'un document par nom, le vide et
le NUL sont refusés : tout cela reste sous la ligne, dans `Document.create`.

Faire l'inverse — encoder en UTF-8 le texte de l'agent pour le repasser dans
`decode_markdown` — aurait été plus court d'une méthode et faux : cette
fonction refuse ce qui n'est pas de l'UTF-8, or une `str` Python l'est par
construction. Le message d'erreur correspondant n'aurait jamais pu être vrai.
Ce commentaire existait déjà dans `domain/documents.py`, qui vérifie la taille
et le NUL « parce que le contenu arrive d'adaptateurs qui n'ont jamais tenu les
octets » : cet ADR est l'adaptateur que cette phrase attendait.

### `mcp/` reste un frère de `api/`, avec deux services au lieu d'un

Le serveur reçoit maintenant **deux fournisseurs**, `get_service` et
`get_documents`, cherchés à chaque appel sur `app.state`. Il ne reçoit pas de
dépôt : la règle « l'élément doit exister avant qu'un fichier s'y accroche »
est celle de `DocumentService`, aucune clé étrangère ne la porte
(`0017`), et un adaptateur tenant `PostgresDocumentRepository` serait libre de
l'oublier. La cascade inverse — supprimer l'élément emporte ses documents —
reste dans `ArchitectureService.delete_element` et vaut donc pour `delete_element`
comme pour `DELETE /elements/{id}`, sans une ligne de plus ici.

`get_documents` est **obligatoire** et non optionnel. La liste des outils
appartient à l'adaptateur, pas au déploiement : un serveur qui annoncerait
quatorze outils ici et dix-neuf là ferait dépendre le contrat d'un réglage.
Avec le magasin relationnel fermé, les outils sont donc offerts et échouent à
l'appel — exactement comme `/documents` reste routée et répond 500.

### Lister n'est toujours pas lire

Les deux modèles de lecture de `0017` traversent tels quels :
`DocumentSummaryRead` nomme les fichiers, `DocumentRead` porte le texte. La
distinction compte davantage ici qu'en HTTP : un agent qui lirait dix documents
d'un mégaoctet pour en afficher dix noms ne paierait pas de la bande passante,
il paierait sa fenêtre de contexte. Les *docstrings* des deux outils le disent,
et les instructions du serveur le redisent une fois — c'est ce que le modèle
lit pour décider.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Base64 dans un argument | Un seul chemin de service | Gonfle le texte d'un tiers, et l'agent devrait encoder de la prose qu'il vient d'écrire | Écarté |
| Un outil `manage_document` avec un mode | Une entrée au lieu de cinq | L'annotation destructive deviendrait conditionnelle : un client ne pourrait plus demander confirmation sur la seule suppression | Écarté |
| Exposer les documents en *resources* MCP plutôt qu'en outils | Sémantiquement plus juste pour la lecture | Ne couvre pas l'écriture, et imposerait deux mécanismes pour une table ; à reconsidérer si la lecture seule devient le cas dominant | Reporté |
| N'exposer que la lecture | Aucun risque d'écriture non authentifiée supplémentaire | Le graphe est déjà en écriture sur ce même chemin ; l'asymétrie n'aurait protégé rien | Écarté |

## Conséquences

**La surface d'écriture non authentifiée de `/mcp` s'étend à PostgreSQL.**
`0014` disait que `/mcp` est un chemin d'écriture non authentifié sur le
graphe ; il l'est désormais aussi sur `element_documents`. Le risque n'est pas
d'une autre nature — `delete_element` emportait déjà les documents — mais il
est plus large : `discard_document` détruit un texte que personne n'a versionné
ici. Les trois conséquences de `0014` valent inchangées, et `EA_MCP_ENABLED=false`
reste la réponse d'ici à l'auth.

**Un document n'a pas d'historique.** `revise_document` remplace le texte
entier, sans conservation de l'ancien, et une révision par un agent est aussi
définitive qu'une révision par un humain. La *docstring* le dit ; le jour où
cela ne suffit plus, c'est une table de versions et un ADR, pas un garde-fou
dans l'adaptateur.

**La liste des outils passe de quatorze à dix-neuf.**
`tests/unit/test_mcp_server.py` l'affirme toujours *en entier* : un outil
ajouté sans test échoue, et c'est voulu.

**Le contrat HTTP ne bouge pas.** Aucun schéma OpenAPI n'a changé, donc aucun
client TypeScript à régénérer — `/mcp` est une route Starlette et n'a jamais
figuré dans le schéma.

**Un test qui parle à PostgreSQL n'est pas un test unitaire.** Les outils
documents sont couverts par les doubles en mémoire, et le test d'assemblage de
`tests/e2e/test_mcp_endpoint.py` construit son application avec
`postgres_enabled=False` et le dépôt doublé : rien de cette suite n'écrit dans
la base partagée.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md),
  [0015](0015-socle-postgresql-sqlalchemy-alembic.md),
  [0017](0017-documents-markdown-attaches-aux-elements.md)
- Code concerné : `backend/src/ea/mcp/server.py`,
  `backend/src/ea/services/documents.py`, `backend/src/ea/main.py`
