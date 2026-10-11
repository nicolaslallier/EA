# Sous-projet 9 — Gros morceaux — cadrage

Date : 2026-10-10. Statut : proposition (cadrage, pas de conception détaillée).

## 1. Intention

### Ce qui est demandé (dit)

Quatre chantiers lourds, tels qu'on les trouve dans Sparx EA : **A3** autres
notations (UML classes/séquence/états/activités, BPMN, SysML) ; **A4**
modélisation de données (ERD, DDL, import d'un schéma) ; **C3** exigences et
traçabilité (exigence → conception → test, matrice, couverture) ; **C6**
simulation (jetons, durées, goulots) de processus BPMN / flux ArchiMate. Ce
document les **cadre** : faisabilité, approche, ordre, coût, réutilisable.

### Hypothèses

- Le produit reste un outil d'architecture d'entreprise ArchiMate. Il ne
  devient pas un Sparx complet : l'écart d'échelle (des dizaines de notations,
  quinze ans de développement) est le fait central de ce cadrage.
- Les sous-projets voisins sont livrés avant ou avec ce qu'on leur emprunte :
  **1** paquets, **3** échanges (import/export, XMI y compris), **4** matrice de
  relations et contrôles qualité, **6** profils/stéréotypes et éditeur enrichi
  (objets de vue, conteneurs, notes). Ce document ne les redéfinit pas.
- Un seul utilisateur réel aujourd'hui, un catalogue de quelques centaines
  d'éléments : on dimensionne pour ça, pas pour 100 000 objets.
- Les propriétés d'un élément restent une map de chaînes (`properties`, jsonb),
  le SPA ne copie jamais le métamodèle (`docs/adr/0012`).

## 2. Décision d'architecture centrale : un ou plusieurs métamodèles ?

État du code : `elements.element_type` et `relationships.relationship_type` sont
des `Text` (pas d'enum SQL) ; la validité vient de `domain/archimate/`
(61 types, 11 relations, matrice 61x61, `rules.py`), servie par `/metamodel`.
`UNIQUE(element_type, name)` et les CTE récursives (`architecture_store.py`)
sont agnostiques du type. Rien dans le stockage n'interdit un second
vocabulaire ; c'est le **domaine** et le **contrat** (« ArchiMate seul », CLAUDE.md,
ADR 0005) qui l'interdisent.

| Option | Principe | Pour | Contre |
|---|---|---|---|
| **1. Même graphe, espace `uml:*`** | `uml:class`, `erd:table`… dans `elements`/`relationships`, règles propres dans `domain/` | Un seul catalogue, une seule recherche, traversées gratuites | Supersède « ArchiMate seul » ; `rules.py`, `/metamodel`, la matrice, l'analyse d'impact, l'IPAM, les 34 outils MCP et le SPA doivent chacun apprendre qu'un type peut être étranger ; deux sémantiques de « relation » (`impact_follows_direction` n'a pas de sens pour une généralisation UML) ; le coût se paie partout, à perpétuité |
| **2. Modèles séparés** | Une colonne/table `model` : un graphe par notation, passerelles explicites | Isolation nette, chaque métamodèle garde ses règles | Migration sur toutes les tables ; chaque requête, route et outil MCP prend un `model_id` ; la valeur de l'EA (relier l'appli au processus) passe par des passerelles qu'il faut inventer — c'est un second produit |
| **3. Un seul métamodèle, notations en profils et en vues** | ArchiMate reste le seul graphe ; une notation étrangère est (a) un **profil** du sous-projet 6 posé sur des types ArchiMate existants et (b) une **vue** qui sait dessiner autrement | Coût marginal faible ; aucun contrat cassé ; traversées, impact, MCP, recherche inclus d'emblée ; aucune ADR superseding le métamodèle | Fidélité partielle : ce qui n'a pas d'équivalent ArchiMate n'existe pas (séquence, états fins, corps de méthode) |

**Recommandation : option 3, sans exception pour le graphe.** On ne crée
aucun type hors ArchiMate. Ce qui a un cousin ArchiMate entre dans le graphe
sous profil ; ce qui n'en a pas (messages de séquence, régions d'état) est du
**dessin pur**, stocké dans la vue (cf. A3) et non dans le catalogue. C'est la
même greffe que l'IPAM (ADR 0020) : un concept « hors métamodèle » vit dans
`properties` d'éléments existants, avec validation côté `services/`, sans
table ni type nouveaux.

