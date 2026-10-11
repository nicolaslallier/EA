# Sous-projet 3 — Échanges (import / export du modèle) — conception

Date : 2026-10-10. Statut : proposition. Dépend du sous-projet 1 (paquets, requêtes enregistrées) pour les périmètres « paquet » et « requête » ; le reste est livrable sans lui.

## 1. Intention

**Dit** : pouvoir ouvrir et échanger le modèle avec Sparx EA, Archi et les autres outils ArchiMate, par le format *ArchiMate Model Exchange File Format* (Open Group, XML, v3.x) et par CSV (éléments, relations, propriétés, à la manière d'Archi et des imports CSV de Sparx). L'import passe par les services, dans une transaction, avec simulation (dry-run) ; l'export peut cibler le modèle entier, un paquet ou une requête enregistrée.

**Hypothèses** (à confirmer, défauts retenus) :
- Sparx EA (16+) sait lire et écrire le format Exchange ; on ne teste pas contre Sparx lui-même (pas d'installation), on teste contre le XSD de l'Open Group et contre un fichier produit par Archi.
- Le « CSV à la Sparx » n'a pas de format public stable : on s'aligne sur la **disposition Archi** (trois fichiers `elements.csv`, `relations.csv`, `properties.csv`), qu'Excel et Sparx savent ouvrir.
- Un échange est une opération ponctuelle, sans besoin d'historique : les fichiers ne transitent pas par MinIO.
- Les fichiers visés sont de quelques Mo (un modèle d'entreprise, pas un entrepôt de données).

## 2. Périmètre

**Dans** : export XML Exchange et CSV ; import XML Exchange et CSV ; périmètre d'export (modèle / paquet avec sous-arbre / requête enregistrée) ; arbre `organizations` ↔ paquets ; vues (diagrammes) avec positions, en XML ; rapport d'import ligne par ligne ; dry-run ; outils MCP ; écran SPA « Échanges ».

**Hors** : XMI/UML (sous-projet 9) ; import/export de fichiers `.zip`/`.gz` en entrée ; XSD validé à l'exécution ; relation vers une relation (Exchange l'autorise, notre graphe non) ; éléments non-ArchiMate (notes, groupes graphiques, images) ; connexions de vues importées comme graphique (une connexion de vue n'est jamais un fait) ; historique des échanges ; mise à jour d'éléments existants à l'import (voir §11) ; format propriétaire Sparx `.eap`/`.qea`.

## 3. Modèle de domaine — cartographie

Le domaine reste pur : `domain/exchange.py` décrit le contenu d'un échange (dataclasses gelées : `ExchangeElement`, `ExchangeRelationship`, `ExchangeFolder`, `ExchangeView`, plus la table des noms de types). Aucune règle ArchiMate n'y est restatée : elles restent dans `domain/archimate/` et sont appliquées par `ArchitectureService`.

**Types.** Le nom Exchange est le `xsi:type` en CamelCase : `snake_case` → `PascalCase` (`business_actor` → `BusinessActor`, `data_object` → `DataObject`, `course_of_action` → `CourseOfAction`). Une table de test vérifie que les 61 types y passent dans les deux sens, sans liste restatée : elle est dérivée de `ElementType` et le test échoue si un membre est ajouté sans correspondant. Cas particuliers :

| Exchange | Nos données |
|---|---|
| `AndJunction` / `OrJunction` | `junction` ; `Or` ⇒ propriété `junction_type = or` (absente = `and`) |
| `Relationship xsi:type="Composition"…"Specialization"` | `RelationshipType` homonyme, 11 types, même dérivation |
| `Access` + `accessType` (`Access`/`Read`/`Write`/`ReadWrite`) | `access_type` (`access`/`read`/`write`/`read_write`) |
| `Association` + `isDirected` | `directed` |
| `Influence` + `modifier` | propriété `modifier` (aucun champ propre) |
| `identifier` (xs:ID) | propriété réservée `source_id` (voir §4) |
| `<name xml:lang>` | `name` ; plusieurs langues : la première, avertissement pour les autres |
| `<documentation>` | `documentation` (20 000 car.) |
| *(aucun équivalent)* `description` | propriété réservée `ea.description` à l'export, rapatriée dans `description` à l'import |
| `<properties>` + `<propertyDefinitions>` | `properties` (`dict[str,str]`) : une définition par clé, type `string` |
| `<organizations><item>` imbriqués | arbre de paquets du sous-projet 1 ; `identifierRef` ⇒ `package_id` de l'élément |
| `<views><diagrams><view>` | `diagrams` + `diagram_nodes` |
| nœud `xsi:type="Element"` + `elementRef` + `x,y,w,h` | `diagram_nodes(x,y,width,height)`, coordonnées absolues (nœuds imbriqués aplatis) |
| `Container`/`Label`, `Connection` | ignorés, comptés dans le rapport (information) |

**Propriétés réservées** : `source_id`, `ea.description`, `junction_type`, `modifier`, `access_type`, `directed` (ces deux derniers seulement en CSV, qui n'a pas de colonne pour eux). Les clés `ea.*` sont retirées des propriétés à l'import ; `source_id` n'est jamais exporté comme propriété mais comme `identifier`.

**CSV (disposition Archi, UTF-8, RFC 4180, virgule, BOM accepté en lecture).**
- `elements.csv` : `ID,Type,Name,Documentation,Specialization` (le dernier est ignoré, avertissement) ; `Type` accepte `BusinessActor` ou `business_actor`.
- `relations.csv` : `ID,Type,Name,Documentation,Source,Target,Specialization` ; `Type` accepte `CompositionRelationship` et `composition`. `Source`/`Target` = `ID` d'un élément du même fichier **ou** d'un élément déjà en base (via `source_id`).
- `properties.csv` : `ID,Key,Value` (l'`ID` vise un élément ou une relation).
- Pas de colonne de paquet ni de vue : le CSV ne porte ni arbre ni diagramme.
- À l'export, une cellule commençant par `=`, `+`, `-`, `@` est préfixée de `'` (injection de formules dans Excel) ; à l'import, un `'` initial suivi d'un de ces quatre signes est retiré. Option `safe_cells` (vrai par défaut).

**Règle de collision.** La clé est `(type, nom)`, déjà unique en base (`element_name_unique_per_type`). Pour chaque élément du fichier :
1. un élément de même `(type, nom)` existe ⇒ **réutilisé** (pas modifié ; sa `description` n'est complétée que si vide, exactement comme le pipeline) ; son `source_id` n'est posé que s'il est vide ;
2. sinon ⇒ créé par `create_element` avec `source_id` = identifiant du fichier.
Une relation est considérée existante si `(type, source, cible)` existe déjà (comme le pipeline : on cherche avant de créer) ⇒ comptée « réutilisée ». Le mode `on_conflict=fail` fait de la présence d'un doublon une erreur de ligne.

**Relation refusée par la matrice** : **rejet ligne par ligne avec rapport**, tout le reste est écrit. C'est le seul comportement utile pour un fichier venu d'un autre outil, dont la matrice (3.0, 3.1 ou 3.2) diffère légèrement de la nôtre ; un échec global rendrait tout fichier réel inimportable. L'option `strict=true` retourne l'échec global (annulation de la transaction) pour qui veut la garantie du « tout ou rien ». Un élément rejeté (nom vide ou trop long, propriété IPAM invalide, doublon sous `fail`) entraîne le rejet de ses relations (« extrémité rejetée »).

**Vues** : importées avec leurs positions (`with_views=true` par défaut en XML). Les vues sont des *visualisations* qui ne possèdent aucun fait : l'import en recrée les boîtes, jamais les connexions. Un nom de diagramme déjà pris ⇒ suffixe ` (import 2)`, ` (import 3)`…. Une vue de plus de `MAX_DIAGRAM_NODES` (500) boîtes est rejetée (rapport). Une boîte dont l'élément a été rejeté est omise (avertissement).

## 4. Données

- **Aucune migration** dans ce sous-projet : `source_id` est une propriété ordinaire dans `elements.properties` ; les paquets et les requêtes viennent du sous-projet 1 ; les vues réutilisent `diagrams`/`diagram_nodes` (migration `0007` pour `width`/`height`).
- Aucun index nouveau : la recherche par `source_id` se fait sur le lot de l'import (carte en mémoire `identifiant → UUID`), pas par requête répétée. Si l'unicité par `source_id` devenait un besoin, ce serait un index partiel sur `properties ->> 'source_id'` ; on ne le crée pas (pas de besoin avéré).
- Fichiers et MinIO : l'import et l'export sont **directs** (le corps de la requête, la réponse en flux). MinIO est la source de vérité des *fichiers* du catalogue (ADR 0036), pas un journal d'échanges ; y déposer chaque export ferait grossir le bucket sans que personne ne le consulte. Une trace légère suffit : une ligne de journal `action=imported|exported` avec compteurs, `dry_run`, format, taille et appelant.
- **Transaction d'import** : une seule session, un `SAVEPOINT` par ligne (`begin_nested`) pour qu'un refus n'empoisonne pas la transaction, un verrou consultatif `pg_try_advisory_xact_lock` (une seule importation à la fois, sinon `409 import_in_progress`). Dry-run : exactement le même chemin, puis `ROLLBACK` — donc les vraies contraintes de la base (unicité, CTE de cycle) décident, pas une imitation.
- **Export** : une transaction `REPEATABLE READ` en lecture seule, éléments et relations lus par pages à curseur (`ORDER BY` clé stable) dans cette même transaction : une lecture cohérente sans tout charger en mémoire.

## 5. API

Routeur `backend/src/ea/api/exchanges.py`, service `services/exchanges.py` (`ExchangeService`, quatrième… cinquième fournisseur du MCP, jamais un dépôt). Bornes déclarées dans `api/schemas.py` et importées par le MCP.

| Méthode | Chemin | Entrée | Réponse |
|---|---|---|---|
| GET | `/exchanges/export` | requête : `format=archimate-xml\|csv`, `scope=model\|package\|query`, `package_id?`, `query_id?`, `with_views=true` | `200` flux (`application/xml` ou `application/zip` de trois CSV), `Content-Disposition` au nom assaini ; `404` paquet/requête inconnu ; `422` |
| POST | `/exchanges/imports` | `multipart/form-data` : `file` (XML) **ou** `elements`, `relations?`, `properties?` (CSV) ; `format`, `dry_run=true`, `on_conflict=reuse\|fail`, `strict=false`, `with_views=true`, `target_package_id?`, `safe_cells=true` | `200 ImportReportRead` ; `409 import_in_progress` ; `413 file_too_large` ; `422 invalid_exchange_file` ; `401`/`403` |

- **`dry_run` vaut `true` par défaut** : écrire exige de dire `dry_run=false`. Un client qui oublie ne détruit rien.
- `ImportReportRead` : `dry_run`, `format`, `sha256` du fichier, `counts` (par catégorie `elements`, `relationships`, `packages`, `views` : `created`, `reused`, `rejected`, `ignored`), `issues[]` (`severity` `error|warning|info`, `code`, `source_id`, `line` ou `path` XML, `message`), `truncated` (vrai au-delà de `MAX_REPORT_ISSUES` = 1 000 lignes ; les compteurs, eux, sont complets).
- Codes d'erreur typés (`domain/errors.py` et une ligne chacun dans `_STATUS` de `api/errors.py`) : `invalid_exchange_file` (XML mal formé, DTD présente, racine inattendue, version d'espace de noms inconnue, CSV sans en-tête), `file_too_large`, `import_in_progress`, `unknown_scope`. Les refus de ligne n'ont pas de code HTTP : ce sont des `issues`.
- Le message d'une erreur XML ne reprend jamais le contenu du fichier au-delà du numéro de ligne.
- Confirmer = rappeler la même requête avec `dry_run=false` et le même fichier ; le rapport renvoie le `sha256` pour que le SPA vérifie qu'il s'agit bien du fichier simulé.
- Le client OpenAPI est régénéré (`make openapi`) ; le téléchargement binaire passe par `fetch` du client (`parseAs: 'blob'`).

## 6. MCP

Deux outils, adaptateurs minces sur `ExchangeService` (trente-quatre → trente-six outils ; mise à jour de l'assertion de liste complète de `tests/unit/test_mcp_server.py` et du compte dans `CLAUDE.md`) :

- **`export_model`** (lecture, `readOnlyHint`) : `format`, `scope`, `package_id?`, `query_id?`. Renvoie **du texte** : le XML Exchange, ou les trois CSV concaténés sous des en-têtes `--- elements.csv ---`. Plafond `MAX_MCP_EXPORT_BYTES` = 1 Mo (même esprit que `MAX_MCP_UPLOAD_BYTES`, l'enveloppe JSON-RPC et le plafond NGINX de 2 Mo à `/api/mcp`) ; au-delà, `ToolError` qui dit de passer par l'écran Échanges et de réduire le périmètre.
- **`import_model`** (écriture, annotation explicite « crée des éléments, ne supprime rien ») : `format`, `content` (texte), ou `elements`/`relations`/`properties` pour le CSV ; `dry_run=true` par défaut ; mêmes options que l'API ; borne `MAX_MCP_UPLOAD_BYTES` (1 Mo) sur le texte. Les `INSTRUCTIONS` du serveur disent : toujours simuler d'abord, lire le rapport, puis refaire avec `dry_run=false`.
- Pas de vue ni d'import de vues via MCP (un diagramme n'a pas d'outil MCP, `CLAUDE.md`) : `with_views` est forcé à `false` côté MCP.
- Aucun outil MCP ne reprend une règle : le rapport vient du service.

## 7. SPA

Nouvelle section « Échanges » (`SECTIONS`, groupe existant le plus proche ; vue paresseuse `features/exchanges/ExchangesScreen.vue`), deux onglets, état dans l'URL (`?tab=export|import`, `?scope=`, `?package=`, `?query=`, `?format=`) selon la règle de `docs/adr/0010`.

- **Export** : format (XML Exchange / CSV), périmètre (modèle ; paquet par sélecteur d'arbre ; requête enregistrée), case « inclure les vues » (XML). Bouton « Télécharger » : `fetch` authentifié → `Blob` → lien objet ; un lecteur y a droit (lecture seule).
- **Import**, trois temps visibles : 1) choisir le(s) fichier(s) et les options (collision, strict, paquet cible) ; 2) « Simuler » → rapport : compteurs par catégorie, tableau des lignes (filtre par sévérité, tri par code, recherche sur `source_id`), mention « rapport tronqué » ; 3) « Confirmer l'import » (bouton désactivé tant qu'aucune simulation du **même fichier** — comparaison du `sha256` — n'a réussi, et caché aux lecteurs : l'API décide, le SPA masque seulement). Après confirmation : même rapport, avec liens vers le catalogue.
- Toutes les requêtes passent par `useLatestRequest()` ; un fichier est conservé en mémoire du composant (jamais `localStorage`). Aucun HTML issu du fichier n'est rendu avec `v-html` : les messages sont du texte.
- Le message de limite de taille est lu depuis la constante exposée par le schéma, jamais recopiée.

## 8. Sécurité

- **XXE / bombe XML** : analyse du XML avec **`defusedxml`** (`defusedxml.ElementTree`), qui refuse DTD, entités et références externes (`forbid_dtd=True`, `forbid_entities=True`, `forbid_external=True`) — c'est la bibliothèque dédiée à cela, maintenue, sans dépendance, alors que `xml.etree` de la bibliothèque standard reste exposé à l'expansion d'entités (« billion laughs »). Toute DTD ⇒ `invalid_exchange_file`. Aucun accès réseau ni disque pendant l'analyse.
- **Taille** : `MAX_EXCHANGE_BYTES` = 20 Mo, lue par morceaux et coupée **pendant** la réception (`413`) — on ne fait pas confiance à `Content-Length`. Plafonds de volume : 50 000 éléments + relations au total, 200 niveaux d'imbrication d'`organizations`, 100 000 ligne CSV max, champs bornés par ceux de `ElementCreate` (nom 200, documentation 20 000, etc.). Le vhost NGINX de l'Infra porte déjà 51 Mo sur `/api/` : pas de changement nécessaire.
- **Zip-bomb** : aucune décompression en entrée (`.zip`/`.gz` refusés, hors périmètre) ; l'export CSV produit un zip mais n'en lit jamais un.
- **Droits** : l'export exige un appelant (`require_caller`), l'import `require_editor()` en première instruction (y compris en dry-run : la simulation exécute des écritures annulées). `tests/unit/test_service_guards.py` couvre `ExchangeService` après son ajout à la liste des services gardés.
- **CSV** : neutralisation des formules (§3) ; encodage UTF-8 imposé, octets NUL refusés.
- **Nom de fichier** de téléchargement : fabriqué par le serveur (`ea-<périmètre>-<date>.xml`), jamais repris d'une entrée.
- **Journal** : jamais le contenu du fichier, seulement compteurs, taille, `sha256`, appelant ; pas de PII.
- Les vues importées ne créent aucun fait ; les éléments créés l'ont été par `create_element` : `validate_ipam_properties` s'applique, un `ip_address` en double devient un rejet de ligne.

## 9. Tests

- **Unitaires** (sans E/S) : `domain/exchange.py` (table des types dans les deux sens pour les 61 + 11 ; réservés), analyse XML (DTD refusée, entités refusées, version 3.0/3.1/3.2 acceptée, langues multiples, doublon de propriété ⇒ dernière gagne + avertissement), CSV (BOM, guillemets, `Type` sous les deux orthographes, injection `=cmd`), sérialisation (ordre déterministe : type, nom ; relations par source, cible, type).
- **Jeux d'exemple versionnés** dans `backend/tests/data/exchange/` : `archisurance-min.xml` (extrait de l'exemple Open Group ; licence vérifiée avant de le committer, sinon un fichier synthétique équivalent), `export-archi.xml` (produit par Archi, saisi à la main une fois), `billion-laughs.xml`, `xxe-file.xml`, `refused-relations.xml` (une `Realization` hors matrice), `three.csv.d/`.
- **Contrat de forme** : l'export est validé contre le XSD `archimate3_Model.xsd` de l'Open Group ; `lxml` en dépendance **de développement seulement**, et le test se saute avec un message si le XSD n'est pas présent (licence du fichier à vérifier avant de l'embarquer).
- **Aller-retour = identité** (intégration, base jetable) : modèle de référence → `export` → base vide → `import` → `export` : octet pour octet identique (ordre déterministe, `source_id` rejoué en `identifier`). Même test en CSV pour éléments/relations/propriétés (sans paquets ni vues). Et `export` → `import` dans la **même** base : 0 créé, tout « réutilisé ».
- **Dry-run** : rapport identique à l'import réel (mêmes compteurs, mêmes `issues`), base inchangée ; deux imports simultanés ⇒ un `409`.
- **Rejet partiel** : fichier à une relation hors matrice ⇒ le reste est écrit, la ligne apparaît ; `strict=true` ⇒ rien d'écrit. Cycle de composition via fichier ⇒ rejet de la ligne.
- **API** : 401 sans jeton, 403 pour un lecteur qui importe (y compris `dry_run=true`), 200 pour un lecteur qui exporte, 413 pendant le flux, 422 typé, en-têtes d'export.
- **MCP** : outils listés, `export_model` au-delà du plafond ⇒ `ToolError`, `import_model` en dry-run par défaut.
- **Vitest + Testing Library** : bouton « Confirmer » désactivé sans simulation du même `sha256`, caché pour un lecteur, tableau du rapport, état d'URL ; E2E Playwright hors périmètre tant que l'outil n'est pas posé (`CLAUDE.md`).
- Couverture ≥ 90 % maintenue ; `make openapi-check` à jour ; aucun test ne sort de la machine.

## 10. ADR à écrire

1. **Échanges : formats, collision et rejet par ligne** — Exchange XML + CSV disposition Archi, import par les services avec `SAVEPOINT` par ligne et dry-run par rollback, clé `(type, nom)` et `source_id`, `dry_run` par défaut, vues importées sans connexions, pas de MinIO (alternatives : import par dépôt, échec global, historique MinIO).
2. **Dépendance `defusedxml`** (et `lxml` en développement pour la validation XSD) — exigence de `CLAUDE.md` pour toute dépendance ; alternatives : `xml.etree` seul, `lxml` en exécution (surface plus grande), analyseur SAX maison.
3. **Session partagée entre cas d'usage** — l'import écrit dans plusieurs services en une transaction, alors que les dépôts ouvrent aujourd'hui une unité de travail par appel ; à trancher : session passée aux dépôts (comme `replace` des documents) ou variable de contexte posée par `ExchangeService`. Défaut proposé : une session explicite, `acting_as`-like pour qu'elle reste invisible des routes.

## 11. Risques et questions ouvertes (défaut retenu)

| Question / risque | Défaut retenu |
|---|---|
| Un élément du fichier existe déjà avec une autre description : mise à jour ? | Non : réutilisé, description complétée si vide seulement ; jamais les propriétés (comme `alimenter-catalogue`). Un mode `update` est un sous-projet futur. |
| Même nom, même type, deux éléments dans le fichier | Fusionnés dans le lot (le premier gagne), avertissement ; la base ne peut pas en tenir deux. |
| Deux relations identiques `(type, source, cible)` avec des noms différents | Une seule créée, la seconde en « réutilisée » + avertissement. |
| Matrices 3.0/3.1 différentes de la 3.2 | Refus par ligne, `code=relationship_not_allowed`, avec le message du service ; jamais de table de repli. |
| Fichier plus gros que 20 Mo | `413` ; découpage par paquet à l'export. Plafond configurable (`EA_EXCHANGE_MAX_BYTES`), pas modifiable à l'exécution. |
| Mémoire à l'import (arbre XML entier) | Acceptée à 20 Mo (ordre de grandeur 200 Mo résidents) ; `iterparse` seulement si le plafond monte. |
| Verrou consultatif : une importation figée bloque les autres | Le verrou est lié à la transaction (`xact`), libéré à la rupture de connexion ; durée limitée par `statement_timeout`. |
| Identifiants non-NCName en CSV (`1`, `a b`) | Acceptés tels quels en CSV ; à l'export XML, préfixés `id-` et normalisés (`[^A-Za-z0-9_.-]` → `_`). |
| Éléments de paquet : l'élément déjà en base reste dans son paquet | Oui ; le paquet du fichier n'est appliqué qu'aux éléments créés (avertissement si différent). |
| Export d'une requête : relations dont une seule extrémité est dans le résultat | Omises ; seules celles dont les deux extrémités sont dans le périmètre sont exportées. |
| Export de vues dont certains éléments sont hors périmètre | La vue est omise si **toutes** ses boîtes ne sont pas dans le périmètre (avertissement dans les en-têtes de réponse `X-Export-Skipped-Views`). |
| Compatibilité réelle avec Sparx EA | Non testée en CI ; vérifiée à la main une fois, résultat consigné dans l'ADR. |
| Volumétrie du rapport | 1 000 lignes max, compteurs complets, indicateur `truncated`. |
| Taille du flux d'export et délai d'un client lent | Pages de 1 000 lignes, aucun tampon complet ; transaction lecture seule `REPEATABLE READ` tenue le temps du flux, un `statement_timeout`/limite de durée de 5 minutes la borne. |

## 12. Dépendances et taille

**Taille : L** (le format XML, deux directions, plus les vues et l'écran).

Dépend de : sous-projet 1 (paquets, requêtes) pour les lots 4 et la portée « paquet/requête » ; ADR 3 (session partagée) avant le lot 2. Ne dépend ni de MinIO ni du pipeline.

Lots livrables, chacun fusionnable et déployable seul :

1. **Domaine + export XML du modèle entier** (S/M) — `domain/exchange.py`, sérialiseur en flux, `GET /exchanges/export`, XSD en test, aller-retour partiel. ADR 1 et 2.
2. **Import XML + dry-run + rapport** (M) — analyse sécurisée, `SAVEPOINT` par ligne, collision, `strict`, verrou, `POST /exchanges/imports` ; ADR 3. Aller-retour complet (hors paquets/vues).
3. **CSV** (S/M) — lecture/écriture des trois fichiers, neutralisation des formules, mêmes routes.
4. **Paquets et vues** (M) — `organizations` ↔ paquets, périmètres paquet/requête (après le sous-projet 1), vues avec positions.
5. **MCP** (S) — `export_model`, `import_model`, `INSTRUCTIONS`, assertion de liste, `CLAUDE.md`.
6. **SPA** (M) — section « Échanges », rapport, confirmation après simulation, client régénéré.
