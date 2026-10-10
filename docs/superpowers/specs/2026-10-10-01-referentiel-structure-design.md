# Référentiel structuré — arbre de paquets et recherche avancée — design

Date : 2026-10-10. Statut : proposition. Sous-projet 1 sur 9 (« modéliser à la Sparx EA »).

## 1. Intention

**Demandé.** B2 : un arbre de paquets/dossiers façon *Project Browser* de Sparx EA, pour
ranger éléments et diagrammes. B3 : une recherche avancée et des requêtes enregistrées.

**Hypothèses (à valider, voir §11).**
- Ranger = classer, pas modéliser : un paquet n'a ni relation ni type ArchiMate.
- Un élément est dans **au plus un** paquet ; hors paquet, il est « à la racine ».
- Les requêtes enregistrées sont partagées entre tous les lecteurs (pas de notion de
  propriétaire privé).
- Le filtre structuré remplace à terme les paramètres épars de `GET /elements`, sans casser
  ceux-ci.

## 2. Périmètre

**Dans** : table `packages` (arbre) ; `elements.package_id`, `diagrams.package_id` ;
CRUD des paquets, déplacement d'éléments et de diagrammes ; objet filtre pur
`ElementQuery` ; `POST /elements/search` ; table `saved_queries` et son CRUD ; outils MCP ;
panneau « arbre » dans la section Éléments et dans Diagrammes ; section Recherche.

**Hors** : droits par paquet (tout paquet suit le rôle global) ; un élément dans plusieurs
paquets ; export/import de paquets (XMI) ; vues générées depuis une requête (sous-projet 6,
qui *consomme* `ElementQuery`) ; recherche plein texte dans les documents (déjà
`search_documents`) ; SQL libre ; tri personnalisé des frères (tri par nom).

## 3. Modèle de domaine

Nouveau `domain/packages.py` (pur) : `Package(id, parent_id, name, description, created_at,
updated_at)`, `MAX_PACKAGE_DEPTH = 12`, `validate_package_name` (strip, 1..200, pas de `/`,
pour pouvoir rendre un chemin lisible `A / B / C`). Erreurs dans `domain/errors.py` :
`PackageNotFoundError`, `DuplicatePackageError`, `CyclicPackageError`,
`PackageNotEmptyError`, `PackageTooDeepError`.

Nouveau `domain/query.py` (pur, dataclasses figées, aucune dépendance framework) :

```
ElementQuery(
  element_types: tuple[ElementType,...], layers: tuple[Layer,...],
  text: str | None,                      # fragment du nom, comme `search` aujourd'hui
  package_id: UUID | None, include_subpackages: bool = True,
  root_only: bool = False,               # n'avoir que les éléments sans paquet
  properties: tuple[PropertyTest,...],   # (key, op in {eq, contains, exists, absent}, value)
  relations: tuple[RelationTest,...],    # (relationship_type, direction in {out,in,any},
                                         #  present: bool, other_type: ElementType|None)
  order: name|created_at|updated_at, limit, offset)
```

Validation dans le domaine : clés de `properties` selon la même règle d'identifiant que
`domain/model.py`, types et relations *demandés au métamodèle* (jamais listés ici),
bornes `MAX_PROPERTY_TESTS = 10`, `MAX_RELATION_TESTS = 5`, `MAX_TEXT = 200`.
Un `RelationTest` dont le type de relation n'est possible pour aucun type d'élément
filtré est **accepté** (résultat vide, pas d'erreur : la règle de métamodèle reste unique).
`ElementFilter` actuel (`domain/ports.py`) devient un alias étroit construit à partir d'un
`ElementQuery` ; un seul chemin SQL (`_matching` étendu).

`domain/saved_queries.py` : `SavedQuery(id, name, description, query: ElementQuery,
created_by, created_at, updated_at)`. Le filtre stocké porte `version: 1`.

Un paquet n'est **pas** un `grouping` ArchiMate : le grouping est un fait du graphe (il porte
des relations, apparaît dans le voisinage et l'impact) ; le paquet est une organisation qui
doit rester invisible des traversées.

## 4. Données

Deux migrations réversibles : `packages` (lot 1 : `packages` et les deux colonnes `package_id`)
puis `saved_queries` (lot 4) :

```
packages(id uuid pk, parent_id uuid null fk packages.id ON DELETE RESTRICT,
         name text not null, description text not null default '',
         created_at, updated_at timestamptz)
  uq_packages_root_name   unique (lower(name))            WHERE parent_id IS NULL
  uq_packages_child_name  unique (parent_id, lower(name)) WHERE parent_id IS NOT NULL
elements.package_id  uuid null fk packages.id ON DELETE RESTRICT, index
diagrams.package_id  uuid null fk packages.id ON DELETE RESTRICT, index
saved_queries(id uuid pk, name text not null unique, description text not null default '',
              query jsonb not null, created_by text not null, created_at, updated_at)
```

Deux index partiels plutôt que `NULLS NOT DISTINCT` : ne dépend pas de la version de
PostgreSQL de l'Infra. Nom de frères insensible à la casse, comme un explorateur de fichiers.
`UniqueConstraint(element_type, name)` des éléments **reste global** (§11, Q3).

**Existant** : les colonnes sont nullables, aucune donnée n'est réécrite ; tous les éléments et
diagrammes actuels sont à la racine. Test de migration sur base peuplée (cf. §9).

**Suppression d'un paquet : refusée s'il n'est pas vide** (sous-paquets, éléments ou
diagrammes), `409 package_not_empty`, avec `?move_contents_to=<id|root>` pour déplacer le
contenu direct (sous-paquets compris) vers un autre paquet ou la racine, dans la même
transaction. Justification : un `CASCADE` qui supprimerait des éléments détruirait des faits
du graphe (et leurs relations) sur un simple clic de rangement ; un `SET NULL` silencieux
éparpille tout à la racine sans que personne l'ait décidé. `RESTRICT` en DDL est le dernier
filet ; le service rend l'erreur lisible. Le déplacement vers un descendant du paquet
supprimé est refusé (`cyclic_package`).