Impact sur le SPA « sans copie du métamodèle » : **nul** avec l'option 3 — les
profils sont servis par l'API comme le métamodèle l'est ; le SPA ne porte que
des tables de traduction (libellés) et des *gabarits de dessin*, jamais une
règle. Avec l'option 1 il faudrait servir deux palettes et deux matrices : le
principe tient encore, mais l'API double de surface.

**Repli assumé** : si un jour un vrai besoin de modèle UML exécutable (génération
de code, round-trip XMI) apparaît, c'est l'option 2 qu'il faudra, pas la 1 :
un projet séparé, pas un mélange. On ne le prépare pas maintenant.

## 3. Chantiers

### 3.1 A3 — Autres notations (UML, BPMN, SysML)

**Valeur utilisateur.** Dessiner un processus au format que les métiers
reconnaissent (BPMN), un diagramme de classes ou de séquence dans un dossier
d'architecture. Valeur réelle : forte pour BPMN, moyenne pour classes, faible
pour le reste dans un outil d'EA.

**Faisabilité par notation.**

| Notation | Voie | Verdict |
|---|---|---|
| **BPMN** | Éléments ArchiMate `business_process`, `business_event`, `business_interaction`, `business_actor/role` ; liens `triggering` (séquence) et `flow` (échange) ; profil BPMN (sous-projet 6) pour les stéréotypes : `bpmn:gateway` (xor/and/or → `junction` ArchiMate, `connector`), `bpmn:task`, `bpmn:startEvent/endEvent` (propriété `bpmn.kind` sur `business_event`) ; une vue à **gabarit BPMN** (couloirs = conteneurs du sous-projet 6, formes BPMN) | **Faire**, par cette voie |
| **UML classes** | Profil sur `application_component` / `data_object` (`uml.kind = class/interface`), attributs et opérations en propriété texte structurée, `realization`/`specialization`/`composition`/`association` déjà ArchiMate | Faire en version réduite, **après A4** (même modèle d'attributs) |
| **UML séquence** | Dessin pur (lignes de vie = éléments existants référencés dans la vue, messages = ordre + texte stockés **dans la vue**). Pas de relation dans le graphe | Option, XL pour le confort d'édition ; **ne pas faire** (§4) |
| **UML états / activités** | Activités ≈ processus BPMN (même gabarit, profil `uml.activity`) ; états : `business_event` + `triggering`, aucune hiérarchie d'états imbriqués | Activités : gratuit via BPMN. États : **ne pas faire** |
| **SysML** | Blocs/exigences/paramétrique : sans cousin ArchiMate hors exigences (cf. C3) | **Ne pas faire** (§4) |

**Approche recommandée.** (1) BPMN via profil + gabarit de vue, sans nouveau type.
(2) Mécanisme générique « gabarit de notation » dans la vue (`notation` +
liste de gabarits de formes servie par l'API, jamais copiée), pour que UML
classes réutilise le même chemin. (3) Aucune relation ni élément en dehors du
métamodèle.

**On ne fait pas.** Import/export XMI et BPMN-XML (relève du sous-projet 3) ;
validation sémantique BPMN complète (soundness, gateways appariées) ; imbrication
d'états ; diagrammes de séquence/temps/communication ; SysML ; génération de code.

**Dépendances.** Sous-projet 6 (profils, conteneurs/couloirs, objets de vue) —
bloquant ; sous-projet 4 (contrôle qualité : « une porte a une entrée et une
sortie ») — utile ; sous-projet 3 si l'import BPMN-XML est voulu.

**Taille.** BPMN : **M** une fois le sous-projet 6 livré. UML classes réduit :
**S** après A4. Le reste : XL non justifié. Total retenu : **M+S**.

**Lots.** A3.1 profil BPMN (tâche/événement/porte) + validation de profil ;
A3.2 gabarit de vue BPMN (formes, couloirs) ; A3.3 profil `uml.class` et gabarit
de classes (après A4).

**Risque principal.** Attente de fidélité : l'utilisateur compare à Sparx ou
Camunda et trouve BPMN « presque ». Parade : nommer le périmètre dans l'UI
(« BPMN simplifié, sémantique ArchiMate »).

**Arrêtons-nous là si** la modélisation BPMN d'un vrai processus (10-20 étapes)
est faisable de bout en bout dans une vue, avec les couloirs, et sans type
hors ArchiMate. On n'ajoute pas de notation de plus avant demande réelle.

### 3.2 A4 — Modélisation de données (ERD / DDL)

**Valeur utilisateur.** Voir quelle table porte quel `data_object`, quelle
application l'utilise ; importer un schéma PostgreSQL existant pour amorcer le
catalogue au lieu de le saisir ; régénérer un DDL de contrôle.

**Modèle.** Table = `data_object` sous profil `erd.table` (propriétés :
`erd.schema`, `erd.engine`). Clé étrangère = relation `association` (ou
`access` depuis un composant, déjà existante) portant `erd.fk_columns` /
`erd.ref_columns`. **Colonnes : défaut retenu, une propriété `erd.columns`
contenant une liste JSON sérialisée** (nom, type, nullable, pk, défaut), validée
par un module pur `domain/erd.py` et rendue par un panneau dédié. Alternative
écartée : un élément par colonne (300 colonnes = 300 éléments qui noient le
catalogue, la recherche et l'analyse d'impact). Plafond connu : une colonne ne
peut pas porter ses propres relations ; si ce besoin naît (lignage par colonne),
on promeut alors les colonnes concernées en éléments. Même philosophie que
l'IPAM : la règle vit dans `services/` + `domain/`, pas dans une table.

**Import : analyseur déterministe, pas le LLM.** Deux sources, une logique :
(a) **introspection PostgreSQL** (`information_schema` / `pg_catalog` en lecture
seule, via un DSN fourni par l'utilisateur côté serveur, jamais stocké) ;
(b) **parseur DDL** (`CREATE TABLE`, `ALTER TABLE ... FOREIGN KEY`) sur un
sous-ensemble documenté PostgreSQL. Le pipeline `alimenter-catalogue`
(Prefect + LLM) est **inadapté** : un DDL est un langage formel, le LLM ajoute
de la non-déterminisme, un coût et un risque d'hallucination sur des colonnes ;
il reste pertinent seulement pour de la prose (docs d'architecture). On
réutilise du pipeline la *forme* : l'import écrit via les mêmes cas d'usage
(`create_element`, idempotence par `element_name_unique_per_type`, ne remplit
que les vides) et un rapport « créé / existant / refusé » comme son artefact.
Entrée recommandée : un use case `SchemaImportService` exposé par l'API et un
outil MCP (l'agent colle le DDL), pas un flow Prefect.

**Génération DDL (sens inverse).** Fonction pure `domain/erd.py` : éléments
`erd.table` d'un paquet/vue → `CREATE TABLE` + contraintes FK, PostgreSQL
uniquement, ordre topologique des dépendances. Livrée comme texte (téléchargement
via le panneau fichiers existant si l'on veut le garder).

**On ne fait pas.** Autres SGBD (MySQL, Oracle) ; vues, index, triggers,
partitions ; migration synchronisante (diff schéma ↔ base réelle, écriture dans
une base) ; rétro-ingénierie de données (volumétrie, profilage) ; modèle
conceptuel/logique/physique en trois niveaux.

**Dépendances.** Sous-projet 6 (profil `erd.*`, validation des propriétés) ;
sous-projet 1 (paquet « schéma » pour grouper les tables) — utile ;
sous-projet 3 (si on veut aussi l'import d'un fichier .sql téléversé : il
passe déjà par MinIO).

**Taille.** **L**.

**Lots.** A4.1 `domain/erd.py` pur (types, colonnes, validation, génération DDL)
+ profil ; A4.2 parseur DDL (sous-ensemble) → plan de création ; A4.3
introspection PostgreSQL → même plan ; A4.4 panneau de colonnes et gabarit de
vue ERD (pied de corbeau) dans le SPA ; A4.5 outil MCP d'import.

**Risque principal.** La largeur du parseur DDL : le sous-ensemble grossit
sans fin. Parade : s'appuyer sur l'introspection (fiable, sans parseur) comme
chemin principal, et refuser explicitement avec le motif toute construction hors
du sous-ensemble documenté, au lieu de deviner.

**Arrêtons-nous là si** l'introspection d'un vrai schéma PostgreSQL du Mac
produit un catalogue exploitable (tables, colonnes, FK) et que la génération
DDL relit la même structure. Le parseur DDL texte n'est écrit que si
l'introspection ne couvre pas le besoin.

### 3.3 C3 — Exigences et traçabilité

**C'est le plus léger des quatre : taille M, et la plus forte valeur par jour
d'effort.** ArchiMate porte déjà le noyau : `requirement`, `constraint`, `goal`,
`driver`, `assessment`, `principle`, `outcome` ; `realization` (conception →
exigence), `influence`, `association` ; et la traversée de graphe existe
(analyse d'impact, ADR 0013 ; voisinage).

**Valeur utilisateur.** Répondre à « cette exigence est-elle réalisée, par
quoi, et testée par quoi ? » et « que casse-t-on si on change cette exigence ? ».

**Minimum à ajouter.**
1. **Profil « Exigence »** (sous-projet 6) sur `requirement` et `constraint` :
   propriétés `req.status` (proposée/approuvée/implémentée/vérifiée/rejetée),
   `req.priority` (must/should/could), `req.source`, `req.id` (identifiant
   lisible stable). Valeurs servies par l'API, pas copiées.
2. **Le test est un élément ArchiMate** : pas de type neuf. Un cas de test =
   `deliverable` (ou `artifact`) sous profil `req.test` (`test.result`,
   `test.last_run`), relié à l'exigence par `realization` (le test « réalise »/
   démontre l'exigence — convention documentée dans l'ADR, la matrice
   ArchiMate l'autorise déjà). Limite assumée : pas de campagnes d'exécution ni
   d'historique ; un résultat = une propriété à jour.
3. **Vue « couverture »** : requête dédiée, pas un nouveau stockage — exigences
   sans `realization` entrante depuis la conception (trou de conception) et
   exigences réalisées sans test (trou de vérification). Une CTE réutilisant
   `impacted_by` / `relations_of` ; le résultat est une liste + un taux, sur
   le modèle de `IpamService` (calcul serveur, SPA qui demande).
4. **Matrice de traçabilité** : c'est la matrice de relations du **sous-projet 4**
   (lignes exigences, colonnes éléments de conception ou tests). C3 n'ajoute que
   le filtre par profil. Pas de matrice propre.
5. **Outils MCP** : `get_coverage` et un filtre par statut — un agent doit
   pouvoir lister « les exigences non couvertes ».

**On ne fait pas.** Workflow d'approbation, versions et baselines d'exigences
(→ sous-projet 1/historique), import ReqIF/Excel (sous-projet 3), liens vers
des gestionnaires de tickets, lancement de tests, exigences hiérarchiques à
numérotation automatique.

**Dépendances.** Sous-projet 6 (profils) bloquant ; sous-projet 4 (matrice)
pour le livrable « matrice » ; sous-projet 1 (paquets) pour grouper par
spécification.

**Taille.** **M.**

**Lots.** C3.1 profil exigence + test, validation des valeurs ; C3.2 requête et
endpoint de couverture ; C3.3 panneau « Couverture » dans le SPA (liste des
trous, filtre statut/priorité) ; C3.4 outils MCP.

**Risque principal.** La convention « test = `deliverable` relié par
`realization` » surprend ; mal posée, elle contamine l'analyse d'impact
(un test « impacté » par une exigence n'est pas une panne). Parade : écrire la
convention dans l'ADR et exclure les éléments de profil `req.test` des
vagues d'impact par défaut.

**Arrêtons-nous là si** la vue couverture liste fiablement les exigences sans
réalisation et sans test sur le catalogue réel et que la matrice du
sous-projet 4 se filtre par profil. Aucune gestion de cycle de vie en plus.

### 3.4 C6 — Simulation de processus

**Valeur utilisateur.** Voir où un processus s'engorge : combien de dossiers
traités par heure, quelle étape est le goulot, combien de temps de bout en
bout, avec hypothèses « et si on double les ressources ? ». Valeur forte mais
**étroite** : elle n'existe que si les processus sont modélisés avec durées et
capacités, ce que personne ne saisit aujourd'hui.

**Approche recommandée.**
- **Entrée** : le sous-graphe d'un `business_process` : étapes = éléments reliés
  par `triggering` (ordre) et `flow` (transfert), portes (`junction`, profil
  BPMN) pour les embranchements. Paramètres en propriétés d'un profil
  « simulable » (sous-projet 6) : `sim.duration` (loi : constante / uniforme /
  triangulaire, en secondes), `sim.capacity` (serveurs parallèles),
  `sim.probability` sur les liens sortants d'une porte exclusive,
  `sim.arrival` sur l'événement de départ.
- **Moteur** : module pur `domain/simulation.py`, sans I/O. File d'événements
  discrète (heap), **horloge simulée** (un entier de secondes qui avance au
  prochain événement), **graine aléatoire injectée** (`random.Random(seed)`) :
  même graphe + même graine = mêmes résultats, donc testable sans `sleep` ni
  aléa. Jetons = dossiers ; une étape = file d'attente + `capacity` ressources.
  Le sous-graphe est lu par `services/simulation.py` (qui exige `require_caller`
  comme tout service) à partir du dépôt existant ; la simulation **n'écrit
  rien** dans le catalogue.
- **Sorties** : par étape — temps d'attente moyen, taux d'occupation, longueur
  max de file ; globales — débit, temps de cycle (moyenne, p90), goulot
  (étape de plus forte occupation). Format JSON via l'API + un outil MCP. Dans
  le SPA : un tableau, et une **animation** simple rejouant la trace sur la vue
  (jetons qui avancent le long des liens, vitesse réglable), obtenue de la
  liste d'événements horodatés déjà calculée — pas de calcul côté navigateur.
- **Borne** : durée simulée et nombre de jetons plafonnés (comme
  `MAX_TRAVERSAL_DEPTH`), déclarés dans `api/schemas.py`.

**On ne fait pas.** Calage statistique sur données réelles (mining de
processus), ressources partagées entre processus, coûts, calendriers de travail,
événements de message et de temporisation BPMN complets, sous-processus
récursifs, optimisation automatique, simulation multi-modèle (UML états).
Premier objectif : processus **séquentiels avec portes exclusives et parallèles**.

**Dépendances.** A3 (BPMN : portes, événements de départ/fin) — bloquant ;
sous-projet 6 (profil `sim.*`) ; sous-projet 4 (contrôle qualité : le graphe est
simulable ? un seul départ, pas de cul-de-sac) — utile, sinon une validation
minimale dans `domain/simulation.py`.

**Taille.** **L** (moteur pur : M ; animation et saisie des paramètres : M).

**Lots.** C6.1 moteur pur + tests déterministes (graine, horloge) ; C6.2 lecture
du sous-graphe, validation de simulabilité, endpoint + outil MCP ; C6.3 saisie
des paramètres (panneau du profil) ; C6.4 tableau de résultats et animation.

**Risque principal.** Résultats de précision illusoire : des durées devinées
produisent des chiffres crédibles et faux. Parade : l'UI affiche les
hypothèses saisies à côté des résultats et marque « scénario illustratif »
tant que les durées n'ont pas été renseignées par l'utilisateur.

**Arrêtons-nous là si** C6.1+C6.2 donnent un goulot juste sur trois processus
réels et reproductible à graine fixe. L'animation (C6.4) est un confort :
la repousser si le tableau suffit.

## 4. Ordre recommandé et ce qu'on abandonne

**Ordre** (les sous-projets 1, 4 et 6 d'abord, car tout dépend du 6) :

1. **C3 exigences et traçabilité** (M) — après le 6 (profils) et le 4
   (matrice). Valeur immédiate, aucun risque d'architecture.
2. **A4 modélisation de données** (L) — importer le schéma réel du Mac est un
   gain tangible ; lots 1, 3, 4 d'abord (pure + introspection + panneau),
   parseur DDL plus tard.
3. **A3 BPMN** (M) — après le 6 ; **UML classes réduit** (S) à la suite de A4.
4. **C6 simulation** (L) — seulement une fois des processus BPMN réellement
   modélisés avec durées ; sinon le moteur tourne à vide.

**À abandonner franchement.**
- **SysML** et **UML séquence / états / temps** : la valeur dans un outil d'EA
  est faible, le coût (éditeur dédié, sémantique propre) XL, et aucune de ces
  notations n'a d'équivalent dans le graphe. Quiconque en a besoin dessine dans
  un outil dédié et référence le fichier via les fichiers du catalogue.
- **Un second métamodèle** (options 1 et 2 du §2) : le coût se paie dans le
  catalogue entier, les 34 outils MCP, l'analyse d'impact et le SPA, pour une
  fidélité que personne n'a demandée.
- **Parseur DDL multi-dialectes** et migration synchronisante : un produit à
  part.
- **L'animation de C6** si le tableau de goulots suffit.

## 5. ADR à écrire (non numérotés)

1. **Notations étrangères : profils et vues, jamais un second métamodèle** —
   précise « ArchiMate seul » (CLAUDE.md, ADR 0005) : ne supersède pas, mais
   interdit explicitement un type hors métamodèle et fixe la règle « dessin pur
   dans la vue, fait dans le graphe ». À écrire avant tout code de A3/A4.
2. **Modèle de données ERD : colonnes en propriété structurée, FK en
   association** — le plafond et la condition de promotion en éléments.
3. **Import de schéma déterministe (introspection puis parseur), pas LLM** — et
   son périmètre PostgreSQL.
4. **Exigences : profil, test = `deliverable` relié par `realization`, couverture
   calculée côté serveur** — avec l'exclusion de l'impact par défaut.
5. **Moteur de simulation pur** — graine injectée, horloge simulée, bornes,
   hors catalogue (aucune écriture).

## 6. Risques et questions ouvertes (défaut retenu)

| Question | Défaut retenu |
|---|---|
| UML/ERD dans le graphe ou à part ? | Un seul graphe ArchiMate, notations par profil + vue (option 3) |
| BPMN : notation entière ? | Non : processus ArchiMate + profil + gabarit de vue |
| Colonnes : éléments ou propriété ? | Propriété JSON sérialisée, promotion au besoin |
| Import de schéma : LLM ou déterministe ? | Déterministe ; le LLM pour la prose seulement |
| Type d'un cas de test ? | `deliverable` sous profil `req.test` |
| Qui stocke un résultat de test ? | Une propriété à jour, sans historique |
| La simulation écrit-elle ? | Jamais ; résultat éphémère, éventuellement exportable en fichier |
| DSN d'introspection ? | Fourni à l'appel, jamais stocké ni journalisé ; lecture seule |
| Sous-projet 6 en retard ? | Tout ce document attend lui ; C3 en dernier recours peut démarrer avec des propriétés libres validées par `services/` (comme l'IPAM) |
| Les entrées MCP/HTTP de l'import respectent-elles `require_editor` ? | Oui : un import écrit, donc rôle `ea-editor` |

Risque transverse : **le sous-projet 6 est le goulot commun**. Si les profils
glissent, A3, A4, C3 et C6 glissent ensemble ; il faut le livrer en premier et
le garder petit (déclaratif, servi par l'API).

## 7. Dépendances et tailles

| Chantier | Taille | Bloquant | Utile | Réutilise |
|---|---|---|---|---|
| C3 exigences | **M** | 6 (profils) | 4 (matrice), 1 (paquets) | impact (0013), types motivation ArchiMate |
| A4 données | **L** | 6 | 1, 3 | `data_object`, MinIO, cas d'usage de création, forme du pipeline |
| A3 BPMN + UML classes | **M + S** | 6 | 4, 3 | `business_process`, `triggering`, `flow`, `junction` |
| C6 simulation | **L** | A3 (BPMN), 6 | 4 | CTE de parcours, `MAX_TRAVERSAL_DEPTH`, horloge/graine injectées |
| Écarté : SysML, séquence, états | XL | — | — | — |

Total des chantiers retenus : environ **2 M + 2 L + 1 S**, sous réserve de la
livraison des sous-projets 1, 3, 4 et 6.
