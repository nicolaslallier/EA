# Analyse du modèle — design (sous-projet 4)

Date : 2026-10-10. Statut : proposition.
Périmètre : C2 matrice de relations éditable, C7 contrôles de qualité, C4 cartes de chaleur et tableaux de bord.

## 1. Intention

**Dit.** Reproduire trois fonctions de Sparx EA : la *Relationship Matrix* (éléments × éléments, avec création de lien dans la cellule), des contrôles de qualité du modèle (orphelins, doublons, nommage, propriétés obligatoires, descriptions manquantes, relations incohérentes) et des cartes de chaleur / tableaux de bord (couverture, obsolescence, coût, risque).

**Hypothèses.**
- Le sous-projet 1 existe : `packages` / `elements.package_id` et `saved_queries` (filtre structuré, évaluateur pur dans `domain/`). Ici, « un ensemble d'éléments » = un paquet (descendants compris) ou une requête enregistrée, résolu par un seul service.
- Rien de ce sous-projet ne crée un fait nouveau : la matrice écrit des `relationships` ordinaires, les contrôles et les tableaux de bord *calculent* à la demande. D'où : aucune table obligatoire (§4).
- Les valeurs `lifecycle`, `cost`, `risk` sont des clés de `properties` (map de chaînes, clés validées par `domain/model.py`).

## 2. Périmètre

**Dans** : matrice (lecture, création, suppression d'un lien par cellule) ; jeu fixe de règles de qualité + 2 paramètres ; agrégations sur type / couche / paquet et sur une propriété ; une carte de chaleur et un tableau de bord dans le SPA ; lecture par MCP.

**Hors** : édition en masse de la matrice (un lien à la fois) ; règles définies par l'utilisateur ; correction automatique des constats ; historique / tendance des scores (rien n'est stocké) ; export Excel ; alertes ; bibliothèque de graphiques ; matrice de type « élément × propriété ».

## 3. Modèle de domaine

Tout en Python pur, sans import de framework.