Détection de cycle et de profondeur : CTE récursive sur les ancêtres du nouveau parent
(`repositories/package_store.py`, à l'image du confinement dans `architecture_store.py`),
profondeur bornée par `MAX_PACKAGE_DEPTH`. L'arbre est servi à plat (liste d'adjacence) ;
le SPA le monte.

Requête sur sous-arbre : `package_id IN (descendants(:id))` par CTE récursive, paramètre lié.

## 5. API

Bornes déclarées une fois dans `api/schemas.py` (`MAX_PACKAGES = 2000`, `MAX_PACKAGE_DEPTH`,
`MAX_MOVE_BATCH = 100`, `MAX_QUERY_*`), importées par `mcp/server.py` et couvertes par
`TestTheSameBoundsAsTheHttpAdapter`.

| Méthode | Chemin | Corps / paramètres | Réponse |
|---|---|---|---|
| GET | `/packages` | — | `200 list[PackageRead]` à plat (`id, parent_id, name, description, path, element_count, diagram_count, child_count`, comptes directs) |
| POST | `/packages` | `PackageCreate{name, description, parent_id?}` | `201`; `404` parent, `409 duplicate`, `422 too_deep` |
| GET | `/packages/{id}` | — | `200 PackageRead`; `404` |
| PATCH | `/packages/{id}` | `PackageUpdate{name?, description?, parent_id?}` ; `parent_id: null` explicite = racine (champ présent vs absent via `model_fields_set`) | `200`; `404`, `409 duplicate`, `409 cyclic_package`, `422 too_deep` |
| DELETE | `/packages/{id}` | `?move_contents_to=` | `204`; `404`, `409 package_not_empty`, `409 cyclic_package` |
| POST | `/elements/move` | `MoveElements{element_ids (≤100), package_id\|null}` | `204`; `404 not_found` paquet ou élément |
| GET | `/elements` | + `package`, `include_subpackages=true`, `root_only` | inchangé (`ElementPage`), `ElementRead` gagne `package_id` |
| POST | `/elements/search` | `ElementQuerySchema` (`extra="forbid"`, strict) | `200 ElementPage` ; `422` filtre invalide |
| GET/POST | `/queries` | `SavedQueryCreate{name, description, query}` | `200 list[SavedQuerySummary]` / `201`; `409 duplicate` |
| GET/PATCH/DELETE | `/queries/{id}` | `SavedQueryUpdate` | `200 SavedQueryRead` / `204`; `404` |
| GET | `/queries/{id}/elements` | `limit, offset` | `200 ElementPage` ; `422 unknown_package` si le paquet visé a disparu |
| PATCH | `/diagrams/{id}` | + `package_id` ; `GET /diagrams?package=` | `404` paquet |

`ElementQuerySchema` est la traduction Pydantic stricte du dataclass (enums du domaine,
`properties` : liste de `{key, op, value?}`), avec conversion dans les deux sens ; c'est elle
que `saved_queries.query` stocke (`model_dump`), relue par la même porte. Nouvelles lignes
dans `_STATUS` (`api/errors.py`) pour chaque erreur de §3 (`not_found`, `duplicate`,
`cyclic_package`, `package_not_empty` 409, `package_too_deep` 422, `unknown_package` 422) :
sinon elles sortiraient en `400 invalid_request`. `openapi.json` et `schema.d.ts` régénérés.

