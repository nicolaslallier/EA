# TOGAF 10 — cadre de process d'architecture

TOGAF 10 est ici le **process de gestion d'un projet d'architecture** que le
référentiel EA va servir, pas un outil de modélisation : EA *stocke* le modèle
(ArchiMate 3.2, voir [adr/0005](../adr/0005-archimate-3-2-comme-metamodele.md))
et *répond* aux analyses d'impact ; TOGAF décrit **comment un tel modèle est
produit, révisé et gouverné**.

## ADM TOGAF 10 — la boucle

L'ADM (*Architecture Development Method*) de TOGAF 10 a restructuré la boucle en
trois phases :

| Phase | Intention | Sortie attendue |
|---|---|---|
| **Prepare** | Cadrage : objectif, portée, contraintes, parties prenantes, *roadmap* | Architecture Vision, plan du cycle |
| **Assess** | Conception : base de référence → cible, transition, gouvernance | Architectures de cible, plan de transition, registre de risques |
| **Manage** | Maintien : évolution, changement, obsolescence | Versions de l'architecture en cours, décisions de fin de vie |

Chaque phase produit des **artefacts** — *architecture statement*, *gap report*,
*roadmap* — qui, à terme, seront modélisés dans le référentiel via le métamodèle
ArchiMate. Le mapping artefact ↔ type ArchiMate n'est pas spécifié ici : il
attendra l'ADR qui posera la correspondance (voir *À écrire*).

## Gouvernance

- Les décisions structurantes (nouveau *bounded context*, changement de stockage,
  évolution de l'auth, une entrée dans `_EXTRA_ALLOWED`) **ouvrent un ADR** dans
  [`../adr/`](../adr/). C'est la *governance* en mode *repository* : on ne
  réécrive pas l'historique, on supersedé.
- La *definition of done* (fin de `CLAUDE.md`) est la porte de sortie d'une
  phase *Assess* réussie sur un incrément donné.
- Les *impact analyses* du référentiel ([`../README.md`](../README.md),
  section *L'API*) sont le mécanisme concret d'une question « qu'est-ce qui
  dépend de X ? » posée en phase *Manage* : un parcours de longueur bornée,
  `MAX_TRAVERSAL_DEPTH = 10`.

## Correspondance avec les artefacts EA déjà existants

| Sortie TOGAF 10 | Équivalent EA actuel | Références |
|---|---|---|
| Architecture Vision | Le graphe d'architecture Neo4j (ensemble des éléments et liens) | [adr/0004](../adr/0004-neo4j-pour-le-graphe-d-architecture.md) |
| Architecture de cible | L'ensemble des `Element` typés ArchiMate 3.2 | [adr/0005](../adr/0005-archimate-3-2-comme-metamodele.md) |
| Analyse d'impact | Route `GET /elements/{id}/impact` | [`../README.md`](../README.md), section *L'API* |
| Registre de décisions | `docs/adr/` | — |
| Plan de transition | **Non couvert** — à écrire | Voir *À écrire* |

## À écrire ici au fil de l'eau

- `ad-prepare.md`, `ad-assess.md`, `ad-manage.md` : déroulé par phase,
  livrables attendus, points de contrôle.
- `artefacts.md` : catalogue des artefacts TOGAF 10 et, pour chacun, le type
  ArchiMate le plus proche (à valider par ADR).
- `governance.md` : qui valide quoi, comment une décision d'architecture
  traverse le comité et atterrit dans l'ADR.
- Un ADR (`../adr/NNNN-togaf-10-cadre-de-process.md`) le jour où une phase sera
  réellement pilotée, pour figer le mapping artefact ↔ ArchiMate.

En attendant, la seule décision stable est celle-ci : **TOGAF 10 décrit le
process, ArchiMate 3.2 décrit le contenu, Neo4j le stocke ; les trois sont
orthogonaux et ne se recopient pas.**