**`domain/matrix.py`** — `MatrixAxis` (liste ordonnée d'éléments, plafonnée), `MatrixCell` (`source_id`, `target_id`, `links: tuple[Link]`, `allowed: tuple[RelationshipType]`), `build_matrix(rows, cols, relationships, permitted)`. `permitted` est la fonction `permitted_relationships` de `domain/archimate/rules.py` : la matrice ne restate aucune règle. Une cellule dont `allowed` est vide et sans lien est « interdite » (grisée). Une cellule `(a, a)` n'est pas spéciale : `validate_relationship` décide.

**`domain/quality.py`** — `Severity` (`error | warning | info`), `Finding(rule, severity, element_id, related_id | None, message)`, `QualityParams(similarity: float = 0.9, required_properties: Mapping[ElementType, frozenset[str]])` et une fonction par règle, chacune identifiée par un `code` stable :

| code | gravité | test |
|---|---|---|
| `orphan` | warning | aucun lien entrant ni sortant (les `grouping`/`location`/`plateau` exclus) |
| `duplicate_probable` | warning | même type, noms égaux après normalisation (casse, accents, ponctuation) ou similarité ≥ seuil (`difflib.SequenceMatcher`, stdlib) |
| `naming` | info | nom vide après trim, espaces doubles, nom tout en capitales, nom identique au type |
| `missing_property` | warning | une clé de `required_properties[type]` absente ou vide |
| `no_description` | info | `description` vide |
| `inconsistent_relationship` | error | lien que `validate_relationship` refuserait aujourd'hui (le métamodèle a pu évoluer, ou l'import l'a laissé passer) ; cycle de composition |

`run_checks(elements, relationships, params) -> QualityReport` (constats triés par gravité puis nom ; compteurs par code). Fonctions pures, déterministes, testables sans I/O. La comparaison des doublons est limitée au même type, par blocs (première lettre normalisée) pour rester sous O(n²) brut.

**`domain/analytics.py`** — `Dimension` (`element_type | layer | package | property:<clé>`), `Metric` (`count | sum | avg | min | max`, les trois derniers sur une propriété), `Aggregate(buckets, total)`, et surtout **la couleur côté serveur** : `scale_for(values, kind)` renvoie, par cellule, un `level` ordinal `0..4` (quantiles pour un nombre, rang de l'énumération déclarée pour `lifecycle`/`risk`) et le serveur envoie `level`, jamais une teinte. Le SPA associe `level → couleur` dans une table de présentation (comme `LAYER_COLOURS`) ; aucune règle de seuil n'est dans le SPA.

**Propriété typée : décision.** On **ne** change **pas** le stockage (`properties` reste `Mapping[str, str]`). On introduit en revanche un *catalogue de lectures* dans le domaine — `ANALYTIC_PROPERTIES = {lifecycle: énum[…], risk: énum[faible, moyen, élevé, critique], cost: nombre, …}` — qui dit comment lire certaines clés connues : énumération ordonnée ou nombre décimal. Une valeur illisible est comptée dans un seau « non renseigné / invalide » et signalée, jamais ignorée en silence. Toute autre clé est traitée comme énumération nominale (comptage seulement). Ce choix évite une migration de données et un schéma de propriétés ; il se retourne si l'on veut valider à l'écriture (voir §11).

## 4. Données

**Aucune table, aucune migration pour la matrice, les contrôles et les tableaux de bord** : les trois sont des vues calculées sur `elements` et `relationships`. Stocker un résultat de contrôle créerait une seconde vérité que chaque modification d'élément périmerait.

Seule exception justifiée, dans le lot 2 : les paramètres (`similarity`, `required_properties`) viennent de `Settings` (`EA_QUALITY_SIMILARITY`, `EA_QUALITY_REQUIRED` en JSON `{"application_component": ["owner"]}`), validés au démarrage. Une table `quality_rules` n'est introduite que si des paramètres par paquet sont demandés (hors périmètre, YAGNI).

Index à vérifier dans la revue du lot 3 : un index GIN sur `elements.properties` n'est ajouté que si `EXPLAIN` montre un balayage séquentiel gênant ; sinon rien.

## 5. API

Tous les routeurs derrière le bearer ; les services font `require_caller()` (lecture) ou `require_editor()` (écriture). Plafonds et bornes déclarés **une fois** dans `api/schemas.py` : `MAX_MATRIX_AXIS = 50`, `MAX_FINDINGS = 1000`, `MAX_BUCKETS = 200`.

- `POST /matrix` (lecture malgré le POST : deux axes sélectionnent chacun `{package_id | query_id | element_ids}`) → `MatrixRead` : `rows`, `cols` (éléments abrégés), `cells` (liste creuse : seules les cellules ayant un lien ou un type permis), `truncated` si un axe dépasse le plafond. 422 `matrix_axis_too_large` au-delà de `MAX_MATRIX_AXIS`.
- `GET /matrix/cell?source=&target=` → liens existants + types permis (réutilise `/metamodel/relationships`).
- La création / suppression passent par `POST /relationships` et `DELETE /relationships/{id}` existants : **aucun nouveau chemin d'écriture**, donc aucune règle contournée.
- `GET /quality?package_id=&query_id=&severity=&code=&limit=&cursor=` → `QualityReportRead` : `counts` par code et gravité, `findings[]` (`rule`, `severity`, `element_id`, `related_id`, `message`, `fix_hint`). `fix_hint` est un lien de l'application (`/elements?element=<id>`, `/relations?element=<id>`), pas du code.
- `GET /analytics/aggregate?by=&metric=&property=&package_id=&query_id=` → `AggregateRead` : `buckets[{key, count, value, level}]`, `unreadable`. `by` et `metric` sont des enums ; `property` est borné par le motif de clé de `domain/model.py`.
- `GET /analytics/heatmap?rows=&cols=&metric=&property=` → grille deux dimensions (p. ex. couche × cycle de vie) avec `level` par cellule.

L'agrégation se fait en SQL (`GROUP BY` + `properties ->> :key`, clé en paramètre lié, jamais interpolée ; cast numérique protégé par `CASE WHEN ... ~ '^-?[0-9]+(\.[0-9]+)?$'`). Les contrôles chargent les éléments du périmètre et leurs liens (deux requêtes) puis appellent le domaine pur ; le seul calcul en SQL est la détection d'orphelins, par `NOT EXISTS`, pour tenir sur un grand modèle. Les contrôles et agrégats sont dans `services/analysis.py` (un service par sous-projet, gardes en première instruction, vérifiés par `test_service_guards.py`).

## 6. MCP

Adaptateur frère, mêmes modèles de lecture, trois outils en lecture seule :
- `check_model(package_id?, query_id?, severity?, code?, limit)` — le pipeline ou un agent lit les constats ; la docstring dit quand l'appeler (avant d'écrire, après un import).
- `aggregate_model(by, metric, property?, package_id?, query_id?)`.
- `get_relationship_matrix(rows, cols)` — lecture ; la création reste `create_relationship`.

Annotations `readOnlyHint` ; entrée dans l'assertion de liste de `tests/unit/test_mcp_server.py` ; bornes importées de `api/schemas.py` et ajoutées à `TestTheSameBoundsAsTheHttpAdapter`. Le texte `INSTRUCTIONS` du serveur mentionne `check_model`.

## 7. SPA

Trois sections ajoutées à `router/sections.ts` (groupe « Analyse ») : `matrix`, `quality`, `dashboard`, chacune en import paresseux.

**Matrice** (`features/matrix/`). Grille HTML (`role="grid"`) : en-têtes de ligne et de colonne = noms ; une cellule affiche une pastille par lien (abréviation du type, flèche de sens, libellé de `RELATIONSHIP_LABELS`) ; cellule interdite grisée. Clavier : flèches pour se déplacer, Entrée ouvre le panneau de la cellule, Échap le ferme. Le panneau reprend la logique de `RelationshipPanel` : liste les liens existants (suppression, réservée à l'éditeur), puis un `<select>` n'offrant que `allowed` ; `useLatestRequest` pour la matrice et pour la cellule. Sélecteurs d'axes : paquet ou requête enregistrée (composants du sous-projet 1). Aucun type permis n'est calculé côté SPA : tout vient de `MatrixRead`/`/matrix/cell`. État dans l'URL : `?rows=`, `?cols=` (`pkg:<id>` ou `q:<id>`), `?cell=` ; changer un axe pousse une entrée d'historique, ouvrir une cellule la remplace. Au-delà du plafond, bandeau « 50 premiers sur N, affinez ».

**Qualité** (`features/quality/`). Tableau de constats groupé par règle avec compteurs en tête, filtre gravité / code / périmètre dans l'URL, lien « corriger » = `fix_hint`. Aucun libellé de règle n'est une règle : table `RULE_LABELS` de traduction seulement.

**Tableau de bord** (`features/dashboard/`). Tuiles de comptage, et une carte de chaleur en SVG à la main (rectangles, `<title>` pour l'infobulle, texte lisible), couleur = `LEVEL_COLOURS[level]` à cinq crans avec encre propre à chaque fond (pattern `LAYER_COLOURS`) ; légende dérivée des `level` reçus. Chaque case est un lien vers le catalogue filtré. **Pas de bibliothèque de graphiques** : une grille de rectangles ne justifie pas une dépendance (cf. CLAUDE.md, « Drawing a graph »). Géométrie dans un module pur `lib/heatmapLayout.ts`, testé sans montage. Un diagramme à barres n'est pas fourni : un tableau trié suffit.

## 8. Sécurité

- Lecture : tout appelant authentifié ; la matrice ne renvoie que des éléments que `/elements` renverrait déjà. Écriture : uniquement via `/relationships` (`ea-editor`), le SPA masque les contrôles d'édition au lecteur mais l'API décide.
- Pas de SQL dynamique : `by`/`metric` sont des enums mappés vers des expressions SQLAlchemy ; la clé de propriété est validée par le motif existant puis liée en paramètre.
- Charges bornées : plafonds d'axes, `MAX_FINDINGS`, `MAX_BUCKETS`, délai de requête (`statement_timeout` local à la transaction de lecture) pour un `heatmap` coûteux.
- Les messages de constats contiennent des noms d'éléments (données utilisateur) : rendus en texte par Vue, jamais en `v-html`. Aucun secret ni valeur de propriété sensible journalisés ; une ligne de log `action=quality_run` avec `duration_ms` et nombre de constats.

## 9. Tests

TDD, tests d'abord.
- **Unit** : `build_matrix` (cellule vide, multi-liens, sens, cellule interdite, plafond, symétrie A→B/B→A) ; une série par règle de qualité (cas positif, négatif, bord : nom accentué, élément seul de son type, cycle) ; `scale_for` (quantiles avec égalités, énumération ordonnée, valeur illisible) ; lecture de propriété `cost` (`"1 200,50"` refusé ou accepté selon la décision §11) ; `test_service_guards` couvre `AnalysisService`.
- **Intégration** (PostgreSQL jetable) : agrégation SQL sur `properties`, cast numérique avec valeurs sales, orphelins par `NOT EXISTS`, résultat identique à l'implémentation pure sur un même jeu.
- **API** : 401 sans jeton, 200 lecteur, 422 au-delà des plafonds, création de lien depuis une cellule (`POST /relationships`) refusée par le métamodèle pour un type non permis, schéma OpenAPI régénéré (`make openapi-check`).
- **MCP** : liste complète des outils, bornes identiques à l'HTTP, `ToolError` sur périmètre inconnu.
- **SPA** (Vitest + Testing Library) : navigation clavier de la grille, panneau n'offrant que `allowed`, bouton de suppression absent pour un lecteur, état d'URL, `heatmapLayout`, dernière requête gagnante.
- **E2E** : non scaffoldé ; le parcours « ouvrir la matrice, créer un lien » est écrit en spec Vitest d'intégration en attendant Playwright.

## 10. ADR à écrire

1. Lecture des propriétés pour l'analyse (catalogue de lectures sans changer le stockage ; couleur par `level` calculé serveur).
2. Contrôles de qualité calculés à la demande, règles pures identifiées par `code`, paramètres en configuration.
3. Matrice : axes par paquet / requête, plafond, écriture exclusivement par `/relationships`.
4. Pas de bibliothèque de graphiques pour la carte de chaleur (à fusionner avec l'ADR 1 si l'ensemble reste court).

## 11. Risques et questions ouvertes (défaut retenu)

- **Propriétés typées ?** Défaut : non, chaînes + catalogue de lectures. Risque : `cost` saisi « 1 200 € » est illisible. Défaut : seuls `.`/`,` décimaux et l'espace insécable sont tolérés, le reste va dans « invalide » et produit un constat `info` ; une validation à l'écriture est un chantier ultérieur.
- **Coût du doublon probable** : quadratique par bloc. Défaut : blocs par type + première lettre, plafond 5 000 éléments par exécution, message « périmètre trop large » au-delà.
- **Matrice 50×50 insuffisante ?** Défaut : 50, constante unique modifiable ; pagination d'axes hors périmètre.
- **Doublon faux positif** : pas de liste d'exceptions (aucun stockage). Défaut : le seuil est réglable, `duplicate_probable` est un avertissement, jamais une erreur.
- **Relation « incohérente »** : un lien existant peut devenir refusé après évolution du métamodèle. Défaut : constat `error`, sans suppression automatique.
- **Cohérence entre sous-projets** : si le sous-projet 1 n'est pas livré, la matrice et les contrôles acceptent `element_ids` explicites et un périmètre « tout le catalogue » ; paquets et requêtes s'ajoutent sans changement d'API.
- **Lisibilité couleur** : cinq crans plus encre propre, motif ou valeur imprimée dans la case en plus de la teinte (daltonisme).

## 12. Dépendances et taille

Dépend de : sous-projet 1 (paquets, requêtes enregistrées) pour les sélecteurs d'axes ; sans lui, les lots restent livrables sur « tout le catalogue ». Indépendant des autres sous-projets. **Taille globale : L** (3 lots indépendants).

Lots, dans l'ordre :
1. **Matrice (M)** — `domain/matrix.py`, `POST /matrix`, `GET /matrix/cell`, outil MCP, section SPA, grille clavier, panneau de cellule. Réutilise les écritures de `/relationships`.
2. **Contrôles de qualité (M)** — `domain/quality.py` (six règles + 2 paramètres), `GET /quality`, `check_model`, section SPA ; première exécution utilisable par le pipeline.
3. **Tableaux de bord (M)** — `domain/analytics.py`, `/analytics/aggregate` et `/heatmap`, `aggregate_model`, section SPA avec carte de chaleur SVG, ADR sur les propriétés lues.

Chaque lot : tests d'abord, client OpenAPI régénéré, ADR correspondant, `make check` vert.