## 6. MCP

Dix outils ajoutés (34 → 44), même service, aucun dépôt ; docstrings en intention :

| Outil | Intention (ce que lit le modèle) | Annotation |
|---|---|---|
| `list_packages` | « Montre l'arbre des paquets avec les effectifs : à appeler avant de ranger ou de chercher dans un domaine. » | lecture |
| `create_package` | « Crée un paquet (dossier de classement) ; ce n'est pas un élément ArchiMate. » | écriture |
| `update_package` | « Renomme ou déplace un paquet (parent nul = racine). » | écriture |
| `delete_package` | « Supprime un paquet vide ; sinon passe `move_contents_to`. Ne supprime jamais d'éléments. » | destructif |
| `move_elements` | « Range jusqu'à 100 éléments dans un paquet ou à la racine. Ne change aucun fait du graphe. » | écriture |
| `search_elements` | « Recherche structurée : types, couche, paquet, texte, propriétés, relations présentes/absentes. Préfère-la à parcourir `list_elements` page à page. » | lecture |
| `list_saved_queries` | « Requêtes nommées partagées : réutilise-les plutôt que de reformuler. » | lecture |
| `save_query` | « Mémorise un filtre sous un nom pour l'équipe. » | écriture |
| `run_saved_query` | « Exécute une requête enregistrée, paginée. » | lecture |
| `delete_saved_query` | « Supprime une requête enregistrée (pas les éléments qu'elle trouve). » | destructif |

`list_elements` gagne `package_id`, `include_subpackages`. Les `INSTRUCTIONS` du serveur
mentionnent paquets et `search_elements`. Entrées dans l'assertion de liste complète de
`tests/unit/test_mcp_server.py`. Pas d'outil de rangement des diagrammes (pas d'outils
diagrammes en MCP).

## 7. SPA

- **Section Éléments** : colonne gauche `PackageTree.vue` (arbre déployable, nœud « Racine »,
  compteurs, un nœud sélectionné = filtre). État dans l'URL : `?package=<id>` (absent = tous),
  `?subpackages=0` pour désactiver la récursion ; sélection = `push`, cases à cocher = `replace`.
  Actions éditeur : créer, renommer, déplacer, supprimer (dialogue proposant « déplacer le
  contenu vers… »). Rangement : glisser une ligne du catalogue sur un nœud **et** bouton
  « Déplacer vers… » au clavier. Un lecteur ne voit aucune de ces commandes.
- **Section Diagrammes** : même arbre (réutilisé) pour filtrer la liste ; sélecteur de paquet
  à la création et dans l'édition du nom.
- **Détail d'un élément** : chemin de paquet cliquable (`path` fourni par l'API).
- **Section Recherche** (`/recherche`, groupe `analysis`, entrée dans `sections.ts`) :
  `QueryBuilder.vue` (types et couches proposés depuis `useMetamodel()`, relations proposées
  depuis `/metamodel`, aucune règle recopiée), résultats paginés, panneau
  `SavedQueries.vue` (enregistrer, ouvrir, renommer, supprimer). URL : `?saved=<id>` ou
  `?q=<JSON encodé>` pour un filtre non enregistré (partageable, borné à 2 000 caractères ;
  au-delà, le bouton « Enregistrer » est proposé).
- Chargements via `useLatestRequest()` (un par question : arbre, résultats, requêtes
  enregistrées), saisie texte via `debounce.ts`. Composables : `usePackages.ts`,
  `useElementSearch.ts`, `useSavedQueries.ts`. Client généré uniquement, aucun type recopié.
- Accessibilité : arbre `role="tree"`, flèches clavier, `aria-expanded`.

## 8. Sécurité

Lecture = `require_caller()`, écriture = `require_editor()` en première instruction de chaque
méthode publique du nouveau `PackageService` et de `SavedQueryService` (ou étendues dans
`ArchitectureService`) ; `test_service_guards.py` le vérifie. Aucun SQL libre : le filtre est
un arbre de données compilé par SQLAlchemy Core, clés/valeurs de `properties` en paramètres
liés (`->>` avec clé liée), jamais interpolées ; `text` du filtre via `strpos` comme
aujourd'hui. Bornes de taille sur le JSON stocké (≤ 16 Ko) et de nombre de tests : un filtre
n'est pas un déni de service. Un déplacement par lot est atomique et vérifie l'existence de
chaque id. `created_by` vient du `Caller`, jamais du corps. Une requête enregistrée est une
donnée : relue, elle repasse par la validation du schéma strict (version inconnue → `422`).
Journal : une ligne par écriture (`action=package_created`, `elements_moved`, …).

