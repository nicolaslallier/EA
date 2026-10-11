---
titre: Le paramètre as_of des lectures
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/services/architecture.py, backend/src/ea/services/diagrams.py, backend/src/ea/repositories/architecture_store.py, backend/src/ea/api/architecture.py, backend/src/ea/api/diagrams.py, backend/src/ea/mcp/server.py, frontend/src/lib/asof.ts
---

# 60. Le paramètre `as_of` des lectures

Date : 2026-10-11
Statut : Proposition

## Contexte

L'ADR 0059 donne des dates de validité aux éléments et aux relations. Reste à les lire : « que dit le catalogue au
2028-01-01 ? », « que casse la panne de cette application au 2028 ? ». La question se pose sur toutes les vues, pas
sur une seule.

## Décision

- **Un seul paramètre transverse**, `as_of: date | None`, ajouté aux lectures `GET /elements`, `GET /relationships`,
  `GET /elements/{id}/neighbourhood`, `GET /elements/{id}/impact`, `GET /diagrams/{id}`, et aux outils MCP
  `list_elements`, `neighbourhood`, `impact_of`. **Absent, le comportement est strictement l'actuel** : le
  prédicat est une liste vide, aucune condition, aucune jointure (testé sur le SQL compilé et sur PostgreSQL).
  Une valeur qui n'est pas un jour est un 422 de validation, jamais du texte dans le SQL ; la date est un
  paramètre lié.
- **Sémantique.** Un élément est visible à `d` si `(valid_from IS NULL OR valid_from <= d) AND (valid_until IS NULL
  OR valid_until >= d)` — bornes inclusives, borne absente ouverte. Une relation l'est si elle-même **et** ses
  deux extrémités le sont : pas de lien orphelin.
- **Dans les CTE récursives**, le prédicat est dans le terme récursif (le lien et l'extrémité lointaine) et dans
  la graine : un voisin non encore ou plus en service **coupe** le chemin ; l'impact « au 2028 » ne traverse pas
  une application retirée en 2027. Une racine hors période donne un sous-graphe **vide** (200), pas un 404 :
  l'élément existe, il n'est simplement pas visible ce jour-là.
- **Un seul endroit** écrit le SQL : `visible_at(record, as_of)` dans `architecture_store.py`, qui compare des
  chaînes ISO (`properties ->> 'valid_from'`, clé inlinée pour ressembler à l'index).
- **Les diagrammes ne perdent rien.** Un diagramme est une vue : `as_of` ne retire aucune boîte (la mise en page
  serait perdue) ; `DiagramRead` ajoute `inactive_element_ids` / `inactive_relationship_ids` et le SPA grise. Ce
  calcul lit la période de chaque élément en mode tolérant (`Lifespan.read_tolerant`) : une valeur illisible
  compte pour ouverte, une lecture ne lève pas.
- **Dans le SPA**, `?asof=YYYY-MM-DD` est global (absent : « aujourd'hui, sans filtre »), propagé par une garde du
  routeur ; le changer est un `replace` (ADR 0010).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Filtre de lecture `as_of` | Une seule sémantique pour toutes les vues ; rétro-compatible | Chaque lecture doit le porter | Retenu |
| Type de requête dédié (« état au… ») | Isolé | Duplique voisinage, impact, listes | Écarté |
| Filtrer dans le service, après lecture | Pas de SQL | Faux dans les CTE (un chemin passerait par un voisin retiré) et pagination fausse | Écarté |
| Retirer les boîtes hors période des diagrammes | Simple | Perte de la mise en page, incohérent avec « une vue ne porte aucun fait » | Écarté |

## Conséquences

- **Performance à mesurer.** Un index partiel (`WHERE … IS NOT NULL`) ne peut pas répondre à `IS NULL OR …` : le
  prédicat de visibilité lit les lignes. Sous quelques dizaines de milliers d'éléments, sans souci attendu ; à
  mesurer sur le jeu réel avant d'aller plus loin (index non partiels si besoin, nouvelle révision). L'index
  sert les bornes sur lignes datées (`valid_from <= :d`), ce que la feuille de route (ADR 0061) demande.
- `GET /elements/{id}/relationships` n'est pas filtré : le panneau Relations montre encore les liens vers un
  élément retiré. La recherche structurée (`POST /elements/search`) n'a pas non plus de `as_of`. À reprendre si
  cela gêne.
- Les traversées ne sont pas bornées en nombre de résultats (elles le sont en profondeur, ADR 0010) : `as_of`
  ne change pas cela, il ne peut que réduire.
- Le temps de transaction (`known_at`, baselines) reste libre : `as_of` pourra se combiner avec lui plus tard.
- Un lecteur voit tout le futur planifié ; des plans confidentiels seraient un contrôle d'accès général, hors
  périmètre.

## Références

- ADR liés : [0059](0059-temps-de-validite-dans-les-proprietes.md), [0031](0031-diagrammes-enregistres.md),
  [0010](0010-dessiner-le-voisinage-d-un-element.md), [0033](0033-postgresql-seul-pour-le-graphe.md)
- Code concerné : `backend/src/ea/repositories/architecture_store.py` (`visible_at`),
  `backend/src/ea/services/diagrams.py` (`open`)
