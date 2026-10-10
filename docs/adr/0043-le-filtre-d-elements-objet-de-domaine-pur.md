---
titre: Le filtre d'éléments — un objet de domaine pur, partagé par le catalogue, MCP et les vues
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/query.py, backend/src/ea/domain/ports.py, backend/src/ea/domain/errors.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/api/schemas.py, backend/src/ea/api/queries.py, backend/src/ea/mcp/server.py, frontend/src/features/search
---

# 43. Le filtre d'éléments — un objet de domaine pur, partagé par le catalogue, MCP et les vues

Date : 2026-10-10
Statut : Proposition

Supersède en partie le rôle d'`ElementFilter` (`backend/src/ea/domain/ports.py`),
qui devient la forme étroite que le dépôt compile.

## Contexte

`GET /elements` sait filtrer par type, par couche et par fragment de nom, et
rien d'autre. Or les questions qu'une équipe d'architecture pose au catalogue
sont d'un autre ordre : « les composants applicatifs du paquet Finance dont le
propriétaire est renseigné et qui ne servent aucun processus ». Les obtenir
aujourd'hui, c'est lire le catalogue page à page et filtrer côté client — pour
le SPA comme pour un agent MCP, qui paie chaque page en jetons. Et ce besoin va
se répéter : les vues générées d'un sous-projet suivant voudront exactement le
même filtre.

Écrire un filtre par consommateur, c'est écrire trois fois (HTTP, MCP, vues) la
même règle de combinaison, et la voir diverger.

## Décision

Un objet de domaine pur, `ElementQuery` (`domain/query.py`), est **le** filtre :
types, couches, fragment de nom, paquet (avec ou sans sous-paquets, ou « à la
racine »), tests de propriétés (`eq`, `contains`, `exists`, `absent`), tests de
relations (type, sens `out|in|any`, présente ou absente, type de l'autre bout),
tri (`name`, `created_at`, `updated_at`), page. Tous les critères se combinent
par ET.

| Point | Choix |
|---|---|
| Un seul chemin SQL | `ElementQuery` devient un `ElementFilter` (`from_query`) que `architecture_store._matching` compile ; `GET /elements`, `POST /elements/search`, les requêtes enregistrées et les outils MCP passent par là. |
| Aucun SQL libre | Le filtre est un arbre de données. Les clés et valeurs de propriétés sont des paramètres liés (`->>` avec clé liée) ; le texte passe par `strpos`, jamais `ILIKE`. |
| Pas de règle ArchiMate recopiée | Les types et relations sont les énumérations du métamodèle. Un test de relation qu'aucun type filtré ne peut satisfaire est accepté : la réponse est vide, le métamodèle reste le seul juge. |
| Bornes | 10 tests de propriétés, 5 tests de relations, 200 caractères de texte et de valeur, page de 1 à 200. Les contradictions (`root_only` avec un paquet) sont refusées en `422`. |
| Valeurs textuelles | Les propriétés sont du texte (comme dans le modèle) : pas de comparaison numérique. |
| Forme sérialisée | JSON versionné (`version: 1`) ; un schéma Pydantic strict (`extra="forbid"`, **sans valeur par défaut** pour ne pas scinder le composant OpenAPI en entrée et sortie) en est la porte HTTP, et `to_json`/`from_json` la porte du domaine. |
| Tri | Nom croissant ; dates du plus récent au plus ancien ; l'identifiant départage toujours, pour qu'une pagination ne répète ni ne perde une ligne. |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Étendre `GET /elements` de paramètres de requête | Pas de nouvelle route | Les propriétés et relations en paramètres plats sont illisibles et non bornables ; rien à enregistrer | Écartée |
| SQL libre ou petit langage de requête | Expressif | Surface d'injection, parseur à maintenir, aucune garantie de borne | Écartée |
| Filtrer en Python après lecture | Aucun SQL à écrire | Lit tout le catalogue ; `total` et pagination faux | Écartée |
| Un filtre par adaptateur | Chacun à son rythme | Trois règles de combinaison qui divergent | Écartée |
| Schéma avec valeurs par défaut | Corps de requête plus court | Scinde le composant OpenAPI (`-Input`/`-Output`) | Écartée |

## Conséquences

- Tout critère nouveau s'ajoute à `ElementQuery`, à `ElementFilter`, à
  `_matching`, au schéma et à `to_json` — et chaque lieu a son test. Un
  critère qui change le sens d'un champ existant demande `version: 2` et un
  lecteur qui refuse l'ancienne.
- Une requête relue après coup repasse par `from_json` : version inconnue ou
  valeur que les énumérations ne connaissent plus → `422 invalid_query`, jamais
  une erreur de serveur.
- `EXISTS` corrélé par test de relation : correct à quelques milliers
  d'éléments ; à mesurer avant tout index dédié (`relationships` a déjà ses
  index sur les deux extrémités).
- Le filtre ne sait pas comparer des nombres : une propriété numérique se teste
  en texte.
- `GET /elements` et `POST /elements/search` construisent le même
  `ElementFilter` et passent par le même `_matching` : un test d'intégration
  (`test_search_parity.py`) exige qu'ils rendent la même page.

## Références

- ADR liés : [0033](0033-postgresql-seul-pour-le-graphe.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0042](0042-les-paquets-classement-en-liste-d-adjacence.md)
- Spec : `docs/superpowers/specs/2026-10-10-01-referentiel-structure-design.md`
- Code concerné : `backend/src/ea/domain/query.py`, `backend/src/ea/repositories/architecture_store.py` (`_matching`)
