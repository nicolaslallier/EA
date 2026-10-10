# C5 — Temps et feuille de route — design

Date : 2026-10-10. Statut : proposition. Sous-projet 5 sur 9 (Sparx EA : plateaux, gap analysis, modélisation datée).

## 1. Intention

**Dit** : modéliser l'évolution du SI dans le temps — états As-Is / To-Be, plateaux, work packages,
deliverables, écarts — et la montrer en feuille de route.

**Hypothèses** :
- ArchiMate 3.2 couvre déjà le vocabulaire : `plateau`, `work_package`, `deliverable`, `gap` existent dans
  `domain/archimate/taxonomy.py` (couche Implementation & Migration). Il manque seulement **le temps** : aucun
  élément n'a de date.
- *As-Is* et *To-Be* ne sont pas des types : ce sont deux plateaux (le premier pris à une date passée/présente,
  le second à une date future). Pas de champ « état ».
- **Temps de validité** (ce spec) = quand une chose est vraie *dans le monde* (l'application X est en service du
  2024-03-01 au 2027-06-30). **Temps de transaction** (sous-projet 2, audit + baselines) = quand *le modèle* l'a su
  (saisi le 2026-10-10). Les deux sont indépendants : on peut saisir aujourd'hui une mise hors service de 2028 (validité
  future, transaction présente), ou corriger en 2026 une date de 2024 (validité passée, transaction présente). Une baseline
  fige l'état tel que le modèle le contenait à une date de saisie ; `as_of` interroge le monde à une date de validité. Ils
  se composent (« l'état du monde au 2027-01-01 tel que nous le croyions le 2026-06-01 ») mais ce spec ne livre que le premier axe.
- Un seul axe de validité par élément/relation (pas de bitemporel complet, pas d'historique des versions d'un fait).

## 2. Périmètre

**Dans** : dates de cycle de vie `valid_from`/`valid_until` sur éléments et relations ; filtre `as_of` des lectures ;
calcul d'écart entre deux plateaux ; création assistée d'un `gap` ; vue Feuille de route (SVG) ; deux outils MCP ;
sélecteur de date `?asof=`.

**Hors** : historique/versions et « qui a changé quoi » (sous-projet 2) ; paquets et requêtes enregistrées (sous-projet 1 —
on les consommera comme filtre, sans les attendre) ; plan de charge, coûts, ressources, dépendances de planning (pas de
Gantt, pas de chemin critique) ; heures (jour seulement) ; fuseaux ; diagrammes datés (un diagramme reste une vue, voir §7) ;
simulation de scénarios alternatifs.

## 3. Modèle de domaine

Nouveau module pur `domain/timeline.py` (aucun import framework), voisin de `domain/ipam.py`.

- `VALID_FROM = "valid_from"`, `VALID_UNTIL = "valid_until"` : clés de `properties`, valeur `YYYY-MM-DD` canonique
  (`date.fromisoformat` puis `isoformat()`). `valid_until` est **inclusive** ; absente = ouvert (« depuis toujours » /
  « sans fin prévue »).
- `Lifespan(start: date | None, end: date | None)` ; `Lifespan.read(properties)`, `.covers(day)`, `.overlaps(other)`.
- `validate_lifespan_properties(properties) -> Mapping | None` : même contrat que `validate_ipam_properties` — refuse une
  date mal formée ou `valid_until < valid_from` (`InvalidLifespanError`, 422), renvoie les propriétés sous forme canonique.
  Appelée par `ArchitectureService.create_element`/`update_element`/`connect` à côté de l'IPAM, pour que
  `PATCH /elements/{id}` ne soit pas une porte dérobée.
- `Roadmap` / `RoadmapItem` : lecture dérivée (id, nom, type, `Lifespan`, plateau d'appartenance).
- `PlateauGap` : `only_in_a`, `only_in_b`, `common` (tuples d'éléments) ; fonction pure `diff_plateaus(a, b)`.
- Appartenance à un plateau : **calculée**, jamais stockée en double — un élément est « dans » un plateau s'il est la cible
  d'une `composition` ou `aggregation` partant du plateau (déjà légal : `plateau` est dans `_CONTAINERS`). Même philosophie
  qu'IPAM (« belonging is computed »).
- Quand un plateau a lui-même un `Lifespan`, ses dates disent « à partir de quand cet état cible est atteint » ; la feuille
  de route le dessine en jalon. Les `triggering` plateau→plateau (déjà permis par `_permits_dynamic`) donnent l'ordre
  As-Is → transition → To-Be.
- Aucune règle de métamodèle nouvelle : `work_package` —`realization`→ `deliverable` est déjà dans `_EXTRA_ALLOWED`, un
  `deliverable` réalise n'importe quel élément (source en couche migration), `association` est universelle (gap ↔ plateau).
  Le SPA n'en copie aucune (règle « SPA sans métamodèle »).

## 4. Données

**Décision : aucune nouvelle table de faits ; option (a), dates dans `properties`.** Comparaison :

| | (a) `valid_from`/`valid_until` dans `properties` | (b) table `element_lifespans` |
|---|---|---|
| Cohérence avec l'existant | Même schéma que `cidr`/`ip_address` (ADR 0020), visible dans catalogue, voisinage, impact, MCP, import pipeline | Nouvelle jointure dans chaque CTE |
| Intégrité | Validée en `domain/` ; `properties` est une map de chaînes | FK + `DATE` natif, mais tout lecteur doit joindre |
| Tri/filtre des dates | Chaînes ISO `YYYY-MM-DD` de largeur fixe : l'ordre lexicographique = l'ordre chronologique ; **aucun cast nécessaire**, un index b-tree d'expression sur `(properties ->> 'valid_from')` sert | Index natif |
| Coût | Une migration d'index seulement | Table, repo, cascade, ADR de stockage |

Le risque de (a) (chaîne mal formée) est neutralisé par la validation d'écriture + une **migration de données** qui
canonise/valide les valeurs existantes (aucune attendue). Le jsonb ne trie pas de dates *non canoniques* : c'est pourquoi
le format est imposé et le *format partiel* (`2027-Q2`, `2027-06`) refusé.

**Migration (une révision Alembic)** : index d'expression `ix_elements_valid_from` et `ix_elements_valid_until` sur
`properties ->> 'valid_from'` / `'valid_until'` (partiels `WHERE … IS NOT NULL`), idem `relationships`. Nom selon la
convention gelée de `db/base.py`. Réversible. Pas de colonne ajoutée : appliquée sans risque à une base peuplée.

## 5. API

Un seul paramètre transverse, **`as_of: date | None`** (absent = comportement actuel, rétro-compatible), ajouté aux lectures :
`GET /elements`, `GET /elements/{id}/neighbourhood`, `GET /elements/{id}/impact`, `GET /diagrams/{id}`, `GET /relationships`.
Sémantique (dans `services/`, SQL dans `architecture_store.py`) : un élément est visible si
`valid_from IS NULL OR valid_from <= :d` **et** `valid_until IS NULL OR valid_until >= :d` ; une relation l'est si elle-même
l'est **et** si ses deux extrémités le sont. Dans les CTE récursives le prédicat est dans le terme récursif : un voisin
non encore/plus en service coupe le chemin (l'impact « au 2028 » ne traverse pas une appli retirée en 2027). Décision :
filtre au niveau de la **lecture**, pas un type de requête à part.

Nouveaux endpoints (routeur `api/timeline.py`, bornes dans `api/schemas.py`) :

| Méthode | Chemin | Réponse |
|---|---|---|
| GET | `/plateaus` | `list[PlateauRead]` (id, nom, `valid_from`, `valid_until`, nb d'éléments) triés par `valid_from` |
| GET | `/plateaus/gap?a=&b=` | `PlateauGapRead` : `only_in_a` (sorties), `only_in_b` (arrivées), `common`, et `gaps` existants reliés aux deux ; 404 plateau inconnu, 422 si `a == b` |
| POST | `/plateaus/gap` | corps `{a, b, name?}` : crée un `gap` (nom par défaut « Écart <A> → <B> »), l'associe (`association`) aux deux plateaux ; `201 ElementRead` ; 409 si le gap existe déjà pour ce couple |
| GET | `/roadmap?from=&to=` | `RoadmapRead` : plateaux, work packages, deliverables et leurs `Lifespan`, arêtes `triggering`/`realization` entre eux ; fenêtre par défaut = bornes des données |

Les dates s'écrivent via `PATCH /elements/{id}` existant (propriétés) — pas d'endpoint de plus. OpenAPI et client TS régénérés
(`make openapi`).

## 6. MCP

Adaptateur frère de `api/`, mêmes modèles de lecture, `@speaking_plainly`, entrée dans l'assertion de liste de
`tests/unit/test_mcp_server.py`, bornes importées de `api/schemas.py` :
- `list_plateaus()` — lecture ; docstring : « pour comparer deux états du SI ».
- `gap_between(a, b, create_gap=False)` — lecture si `create_gap` faux ; annotation d'écriture sinon (« crée un élément »).
- Les outils existants (`list_elements`, `get_neighbourhood`, `get_impact`) gagnent un argument optionnel `as_of`.
- `INSTRUCTIONS` : dire qu'un agent écrit `valid_from`/`valid_until` en `YYYY-MM-DD` et ne calcule pas l'écart de tête.

## 7. SPA

- **Sélecteur de date global** dans le shell (entrée native `<input type="date">`, pas de bibliothèque), état dans l'URL :
  **`?asof=YYYY-MM-DD`** (absent = « aujourd'hui, sans filtre »). Choix global plutôt que par écran : la question « et au
  2028 ? » se pose sur toutes les vues, et le router propage `asof` aux liens du menu (`router/sections.ts`). Changer la date
  = `replace`, comme un cadran de `depth` (ADR 0010). Un bandeau « Vue au 2028-01-01 » rappelle que le catalogue est filtré.
  Les diagrammes enregistrés restent des vues : `?asof=` grise les boîtes dont l'élément est hors période plutôt que de les
  retirer (on ne perd pas la mise en page).
- **Section « Feuille de route »** (`features/roadmap/`, entrée dans `SECTIONS`, groupe existant le plus proche) :
  frise horizontale, une ligne par plateau puis par work package, losanges pour les jalons de plateau, barres pour les work
  packages, ligne verticale pour la date `asof`. Géométrie dans un module **pur** `lib/roadmapLayout.ts`
  (`layoutRoadmap(items, window, width) → {bars, ticks, today}`), testé sans montage comme `lib/graphLayout.ts` ; rendu SVG
  fait main dans `components/RoadmapChart.vue`, chaque barre est un contrôle focalisable ouvrant le détail (`?element=`).
  Un élément sans date est listé dans une bande « non daté » (pas masqué).
- **Écart** : panneau « Comparer deux plateaux » (`?a=`, `?b=` dans l'URL) listant sorties / arrivées / communs, bouton
  « Créer l'écart » (éditeur seulement). Toutes les requêtes passent par `useLatestRequest()`.
- Les champs `valid_from`/`valid_until` du formulaire d'élément sont des `<input type="date">` ; la validation reste celle de l'API.

## 8. Sécurité

Lectures : `require_caller()` ; `POST /plateaus/gap`, écritures de dates : `require_editor()`, en tête de chaque méthode de
`services/` (AST de `test_service_guards.py`). `as_of` est un `date` typé (pas de texte libre dans le SQL, paramètre lié).
Les dates sont de la donnée métier, pas de PII ; aucune journalisation de valeur. Un lecteur voit tout le futur planifié : si
des plans confidentiels deviennent un sujet, ce sera un contrôle d'accès général (hors périmètre ici). Fenêtre de
`/roadmap` bornée (max 20 ans) pour éviter une réponse démesurée.

## 9. Tests

TDD, tests d'abord.
- **Unit** : `Lifespan` (bornes inclusives, ouvert/fermé, chevauchement) ; `validate_lifespan_properties` (format, ordre,
  forme canonique, `2027-06` refusé) ; `diff_plateaus` (ensembles, doublons, plateaux vides) ; `layoutRoadmap` (échelle,
  éléments hors fenêtre tronqués, élément non daté, déterminisme) ; assertion MCP et `test_service_guards`.
- **Intégration** (PostgreSQL jetable) : prédicat `as_of` dans la liste, le voisinage et l'impact (un voisin retiré coupe le
  chemin) ; l'index d'expression est utilisé (`EXPLAIN`) ; migration up/down sur base peuplée.
- **API** : 401/403/200 par endpoint, `as_of` absent = résultat identique à aujourd'hui (non-régression), 422 date invalide,
  409 gap en double, enveloppe d'erreur typée.
- **Vitest + Testing Library** : sélecteur et URL `?asof=`, frise, panneau d'écart, masquage des boutons pour un lecteur.
  E2E Playwright : non en place (CLAUDE.md) — le scénario est écrit comme test de composant en attendant.

## 10. ADR à écrire

1. **Temps de validité dans `properties`** (étend l'ADR du métamodèle et du graphe 0033, et la convention de l'ADR 0020) :
   dates ISO jour, inclusives, index d'expression, rejet de la table dédiée ; distinction validité / transaction.
2. **Le paramètre `as_of` des lectures** : sémantique sur les arêtes et dans les CTE récursives, rétro-compatibilité,
   `?asof=` global dans le SPA.
3. (Optionnel, peut être fusionné au 1) **Écart entre plateaux calculé, gap = élément ordinaire** : pas de table d'écarts.

## 11. Risques et questions ouvertes (défaut retenu)

- *Dates dans des chaînes libres modifiables par `PATCH` avant validation du client* → défaut : validation à toute écriture,
  y compris l'import pipeline (passe par l'API).
- *Remplacement total de `properties` par `PATCH` efface les dates* (risque déjà connu, cf. pipeline) → défaut : le SPA
  renvoie la map complète ; le pipeline n'écrit jamais `properties`.
- *Relation sans dates mais extrémités datées* → défaut : visible ssi les deux extrémités le sont (pas de lien orphelin).
- *Plateau défini par composition ou par dates ?* → défaut : l'écart utilise la **composition** (choix explicite de l'architecte) ;
  une aide « proposer les membres actifs à la date du plateau » est une suggestion, pas un calcul automatique.
- *Granularité* → défaut : le jour ; trimestres/mois affichés par l'échelle de la frise, pas stockés.
- *Performance* : prédicat sur jsonb dans chaque CTE → défaut : index d'expression + mesure sur le jeu réel ; en dessous de
  quelques dizaines de milliers d'éléments, aucun souci attendu.
- *Interaction avec les baselines (sous-projet 2)* → défaut : indépendantes ; `as_of` peut se combiner plus tard avec un
  paramètre `known_at`, nom réservé mais non livré ici.
- *Sous-projet 1* (paquets, requêtes enregistrées) : la frise et l'écart acceptent plus tard un filtre ; pas de dépendance dure.

## 12. Dépendances et taille

**Taille : M.** Dépend de rien en dur (le métamodèle porte déjà les types) ; se combine avec les sous-projets 1 et 2
sans les attendre. Lots livrables, chacun fusionnable seul :
1. `domain/timeline.py` + validation d'écriture + migration d'index + ADR 1 (S).
2. `as_of` sur les lectures (API + MCP + client régénéré) + ADR 2 (M).
3. `/plateaus`, `/plateaus/gap` (GET puis POST), outils MCP `list_plateaus`/`gap_between` (S).
4. SPA : sélecteur `?asof=` global + champs de date + panneau d'écart (M).
5. SPA : section Feuille de route (`roadmapLayout.ts` + `RoadmapChart.vue`) (M).
