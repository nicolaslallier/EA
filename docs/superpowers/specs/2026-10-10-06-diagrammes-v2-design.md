# Sous-projet 6 — Diagrammes v2 — design

Date : 2026-10-10. Statut : proposition (à relire avant plan d'implémentation).

Périmètre fonctionnel : A2 éditeur enrichi, A5 vues générées par requête, A7 modèles de
diagrammes, A6 profils et propriétés personnalisées (à la manière des MDG/profils de Sparx EA).

## 1. Intention

**Dit.** Le constructeur de diagrammes actuel (ADR 0031, 0035) pose des boîtes, tire des liens
droits et enregistre après chaque geste. Un architecte attend davantage : plusieurs
objets à la fois, annuler, zoomer, ranger, annoter, grouper, router un lien autour d'un
obstacle, exporter une image ; obtenir un diagramme « tout ce qui répond à cette requête » sans
le dessiner ; repartir d'un modèle ; et typer finement ses éléments (stéréotypes) avec les
propriétés attendues, comme les profils d'UML/Sparx.

**Hypothèses.**
- Un diagramme reste une **vue** : aucun fait n'y habite (ADR 0031). Tout ce qui est ajouté ici
  (notes, groupes, coudes, stéréotypes dessinés) est de la présentation, ou de la donnée de
  référence (profils) qui ne modifie pas le métamodèle ArchiMate 3.2 (ADR 0005).
- Les paquets (`packages`, `diagrams.package_id`) et les requêtes enregistrées (`saved_queries`,
  filtre structuré en domaine pur) viennent du sous-projet 1 ; l'audit du sous-projet 2 ; les
  contrôles qualité du sous-projet 4. Aucun des trois n'est réécrit ici.
- La mise en page automatique vit côté SPA (TypeScript pur, sans DOM) : le serveur ne
  connaît aucune coordonnée calculée, seulement celles qu'on lui envoie.
- Pas de bibliothèque de graphes ni de dessin (CLAUDE.md, ADR 0010/0013) : tout est SVG écrit à
  la main sur des modules purs testés sans montage.
- Un seul rédacteur à la fois reste le cas courant, mais l'écrasement silencieux relevé par
  l'ADR 0031 est traité ici (champ `revision`, section 4).

## 2. Périmètre

**Dans** : multi-sélection, zoom/pan, annuler/rétablir, alignement/distribution, export SVG/PNG ;
notes, groupes imbriqués, points de coude des liens ; vues générées (requête ou paquet), vivantes
ou figées, avec auto-layout par couches ArchiMate ; modèles de diagrammes (créer depuis un
diagramme, instancier) ; profils, stéréotypes, propriétés déclaratives, rendu spécifique,
export/import JSON d'un profil ; contrôle de concurrence optimiste ; outils MCP de lecture et de
génération.

**Hors** : boîtes d'élément imbriquées dans une autre boîte d'élément (la sémantique
d'imbrication ArchiMate — implique une relation — est un autre sujet ; un **groupe** suffit
ici) ; édition collaborative temps réel ; routage orthogonal automatique des liens (les
coudes sont posés à la main) ; outil MCP qui écrit des coordonnées ; icônes SVG arbitraires
fournies par l'utilisateur (jeu fermé) ; export vers Visio/draw.io/ArchiMate Exchange ; types
d'éléments ou de relations nouveaux dans le métamodèle ; propriétés typées autrement qu'en
chaînes (le schéma déclaratif contraint la chaîne, il ne change pas son type de stockage) ;
héritage entre stéréotypes ; pagination de `/diagrams`.

## 3. Modèle de domaine

Nouveaux modules purs (aucun import framework) : `domain/diagram_objects.py`,
`domain/diagram_templates.py`, `domain/profiles.py`, `domain/generated_views.py`. `domain/diagrams.py`
garde `Diagram`, `DiagramNode`, `check_layout`, qui s'étendent.

### 3.1 Objets de vue : notes et groupes
`DiagramObject(id, kind: note|group, parent_id, x, y, width, height, text, colour, z, anchor_element_id)`.
Une **table `diagram_objects` polymorphe** (pas deux tables) : les deux ont la même géométrie, le
même ordre de superposition, la même imbrication (un groupe contient groupes et notes) ; seule
la signification de `text` change (titre d'un groupe, corps d'une note). Une table par genre
dupliquerait la lecture, le remplacement transactionnel et la détection de cycles.
- `check_layout` devient `check_scene(nodes, objects, edges)` : un élément une seule fois, ids
  d'objets uniques, `parent_id` désigne un **groupe** du même diagramme, pas de cycle, profondeur
  ≤ `MAX_GROUP_DEPTH = 5`, un groupe n'a pas d'`anchor_element_id`.
- Une note peut être **ancrée** à un élément (trait pointillé de la note à la boîte) ; ce n'est
  pas une relation, c'est du dessin.

### 3.2 Conteneur imbriqué et relation `composition`
**Un groupe n'est pas un élément du graphe et ne crée jamais de relation.** Mettre trois boîtes dans un
groupe « Domaine Paiement » dit « visuellement ensemble », rien d'autre ; supprimer le groupe
remonte ses enfants au parent du groupe et ne touche aucun élément. Si l'architecte veut
exprimer la composition, il tire le lien (`POST /relationships`, métamodèle consulté) comme
aujourd'hui. Aide, jamais automatique : le menu contextuel d'un groupe propose « Relier le
contenu… », qui ouvre le popover de lien existant pour chaque paire choisie — un geste explicite
par relation, validé par `ArchitectureService`.

### 3.3 Liens routés
`DiagramEdge(relationship_id, waypoints: tuple[Point, ...])`, au plus `MAX_WAYPOINTS = 20`. Une
ligne n'existe que pour un lien dont le tracé a été modifié ; absence de ligne = segment droit
rogné aux bords (`edgeSegment`, ADR 0031). Le lien reste la relation du graphe ; seul son tracé
est de la vue. Une relation supprimée emporte son tracé (cascade).

### 3.4 Vues générées
`ViewSource = QuerySource(query_id) | PackageSource(package_id, recursive: bool)`.
`Generation = live | frozen`. Fonction pure `resolve_members(source_filter, catalogue) -> ids`
réutilise le filtre du sous-projet 1 (aucune réécriture) ; `diff_members(stored, resolved) ->
(added, removed)`.

**Tranche : vivante par défaut, figeable.**
- *Vivante* : le contenu (qui est sur le diagramme) est **recalculé à chaque ouverture** ; aucune
  ligne de `diagram_nodes` n'est stockée ; la mise en page est calculée par le SPA (§7.3),
  déterministe donc stable tant que le contenu l'est. Lecture seule (bandeau « Vue vivante »).
- *Figée* : l'utilisateur clique **Figer** ; le SPA envoie la mise en page calculée, le serveur
  enregistre les boîtes, passe `generation = frozen` ; la référence à la source est conservée.
  Le diagramme devient éditable comme un diagramme manuel (notes, groupes, coudes, déplacements).
- **Rafraîchir** (figée seulement) : `GET .../source-diff` calcule `{added, removed}` sans rien
  écrire ; le SPA place les ajoutés par l'auto-layout (positions des survivants inchangées),
  retire les disparus après confirmation, puis enregistre par le `PUT` habituel.
- **Dégeler** : `POST .../unfreeze` supprime les boîtes stockées et revient à vivante ; refusé
  (409) s'il existe des notes, groupes ou tracés personnalisés, sauf `discard_annotations=true`
  (confirmation explicite dans le SPA).
- Plafond : `MAX_DIAGRAM_NODES` (500, `api/schemas.py`). Une source qui en donne plus est refusée à
  l'ouverture/création avec le décompte (422 `generated_view_too_large`) — on réduit le filtre.

### 3.5 Modèles de diagrammes
`DiagramTemplate(id, name, description, body)`. `body` est un document JSON versionné
(`version: 1`) validé par `domain/diagram_templates.py` : `slots[]` (clé, type d'élément
ArchiMate, stéréotype facultatif, libellé, géométrie, `parent_key`), `notes[]`, `groups[]`,
`links[]` (clé source, clé cible, type de relation). Un modèle ne contient **aucun élément** :
seulement des emplacements typés. Les types de slot/lien sont vérifiés contre `/metamodel` à
l'écriture (domaine pur alimenté par le métamodèle du domaine, pas de liste restatée).
Instanciation : chaque slot est lié à un élément existant du bon type (ou à un élément à
**créer** : nom requis, passe par `ArchitectureService.create_element`) ou laissé vide (omis).
Les liens du modèle ne créent des relations **que si** `create_relationships=true` est demandé
explicitement ; sinon ils ne sont que les relations déjà présentes dans le graphe.

### 3.6 Profils et stéréotypes
- `Profile(id, name, description)` ; `Stereotype(id, profile_id, name, base_type, colour, icon_key)` ;
  `StereotypeProperty(stereotype_id, key, label, required, kind: free|enum|pattern, allowed_values,
  pattern, help)`.
- `base_type` ∈ types ArchiMate existants (le domaine l'interroge, ne le copie pas). Le
  métamodèle ne bouge pas : un stéréotype est une **étiquette supplémentaire** posée sur un
  élément, jamais un type nouveau ; les règles de relations restent celles du `base_type`.
- Un élément porte 0..n stéréotypes de son type de base (`element_stereotypes`).
- Les valeurs restent dans `elements.properties` (jsonb, chaînes, **sans préfixe** comme
  l'IPAM) sous la clé déclarée. Clés réservées refusées dans une déclaration : celles de l'IPAM
  (`ip_address`, `cidr`, `vrf`, …) et de toute convention du domaine.
- `domain/profiles.py` : `validate_stereotype_properties(properties, stereotypes) -> properties`
  (énumération, motif, longueur ≤ 500 ; une clé non déclarée reste libre — un élément garde ses
  propriétés libres) et `missing_required(properties, stereotypes) -> tuple[str, ...]`. **`required`
  n'est pas bloquant à l'écriture** (on crée l'élément puis on le complète) : le domaine
  expose `missing_required` et le sous-projet 4 en fait une règle de qualité. Valider le format
  d'une valeur présente, en revanche, est bloquant, comme `validate_ipam_properties`.
- Motif : sous-ensemble sûr (≤ 200 car., compilé à l'écriture du profil, refus des quantificateurs
  imbriqués `(…+)+`), appliqué à des valeurs ≤ 500 car. — pas de `re2` (dépendance) ; voir ADR.
- Valeur par défaut de rendu : couleur `#rrggbb` validée, `icon_key` dans un jeu fermé embarqué
  par le SPA (liste de clés dans le schéma OpenAPI via enum).

## 4. Données (migrations, numéros non fixés ici)

Une révision par lot qui touche le schéma, générée par `make pg-revision` et relue. Nommage
des contraintes : convention gelée de `db/base.py`.

| Table | Colonnes | Contraintes |
|---|---|---|
| `diagram_objects` | `id uuid pk`, `diagram_id` FK `diagrams` CASCADE, `kind varchar(8)` check in (`note`,`group`), `parent_id` FK `diagram_objects` SET NULL, `x,y,width,height double`, `text` (≤ 2000), `colour varchar(7) null`, `z int default 0`, `anchor_element_id` FK `elements` SET NULL | index `(diagram_id)` ; check `kind='note' or anchor_element_id is null` |
| `diagram_edges` | `diagram_id` FK CASCADE, `relationship_id` FK `relationships` CASCADE, `waypoints jsonb` (liste de `[x,y]`) | pk `(diagram_id, relationship_id)` ; index `relationship_id` |
| `diagram_nodes` (+) | `parent_object_id` FK `diagram_objects` SET NULL, `z int default 0` | défauts serveur : boîtes existantes inchangées |
| `diagrams` (+) | `revision int not null default 0`, `generation varchar(6) null` check in (`live`,`frozen`), `source_query_id` FK `saved_queries` RESTRICT, `source_package_id` FK `packages` RESTRICT, `source_recursive bool default false` | check : au plus une source ; `generation` non nul ssi une source |
| `profiles` | `id`, `name` unique, `description`, horodatages | |
| `profile_stereotypes` | `id`, `profile_id` FK CASCADE, `name`, `base_type`, `colour`, `icon_key` | unique `(profile_id, name)` ; `base_type` validé en domaine (un enum PG figerait le métamodèle) |
| `profile_properties` | `stereotype_id` FK CASCADE, `key`, `label`, `required`, `kind`, `allowed_values jsonb`, `pattern`, `help` | pk `(stereotype_id, key)` |
| `element_stereotypes` | `element_id` FK `elements` CASCADE, `stereotype_id` FK `profile_stereotypes` RESTRICT | pk `(element_id, stereotype_id)` ; index `stereotype_id` |
| `diagram_templates` | `id`, `name` unique, `description`, `body jsonb`, `created_at`, `updated_at` | `body` n'est jamais interrogé à l'intérieur : jsonb versionné, validé en domaine |

- **Concurrence** : `PUT /layout` reçoit `revision` ; si elle diffère, 409 `diagram_revision_conflict`
  avec la révision courante ; sinon la révision s'incrémente dans la même transaction. Le SPA
  recharge et propose de recoller son état (cas rare, message clair), au lieu de l'écrasement
  silencieux de l'ADR 0031.
- Les sources RESTRICT : supprimer une requête ou un paquet utilisé par une vue générée est un
  409 qui nomme les diagrammes (hook dans le service du sous-projet 1).
- Retirer un stéréotype appliqué : RESTRICT côté définition (409 avec le décompte d'éléments) ;
  retirer l'application sur un élément ne touche pas ses propriétés.
- Migrations sur base existante : aucune donnée à convertir (ajouts nullables ou défauts) ;
  `engine_at_head` monte et redescend toute la chaîne.

## 5. API

Toutes les entrées en Pydantic `extra="forbid"`, bornes déclarées une fois dans
`api/schemas.py` (nombre d'objets ≤ 500, d'arêtes ≤ 2000, coordonnées ±100 000, texte de note
≤ 2000, coudes ≤ 20). Erreurs typées dans `_STATUS` (`api/errors.py`).

**Diagrammes**
- `PUT /diagrams/{id}/layout` : corps `{revision, nodes[], objects[], edges[]}` (tous requis, listes
  éventuellement vides — évite le dédoublement `-Input/-Output` de l'ADR 0035) ; `204` +
  en-tête `X-Diagram-Revision`; `404`, `409` (révision), `422` (élément absent, doublon, cycle de
  groupes, profondeur, arête sur une relation dont une extrémité n'est pas sur le diagramme).
- `GET /diagrams/{id}` : `DiagramRead` + `objects`, `edges`, `revision`, `generation`, `source`.
  Pour une vue vivante, `nodes = []`, `elements` = résultat de la requête (+ relations entre eux).
- `POST /diagrams/generate` : `{name, source: {query_id}|{package_id, recursive}, generation}` →
  `201 DiagramRead`. `GET /diagrams/{id}/source-diff` → `{added[], removed[]}`.
  `POST /diagrams/{id}/freeze` (corps : la mise en page `nodes`) et `POST /diagrams/{id}/unfreeze`
  (`discard_annotations`).
- Modèles : `GET/POST /diagram-templates`, `GET/PUT/DELETE /diagram-templates/{id}`,
  `POST /diagrams/{id}/as-template` (`{name}` : remplace chaque élément par un slot du même type,
  garde notes/groupes/liens par type) ; `POST /diagram-templates/{id}/instantiate`
  `{name, bindings[{slot_key, element_id?|create:{name}}], create_relationships}` → `201 DiagramRead`.
  Slot lié à un élément d'un autre type : 422.

**Profils**
- `GET/POST /profiles`, `GET/PUT/DELETE /profiles/{id}`, `POST /profiles/{id}/stereotypes`,
  `PUT/DELETE /stereotypes/{id}` (propriétés déclaratives dans le corps), `GET /stereotypes`
  (liste plate, pour le rendu), `GET /profiles/{id}/export`, `POST /profiles/import` (JSON
  versionné `profile_format: 1`).
- `PUT /elements/{id}/stereotypes` `{stereotype_ids[]}` : remplace l'ensemble ; refuse un
  stéréotype dont le `base_type` n'est pas celui de l'élément (422) ; revalide les propriétés
  présentes. `ElementRead` gagne `stereotypes: list[StereotypeRef]` (le client est régénéré,
  `make openapi`).
- `create_element`/`update_element` appellent `validate_stereotype_properties` pour les
  stéréotypes déjà appliqués : une règle derrière toutes les portes (leçon de l'IPAM).

Chaque écriture : `require_editor()` en première instruction, une ligne de journal
`action=diagram_scene_replaced|diagram_generated|diagram_frozen|template_created|profile_changed…`
et un enregistrement d'audit du sous-projet 2 (§8).

## 6. MCP

L'adaptateur reste un voisin d'`api/` (une requête = un appel de service, modèles de lecture de
l'API). Pas d'outil qui écrit des coordonnées : la géométrie n'est pas le travail d'un agent.
Fonctions décorées `@server.tool` + `@speaking_plainly`, docstring qui dit quand appeler,
annotation d'écriture, entrée dans la liste complète de `tests/unit/test_mcp_server.py`.

| Outil | Lot | Écriture |
|---|---|---|
| `list_diagrams`, `get_diagram` (nom, source, génération, boîtes/éléments, notes) | 2 | non |
| `create_generated_diagram` (requête ou paquet ; vivante par défaut) | 4 | oui |
| `list_diagram_templates`, `instantiate_diagram_template` | 5 | oui (2e) |
| `list_profiles`, `apply_stereotype`, `remove_stereotype` | 3 | oui (2 derniers) |

`INSTRUCTIONS` du serveur : « pour dessiner un périmètre, génère une vue depuis une requête ou
un paquet plutôt que de poser des boîtes ». La définition des profils reste dans le SPA/HTTP
(donnée de référence rare, relue par des humains). Le décompte « trente-quatre outils » de
CLAUDE.md est mis à jour avec les lots.

## 7. SPA

Section `/diagrammes` existante ; `?diagram=` inchangé. Types uniquement via le client
généré. Chaque chargement passe par `useLatestRequest()`. Nouveaux modules **purs** dans
`frontend/src/lib/` (Vitest, sans DOM) :

### 7.1 Éditeur (lot 1 puis 2)
- `diagramGeometry.ts` : transformation zoom/pan (`toCanvasPoint` généralisé avec `pan`), boîte
  englobante d'une sélection, rectangle de sélection (`rubberBand`), `alignBoxes(boxes, 'left'|
  'centre'|'right'|'top'|'middle'|'bottom')`, `distributeBoxes(boxes, 'horizontal'|'vertical')`
  (≥ 3 boîtes, espacement égal des intervalles).
- `diagramHistory.ts` : pile de **commandes** `{do, undo}` sur l'état de scène (nodes, objects,
  edges), plafond 100, fusion des micro-gestes (un glisser = une entrée). L'annulation ne
  rappelle pas le serveur à part : elle restaure l'état local, l'autosave habituel (anti-rebond)
  enregistre le résultat. Ce qui touche le **graphe** (créer une relation) n'est pas annulable
  par la pile : le bouton propose « supprimer la relation » explicitement.
- `diagramExport.ts` : `serializeSvg(scene, theme)` produit un SVG autonome (styles en ligne,
  palette claire fixe, textes échappés, polices de la pile système) ; PNG = le SVG dessiné sur un
  `<canvas>` (`Image` + `toBlob`), échelle 1x/2x. Aucune dépendance.
- Interactions : sélection au rectangle et `Maj+clic`, `Ctrl/Cmd+A`, déplacement groupé, `Suppr`,
  `Ctrl+Z`/`Ctrl+Maj+Z`, molette = zoom centré sous le curseur, `Espace`+glisser ou bouton du
  milieu = pan, boutons « ajuster à la vue » et 100 %. Tous les gestes ont un équivalent clavier
  (flèches = déplace de 1/10 px, Maj = plus grand) — ce que 0035 laissait en dette est payé ici
  pour le déplacement et le redimensionnement.
- Notes/groupes (lot 2) : palette « Annotations » ; un groupe se redimensionne, déplacer un groupe
  déplace ses enfants (calcul pur `moveWithChildren`) ; déposer une boîte dans un groupe fixe
  `parent_object_id` ; coudes : poignée au milieu d'un segment pour en créer, glisser pour déplacer,
  double clic pour supprimer, « redresser » remet `waypoints = []`.

### 7.2 Profils (lot 3)
Écran `/profils` (nouvelle entrée de `SECTIONS`, groupe `model`) : liste des profils, éditeur de
stéréotypes et de leurs propriétés, import/export JSON. Dans le détail d'un élément : sélecteur
de stéréotypes (filtré sur le type de l'élément) et formulaire des propriétés déclarées
(liste déroulante pour une énumération, indice du motif, astérisque pour obligatoire — le
SPA **affiche** la règle reçue, ne la recalcule pas ; l'API décide). Rendu : `«nom»` au-dessus du
libellé, liseré/teinte de `colour` en plus de la couleur de couche (`LAYER_COLOURS` reste la
base), icône du jeu fermé `STEREOTYPE_ICONS` (table de présentation, comme `LAYER_LABELS`). Aucune
règle d'ArchiMate n'est écrite en TypeScript.

### 7.3 Vues générées et auto-layout (lot 4)
`lib/autoLayout.ts` : `layoutByLayers(graph, { layerOf, order }) -> positions`, pure et
déterministe.
1. Rangées par couche, de haut en bas : motivation et stratégie, **métier**, **application**,
   **technologie** (physique avec elle), implémentation/migration en bas. `layerOf(type)` vient de
   `/metamodel` (`useMetamodel`) ; seul l'**ordre visuel** des couches est une table de
   présentation dans le SPA, pas une règle.
2. Dans une rangée, tri par barycentre des voisins (2 balayages haut/bas), départage par nom puis
   `id` — mêmes entrées, mêmes sorties.
3. Rangées plus larges que `MAX_ROW_WIDTH` repliées en sous-rangées ; groupes ignorés ; espace
   `GAP_X/GAP_Y` constants ; coordonnées ≥ 0 par construction.
4. Un `restrictTo(newIds, existingPositions)` place seulement les ajoutés (rafraîchir) sans bouger
   les survivants.
Écran « Nouvelle vue » : choix de la source (requête enregistrée / paquet + sous-paquets), nom,
« Vivante » / « Figée » (défaut : vivante) ; bandeau de lecture seule + boutons Figer / Rafraîchir /
Dégeler. Les modules de graphe (`GraphDiagram`) restent séparés : l'auto-layout du diagramme et les
anneaux du voisinage sont deux dessins, ne partagent que `BOX_WIDTH/HEIGHT`.

### 7.4 Modèles (lot 5)
Menu « Enregistrer comme modèle », bibliothèque de modèles, assistant d'instanciation (un champ de
recherche d'élément par slot, filtré sur son type/stéréotype, case « créer l'élément » et case
**non cochée par défaut** « créer aussi les relations du modèle »).

## 8. Sécurité

- Lecture : tout appelant authentifié (`require_caller`) pour diagrammes, modèles, profils.
  Écriture : `ea-editor` (`require_editor`), dans `services/` — nouvelle ligne dans
  `test_service_guards.py` pour chaque méthode publique de `ProfileService`, `TemplateService`
  et les ajouts à `DiagramService`.
- Un lecteur : le SPA masque palette, poignées, annulation, import, génération ; l'API refuse (403).
- **Audit (sous-projet 2)** : `diagram.scene_replaced` (comptes ajoutés/retirés/déplacés, pas le
  détail des coordonnées), `diagram.generated`, `diagram.frozen`/`unfrozen`, `template.*`,
  `profile.*`, `element.stereotypes_changed`. Un `PUT /layout` enregistre un seul événement, pas un par
  boîte (autosave fréquent).
- Entrées non fiables : notes en **texte**, jamais HTML ; l'export SVG échappe tout texte et
  n'embarque ni script ni `href` externe ; couleurs `#rrggbb` validées côté API (sinon injection
  de style dans le SVG) ; `icon_key` = enum ; motif regex borné (ReDoS, §3.6) ; import de
  profil/modèle : taille ≤ 256 Ko, version vérifiée, mêmes validateurs que l'écriture manuelle.
- Un modèle ou une instanciation ne contourne jamais `ArchitectureService` : création d'éléments et de
  relations par ses cas d'usage, donc les règles ArchiMate et l'IPAM s'appliquent.
- Journaux : jamais le texte d'une note ni une valeur de propriété (peut être sensible).

## 9. Tests (TDD, écrits d'abord)

- **Unit domaine** : `check_scene` (doublons, cycle, profondeur, parent non-groupe) ;
  `validate_stereotype_properties` (énumération, motif, clé réservée, valeur trop longue) ;
  `missing_required` ; filtre d'un motif dangereux refusé ; `resolve_members`/`diff_members` ;
  validation du corps de modèle (slot de type inconnu, lien incohérent) ; instanciation pure
  (liaison de mauvais type refusée).
- **Unit services** (doubles en mémoire) : conflit de `revision` ; suppression d'un groupe remonte les
  enfants ; `freeze`/`unfreeze` (409 avec annotations) ; instanciation sans relations créées par
  défaut ; ordre « objet puis ligne » n'existe pas ici, mais la cascade d'un stéréotype appliqué.
  `test_service_guards.py` couvre les nouvelles méthodes.
- **Intégration** (conteneurs jetables) : cascade élément→boîtes/stéréotypes, relation→tracé,
  diagramme→objets ; SET NULL des parents ; RESTRICT d'une requête utilisée ; remplacement de
  scène atomique ; unicité des profils ; migration montée/descendue sur une base déjà peuplée.
- **API (e2e backend)** : chaque code d'état ci-dessus, 401/403, enveloppes typées, `ElementRead`
  avec stéréotypes, 422 d'un stéréotype de mauvais type, export/import aller-retour d'un profil.
- **Vitest** : `alignBoxes`/`distributeBoxes`, `diagramHistory` (fusion de gestes, plafond),
  `diagramExport.serializeSvg` (échappement, absence de `<script>`/lien externe, instantané),
  `autoLayout` (déterminisme sur permutation de l'entrée, ordre des couches, `restrictTo`),
  composables, composants (multi-sélection, groupes, coudes, bandeau de vue vivante).
- **Playwright** n'étant pas monté : les parcours visibles (annuler, figer, instancier) sont
  couverts par composants Vitest ; l'E2E reste une dette assumée de CLAUDE.md, pas aggravée.
- `make openapi` à chaque lot qui bouge le schéma ; `make check` vert ; couverture ≥ 90 %.

## 10. ADR à écrire

1. **Objets de vue : notes, groupes, tracés** — table polymorphe, un groupe n'est pas un élément ni
   une composition, `revision`/concurrence optimiste (complète l'ADR 0031 sans le supplanter).
2. **Vues générées : vivante ou figée** — source par FK RESTRICT, figer/dégeler/rafraîchir.
3. **Profils et stéréotypes** — extension par donnée sans toucher au métamodèle (renvoie à 0005),
   propriétés déclaratives, `required` non bloquant, motif sûr.
4. **Modèles de diagrammes** — corps jsonb versionné, slots typés, relations jamais créées en silence.
5. **Historique, export et auto-layout côté SPA** — modules purs, aucun ajout de bibliothèque
   (confirme la règle de l'ADR 0010/0013), format d'export.

## 11. Risques et questions ouvertes (défaut retenu)

| Question / risque | Défaut retenu |
|---|---|
| Vue générée : instantané ou vivante ? | Vivante à la création ; « Figer » pour éditer ; dégeler explicite |
| Une requête change après qu'un diagramme figé l'utilise | Rien ne bouge seul ; « Rafraîchir » montre l'écart |
| Plus de 500 résultats | Refus 422 avec le décompte ; réduire le filtre |
| Annuler et le graphe (relation créée depuis un lien) | Hors pile ; action inverse proposée explicitement |
| Annuler après rechargement | Non : historique en mémoire, par session d'édition |
| Deux éditeurs simultanés | 409 sur `revision`, rechargement guidé ; pas de fusion automatique |
| `required` bloquant à l'écriture ? | Non ; vérifié par le sous-projet 4 via `missing_required` |
| Qui peut éditer les profils ? | Tout `ea-editor` ; rôle dédié seulement si un besoin apparaît |
| Un stéréotype sur plusieurs `base_type` ? | Non : un type de base ; dupliquer le stéréotype si besoin |
| Clé de propriété en collision avec l'IPAM ou entre deux stéréotypes d'un élément | Refus à la déclaration pour les clés réservées ; entre stéréotypes, collision de clé → refus à l'application (422) |
| Regex fournie par l'utilisateur (ReDoS) | Sous-ensemble sûr + bornes de longueur, sans nouvelle dépendance |
| PNG dépend du navigateur (`canvas`, polices) | Acceptable ; le SVG est la référence, le PNG un confort |
| Poids du corps `PUT /layout` (500 boîtes + objets + arêtes) | Quelques centaines de Ko au pire ; plafonds du §5 ; autosave anti-rebondi |
| Modèle devenu invalide si le métamodèle évoluait | Le métamodèle est figé (3.2) ; revalidé à l'instanciation de toute façon |
| Profils partagés entre environnements | Export/import JSON versionné ; pas de synchronisation |
| Taille du sous-projet | Cinq lots séparés ; le lot 1 n'a aucune dépendance backend |

## 12. Dépendances et taille

**Taille : L** (cinq lots, ≈ 2 à 3 semaines d'un développeur au total, lots 1 et 2 en premier).

**Dépend de** : sous-projet 1 (paquets, requêtes enregistrées) pour le lot 4 uniquement ; sous-projet
2 (audit) pour la journalisation — repli : lignes de journal `services/` existantes tant que le
port n'existe pas ; sous-projet 4 consomme `missing_required` (lot 3), sans que le lot 3 l'attende.
Aucune dépendance Python/npm nouvelle.

### Lots livrables

1. **Éditeur confort (SPA seul, aucune migration)** — multi-sélection, zoom/pan, annuler/rétablir,
   alignement/distribution, déplacement au clavier, export SVG/PNG. Valeur : un diagramme lisible,
   rangé et partageable en image dès le premier jour. Aucun changement d'API.
2. **Annotations et tracés + concurrence** — `diagram_objects`, `diagram_edges`, `revision`, `PUT
   /layout` étendu, notes, groupes imbriqués, coudes, outils MCP de lecture des diagrammes.
   Valeur : diagrammes expressifs sans écraser le travail d'un collègue.
3. **Profils et stéréotypes** — tables de profils, validation des propriétés, `GET /stereotypes`,
   application sur un élément, écran `/profils`, rendu stéréotypé, export/import, outils MCP de
   profil. Valeur autonome : le catalogue gagne des propriétés attendues, même sans diagramme.
4. **Auto-layout et vues générées** — `autoLayout.ts`, `POST /diagrams/generate`, vivante/figée,
   rafraîchir, outil MCP de génération (après le sous-projet 1). Valeur : un périmètre complet en un
   clic, rangé par couches.
5. **Modèles de diagrammes** — `diagram_templates`, « enregistrer comme modèle », instanciation,
   outils MCP. Valeur : des schémas récurrents (application + hôte + base…) en quelques gestes, avec
   les bons types et stéréotypes.

Les lots 1, 3 sont indépendants entre eux ; 2 précède 5 (un modèle contient notes et groupes) ; 4 attend
le sous-projet 1 mais n'attend ni 3 ni 5.