## 9. Tests (TDD, d'abord rouges)

- **Unit** : `ElementQuery` (bornes, clés invalides, normalisation), noms de paquets,
  aller-retour schéma ⇄ domaine ⇄ JSON stocké, garde-fous (`test_service_guards`), MCP (liste
  complète, bornes identiques à HTTP), `_STATUS` couvre chaque nouvelle erreur.
- **Intégration** (conteneur jetable) : unicité des frères (racine et enfants, casse),
  cycle direct et indirect, profondeur, `RESTRICT`, `move_contents_to`, sous-arbre récursif
  vs non, filtres `properties` (eq/contains/exists/absent), relation présente/absente
  avec/sans `other_type`, combinaisons AND, pagination + `total` ; **migration montée sur une
  base déjà peuplée** (éléments existants à la racine, aller-retour jusqu'à `base`).
- **API** : codes et enveloppes ci-dessus, 401 sans jeton, 403 d'un lecteur qui écrit,
  `PATCH` avec `parent_id: null` vs absent, `GET /elements` rétrocompatible.
- **Frontend (Vitest)** : montage de l'arbre depuis la liste à plat, `?package=` lu/écrit,
  contrôles cachés pour un lecteur, builder → filtre JSON, annulation de la requête obsolète.
- **E2E** (Playwright n'est pas installé : un parcours dans la suite API de bout en bout
  `tests/e2e`) : créer un paquet, y ranger un élément, chercher « sous-arbre », enregistrer
  puis rejouer la requête.

## 10. ADR à écrire

- « Les paquets : un classement en liste d'adjacence, distinct du `grouping` ArchiMate ».
- « Le filtre d'éléments : un objet de domaine pur, partagé par le catalogue, MCP et les
  vues ». Précise `saved_queries` (JSON versionné, validé, aucun SQL libre) ; supersède en
  partie le rôle d'`ElementFilter`.

## 11. Risques et questions ouvertes

| # | Question | Défaut retenu |
|---|---|---|
| Q1 | Un élément dans plusieurs paquets (comme un élément dans plusieurs vues) ? | Non : un seul paquet ; les « vues » multiples sont le rôle des diagrammes. |
| Q2 | Suppression d'un paquet non vide ? | Refus 409 ; `move_contents_to` explicite ; jamais de cascade sur les éléments. |
| Q3 | Unicité `(type, nom)` par paquet plutôt que globale ? | Reste globale (le pipeline et l'IPAM s'appuient dessus) ; assouplir = autre ADR. |
| Q4 | Requêtes enregistrées privées par utilisateur ? | Partagées ; `created_by` enregistré, tout éditeur peut modifier. Privé = ajout ultérieur. |
| Q5 | Paquet supprimé alors qu'une requête le vise ? | Pas de FK dans le JSON ; l'exécution répond `422 unknown_package`, la requête reste éditable. |
| Q6 | Arbre servi en une fois ou paresseux ? | En une fois, plafonné à 2 000 paquets ; paresseux si on dépasse. |
| Q7 | Filtre sur `properties` : valeurs uniquement textuelles (comme le modèle) ? | Oui ; pas de comparaisons numériques. |
| Q8 | Échelle : CTE sur sous-arbre sans index dédié ? | Suffisant (catalogue de quelques milliers d'éléments) ; index `package_id` posés ; mesurer avant d'ajouter une table de fermeture. |
| Q9 | Le pipeline `alimenter-catalogue` doit-il ranger ce qu'il crée ? | Non dans ce lot : il crée à la racine ; paramètre `package` possible plus tard via l'API. |

## 12. Dépendances et taille

Aucune dépendance sur d'autres sous-projets ; **fournit** `packages` et `ElementQuery` aux
sous-projets suivants (dont le 6, vues générées). Taille : **L**.

Lots livrables, chacun vert et déployable seul :
1. **Paquets backend** : domaine, migration, dépôt (CTE), service, routes, `ElementRead.package_id`, tests, OpenAPI régénéré. (M)
2. **Arbre dans la SPA** : `PackageTree`, `?package=`, déplacement, rangement des diagrammes. (M)
3. **`ElementQuery` + `POST /elements/search`** : module pur, compilation SQL (`_matching` étendu). (M)
4. **Requêtes enregistrées** : table, service, routes. (S)
5. **Section Recherche** : builder, résultats, requêtes enregistrées, `?q=`. (M)
6. **MCP** : les dix outils, `INSTRUCTIONS`, tests de liste. (S)

Ordre : 1 → (2 ‖ 3) → 4 → (5 ‖ 6).
