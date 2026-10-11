---
titre: Échanges CSV à la disposition d'Archi : trois fichiers, un zip à l'export, aucune archive à l'import
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/formats/csv_files.py, backend/src/ea/domain/exchange.py, backend/src/ea/services/exchanges.py, backend/src/ea/api/exchanges.py, backend/src/ea/api/body_limit.py, backend/src/ea/services/exchange_admission.py
---

# 51. Échanges CSV à la disposition d'Archi : trois fichiers, un zip à l'export, aucune archive à l'import

Date : 2026-10-11
Statut : Proposition

## Contexte

L'ADR 0048 a posé l'échange du modèle en XML Exchange ; Archi sait aussi échanger trois CSV (`elements.csv`,
`relations.csv`, `properties.csv`), format que les tableurs ouvrent. Tout ce qui garde le XML (porte d'entrée
dans l'ordre jeton, rôle, taille, lecture ; plafond d'octets ; porte d'analyse ; registre d'identifiants ; moteur
d'import par lots ; rapport borné) doit valoir pour le CSV sans qu'aucun de ces garde-fous soit recopié. Un CSV
a en plus deux dangers que le XML n'a pas : un tableur exécute comme une formule une cellule qui commence par
`=`, `+`, `-` ou `@`, et le module `csv` de Python a un état global (`field_size_limit`).

## Décision

- **Même moteur.** `formats/csv_files.py` produit les mêmes `ExchangeElement` / `ExchangeRelationship` que le
  lecteur XML, qui passent par `accept_element` / `accept_link` puis par `ImportRun` : aucune écriture directe.
  Types et colonnes viennent du métamodèle (`lookup_element_type`, `lookup_relationship_type`, dérivés de
  `ElementType` et `RelationshipType`), les deux orthographes (`BusinessActor`, `business_actor`) sont lues.
- **Format.** UTF-8 (BOM accepté), virgule, toutes les cellules entre guillemets, CRLF. `properties.csv` =
  `ID, Key, Value`, validées par `fold_properties` comme en XML (clé identifiant simple, valeur bornée, NUL
  refusé, `ea.*` retiré). `ea.description` porte la description.
- **Écart au plan : `ea.access_type` et `ea.directed`.** Le plan écrivait `access_type` et `directed`. Or une
  propriété utilisateur peut s'appeler ainsi : elle aurait été avalée à la relecture, et `directed=true` aurait
  fléché une association qui ne l'est pas. Les attributs de la relation voyagent sous le préfixe réservé `ea.`,
  que nulle propriété ne peut prendre. Un accès sans type est écrit `access` (comme le XML), pas `write`.
- **Neutralisation des formules** (`guard_cell`). Une cellule est préfixée de `'` si, abstraction faite de
  ses `'` de tête, elle commence par une tabulation ou un retour chariot, ou si son premier caractère qui n'est
  ni blanc, ni de contrôle, ni invisible (espace sans chasse, BOM, dérogation bidi : catégories `Cc`/`Cf`) est
  `=`, `+`, `-` ou `@`. Sont gardés : identifiants, noms, documentation, sources et cibles, valeurs de
  propriétés. `unguard_cell` retire exactement un `'` dans exactement ces cas : c'est l'inverse de `guard_cell`
  pour toute chaîne (testé, y compris `-Legacy`, `'=x`, `''-b`), donc un aller-retour n'accumule aucune
  apostrophe. `safe_cells=false` (requête à l'export, partie du formulaire à l'import) coupe la garde ; il doit
  être le même des deux côtés.
- **Lecteur en flux borné.** `csv.reader(strict=True)` est alimenté ligne à ligne par `_Lines`, qui décode au
  fil de l'eau, refuse un NUL ou un autre encodage que UTF-8 et coupe à `MAX_CSV_RECORD_CHARS` (100 000
  caractères par enregistrement, guillemets multi-lignes compris ; `readline` reçoit la borne plus un : une ligne
  de 20 Mo sans saut n'est jamais lue jusqu'au bout). `MAX_CSV_ROWS` (100 000 enregistrements par fichier, lignes
  vides comprises) et `MAX_EXCHANGE_ITEMS` s'appliquent pendant la lecture. `csv.field_size_limit` n'est jamais
  modifié. En-têtes : casse et ordre libres, colonnes en trop ignorées, colonne répétée ou manquante refusée,
  ligne plus large que l'en-tête (contenu non vide) refusée, ligne plus courte complétée. Un refus de fichier ne
  cite que le nom du fichier et un numéro de ligne, jamais le contenu.
