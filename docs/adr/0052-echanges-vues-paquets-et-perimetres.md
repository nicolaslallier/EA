---
titre: Échanges — les vues, les paquets et les périmètres d'export
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/services/exchange_import.py, backend/src/ea/services/exchanges.py, backend/src/ea/domain/exchange.py, backend/src/ea/formats/archimate_xml.py, backend/src/ea/repositories/exchange_store.py, backend/src/ea/api/exchanges.py
---

# 52. Échanges — les vues, les paquets et les périmètres d'export

Date : 2026-10-11
Statut : Proposition

## Contexte

Les lots 1 à 3 (ADR 0048, 0050, 0051) échangent les éléments et les relations. Un catalogue porte aussi des
diagrammes enregistrés (des *vues* : quels éléments sont dessinés, où) et un arbre de paquets (la classification du
sous-projet 1). Le format Exchange sait dire les deux (`views`, `organizations`). Un export doit aussi pouvoir
se limiter à un paquet ou au résultat d'une requête enregistrée, sans fuir un seul élément hors de ce périmètre.

## Décision

**Écriture par les services, jamais par un dépôt.** Les paquets et les vues d'un fichier sont écrits par
`PackageService` et `DiagramService`, dans la transaction partagée de l'import (ADR 0050) : rôle éditeur, journal
d'audit, règles de l'arbre (`check_tree`, `MAX_PACKAGES`) s'appliquent comme pour une personne. Un test lit le
source (`test_exchange_through_services.py`) et échoue si les modules d'échange importent `ea.repositories`
ou `ea.db`, ou atteignent le `_repository` d'un autre objet.

**Deux phases, un seul budget de refus.** Les éléments et les relations sont écrits d'abord (lignes par lots, avec
rejeu). Quand leur sort est connu, on juge **en mémoire** les dossiers et les vues, puis on les écrit par un second
`apply_rows` qui reçoit ce qui reste du plafond de `MAX_REFUSED_ROWS` (100). Seul ce que la base refuse compte dans ce
plafond ; la prévalidation en mémoire n'y compte pas.

**Dossiers (`organizations`) → paquets.**
- Fusion avec l'arbre existant **par chemin** (noms sans casse, du sommet vers le bas, le paquet cible compris) ; un
  paquet de même nom sous le même parent est réutilisé ; rien n'est jamais supprimé ni déplacé.
- Prévalidation (`plan_folders`, pure) : nom que ne peut porter un paquet (vide, plus de 200 caractères, `/`, NUL),
  frère de même nom sans casse dans le fichier (`duplicate_package`), profondeur au-delà de `MAX_PACKAGE_DEPTH`
  (`package_too_deep`), plus de `MAX_PACKAGES` en tout (`too_many_packages`). Un dossier refusé emporte son
  sous-arbre (`parent_rejected`). L'arbre du fichier peut aller à 200 niveaux (lecture) ; le domaine en accepte 12.
- **Seuls les éléments que cet import a créés sont rangés** (`move_elements`, 100 par appel). Un élément déjà là
  n'est pas déplacé, quel que soit son état (ranger est une modification de l'élément, qu'un élément approuvé
  refuserait) : l'import dit `package_not_applied` si le dossier diffère. Un élément listé dans deux dossiers reste
  dans le premier (`package_duplicate_item`).
- `target_package_id` : les dossiers sont créés sous lui et les éléments créés qu'aucun dossier ne liste y sont
  rangés ; inconnu, c'est un 404 avant toute écriture.
- **Verrous.** Le verrou de l'arbre (`TREE_LOCK`) est pris par l'import **avant** les verrous de lignes des éléments
  (`lock_package_tree`) : une écriture de paquet prend le verrou de l'arbre puis les lignes de ses éléments, donc les
  deux se rencontrent dans le même ordre et ne peuvent pas s'interbloquer (test sur PostgreSQL : l'écrivain prend les
  lignes en `NOWAIT` pendant que l'import attend l'arbre).

**Un dossier refusé par la base emporte son sous-arbre.** Si la base refuse un dossier (une course, une collision),
ses descendants sont rejetés *en mémoire* (ils n'ont plus de parent) : jamais créés à la racine, jamais rangés sous un
faux chemin. Ce n'est pas un refus de la base : aucun rejeu, aucun point du plafond de 100 ; le dossier refusé a sa
ligne dans le rapport, ses descendants sont comptés rejetés sans ligne chacun. Test de propriété sur PostgreSQL
(6 graines, refus à la création et au rangement) : un élément est sous le chemin du fichier ou non rangé.

**La clé des noms frères est le `lower()` de la base.** Les index d'unicité sont sur `lower(name)` de PostgreSQL ; ni
`casefold()` (qui réunit `ß` et `SS`, que la base distingue) ni `lower()` de Python (`İ` y donne `i̇`, la base `i`)
ne sont fiables. L'import demande les clés à la base, en un aller-retour pour les noms de l'arbre et du fichier
(`PackageRepository.fold_names`) : il refuse en mémoire exactement ce que la base refuserait. `name_key` (domaine) est
la clé de remplacement des doubles et des tests. Limite : un nom créé par une autre session entre la lecture et
l'écriture reste un refus de la base (typé, sans cascade).

**Les créations sont bornées.** Une création relit et revérifie tout l'arbre, sous les verrous de l'import et de
l'arbre. Mesuré sur la base jetable : 2 100 dossiers et 2 100 éléments, 62 s avant (2 000 paquets créés, verrous
tenus), 3 s après. Un import crée au plus `MAX_NEW_FOLDERS` (200, réglage `EA_EXCHANGE_MAX_NEW_FOLDERS`, 0 à 2 000)
paquets ; les dossiers qui existent déjà ne comptent pas ; l'excédent est rejeté en mémoire (`too_many_folders`).