- **Rejets de ligne** (codes stables, rapport borné à 200 caractères nettoyés) : `missing_identifier`,
  `duplicate_identifier`, `unknown_type`, `missing_end`, `too_many_properties`, jonction ignorée, et par le
  moteur `unknown_end` pour une référence pendante. Une ligne sans identifiant ne cascade pas (rien ne peut la
  désigner). Le type inconnu n'est pas répété dans le message.
- **Identifiants.** L'export passe par le même `IdentifierRegistry` que le XML (aucune collision silencieuse ;
  un `source_id` inutilisable est remplacé par `id-<uuid>` et journalisé en `export_adjusted`).
- **Export** : `GET /exchanges/export?format=csv` renvoie `application/zip` de trois entrées aux noms fixes,
  nom de fichier `ea-<périmètre>-<date>.zip` fabriqué par le serveur, octets déterministes (date `1980-01-01`
  dans l'archive). Les trois fichiers sont écrits dans des fichiers temporaires en mémoire puis sur disque
  au-delà de 1 Mo, puis assemblés (limite connue : l'écriture du zip est synchrone sur la boucle).
- **Export sans garde** : `safe_cells=false` est étiqueté, dans le nom (`ea-<périmètre>-<date>-raw.zip`) et dans
  l'en-tête `X-Export-Cells: raw` (`safe` sinon). Tout appelant authentifié peut le demander : c'est son
  téléchargement, et le fichier ne lui donne rien qu'il ne lise déjà.
- **Import** : `POST /exchanges/imports` avec `format=csv` et trois parties `elements` (obligatoire),
  `relations`, `properties`. **Aucune archive n'est jamais ouverte** à l'import : la surface d'une bombe zip, d'un
  chemin `../` ou d'un lien symbolique n'existe pas. Chaque format n'accepte que ses parties (`file` pour le
  XML, les trois et `safe_cells` pour le CSV), les parties inconnues ou répétées sont refusées. Le plafond
  d'octets vaut pour la somme des fichiers (la route lit chaque partie dans ce qui reste du plafond). La porte
  d'analyse, le sémaphore, la place prise avant lecture (0048), `dry_run=true` par défaut et le rôle éditeur
  sont ceux du XML.
- **Empreinte.** Un seul fichier : le `sha256` du fichier ; plusieurs : chaque fichier à sa place, de sorte que
  les mêmes octets envoyés comme `relations` ou comme `properties` ne se confondent pas.

## Alternatives envisagées et écartées

- Accepter le zip exporté à l'import : confortable, mais ouvre toute la surface des archives hostiles pour un
  gain de trois sélections de fichier. Écarté ; à rouvrir avec un ADR si le besoin est réel.
- Régler `csv.field_size_limit` : état global du processus, partagé par tout autre code qui lit un CSV.
- Noms `access_type` / `directed` du plan : collision avec les propriétés utilisateur, voir plus haut.
- Une apostrophe sans test de réversibilité : le nom légitime `-Legacy` aurait perdu ou gagné une apostrophe à
  chaque tour.

## Conséquences

- L'aller-retour CSV est une identité structurelle sur PostgreSQL (aussi pour 10 000 éléments, des noms
  `=1+1`, `-Legacy`, des valeurs commençant par tabulation, espace, `@`), sauf les cas suivants, montrés par les
  tests plutôt que masqués : les identifiants qui ne signifiaient rien sont renommés par position (comme en XML) ;
  un `source_id` inutilisable est remplacé ; un nom contenant un saut de ligne ou un caractère de contrôle est
  exporté puis refusé à l'import (`control_character`, une ligne rejetée) ; un export fait avec `safe_cells=false`
  puis réimporté avec la garde (le défaut) change un nom qui commence par `'` suivi d'un signe ; les jonctions et
  la colonne `Specialization` ne sont pas stockées ; la documentation d'une relation n'est pas stockée.
- Le tableur qui ouvre le CSV montre l'apostrophe de garde : c'est le prix de la neutralisation, désactivable.
- Les bornes `MAX_CSV_ROWS` et `MAX_CSV_RECORD_CHARS` sont lues par un site d'application et couvertes par le
  test des bornes et par des tests qui les abaissent.
- Rien de cela n'a été déployé : le statut reste *Proposition*.

## Références

- ADR liés : [0048](0048-echanges-formats-collision-et-rejet-par-ligne.md), [0049](0049-defusedxml-et-validation-xsd.md), [0050](0050-session-partagee-entre-cas-d-usage.md)
- Code concerné : `backend/src/ea/formats/csv_files.py`, `backend/src/ea/domain/exchange.py`, `backend/src/ea/services/exchanges.py`, `backend/src/ea/api/exchanges.py`