**Vues.** Des boîtes seulement : jamais une connexion (un lien dessiné n'est pas un fait), conteneurs et étiquettes
comptés et ignorés ; une vue ne crée jamais d'élément, et ses boîtes ne visent que des éléments qui existent
(résolus après la première phase). Coordonnées : nombres finis dans `±100 000`, côtés de 20 à 5 000 (les bornes
de `api/schemas`, comparées par test) ; une boîte qui n'y tient pas est omise avec un code stable
(`invalid_coordinate`, `invalid_box_size`), une vue de plus de 500 boîtes est rejetée (`view_too_large`).
Le nom d'une vue déjà pris reçoit ` (import N)` ; **une vue identique (même nom ou son double suffixé, mêmes boîtes
à l'unité près) est réutilisée**, de sorte qu'importer deux fois le même fichier ne copie rien.

Une vue dont **toutes** les boîtes ont été omises n'est pas créée vide (`view_without_boxes`, avec les avertissements de
ses boîtes) : un diagramme vide serait autre chose que celui du fichier. Une vue qui n'avait aucune boîte reste une
vue. La reconnaissance d'une copie suit la même coupe du nom que sa création (`_is_a_copy_of`), de sorte qu'un nom trop
long pour porter son suffixe n'est pas recréé à chaque réimport.

**Lecture bornée en flux.** Une vue est lue à sa fin (`MAX_NODES_PER_VIEW` nœuds au plus, vérifié au démarrage de
chaque nœud ; `MAX_EXCHANGE_VIEWS` ; `MAX_FILE_VIEW_BOXES` en tout ; `MAX_VIEW_DEPTH`), puis libérée ; chaque
dossier de premier niveau de `organizations` de même (`MAX_ORGANIZATION_DEPTH`, `MAX_ORGANIZATION_ENTRIES`).

**Périmètres d'export.** `scope=model|package|query` : le client nomme un paquet (`package_id`, avec ou sans ses
sous-paquets) ou une requête enregistrée (`query_id`), jamais le filtre. Le service résout le paquet par
`PackageService` et la requête par `SavedQueryService` ; un paquet inconnu, une requête absente, illisible ou qui
nomme un paquet disparu est un 422 `unknown_scope` sans mot de la cause. L'instantané filtre les éléments par le
chemin SQL commun (`_matching`), n'exporte une relation que si **ses deux extrémités** sont dans le périmètre, une
vue que si **toutes ses boîtes** y sont (les autres sont comptées dans `X-Export-Skipped-Views`), et l'arbre des
paquets du sous-arbre demandé (la racine devient un dossier de premier niveau) ou, pour une requête, tout l'arbre.

## Alternatives envisagées et écartées

- Déplacer un élément déjà rangé ailleurs : modification de contenu qui contourne la garde d'approbation.
- Fusionner deux dossiers frères de même nom sans casse : le plan les rejette (le deuxième), plus prévisible.
- Écrire les paquets par leur dépôt : contourne l'audit et le rôle.

## Conséquences

- L'aller-retour (export → base vide → import → export) conserve paquets, rangement, vues et positions
  (comparaison structurelle sur PostgreSQL), et un réimport dans la même base ne crée ni ne déplace rien.
  Ce n'est **pas** une identité pour : les identifiants sans signification (renommés par position) ; un premier
  réimport qui complète le `source_id` d'un élément qui n'en avait pas (une révision) ; les coordonnées arrondies à
  l'unité (Exchange n'a que des entiers) ; une vue dont une boîte a été omise ; un paquet vide qui n'a pas de
  contenu mais est exporté quand même.
- Les journaux ne citent plus un nom venu d'un fichier : tout nom cité par un service passe par `logged()`,
  vérifié par `test_log_names_guard.py` (le journal d'audit garde les noms).
- Nouvelles bornes, toutes lues par un site d'application et couvertes par `test_exchange_bounds.py`.

## Références

- ADR liés : [0042](0042-les-paquets-classement-en-liste-d-adjacence.md), [0048](0048-echanges-formats-collision-et-rejet-par-ligne.md), [0050](0050-session-partagee-entre-cas-d-usage.md), [0051](0051-echanges-csv-disposition-archi.md)
- Code concerné : `backend/src/ea/services/exchange_import.py`, `backend/src/ea/domain/exchange.py` (`plan_folders`, `validate_box`), `backend/src/ea/repositories/exchange_store.py`
