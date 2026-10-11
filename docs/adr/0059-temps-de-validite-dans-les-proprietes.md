---
titre: Le temps de validité vit dans les propriétés
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/timeline.py, backend/src/ea/services/architecture.py, backend/src/ea/db/models/architecture.py, backend/migrations/versions/0014_validity_indexes.py
---

# 59. Le temps de validité vit dans les propriétés

Date : 2026-10-11
Statut : Proposition

## Contexte

Le métamodèle porte déjà le vocabulaire de l'évolution du SI (`plateau`, `work_package`, `deliverable`, `gap`),
mais aucun élément n'a de date : on ne peut dire ni « cette application est en service du 2024-03-01 au
2027-06-30 », ni « le To-Be est atteint en 2028 ». Il faut un temps, sans nouvelle famille de tables ni nouvelle
règle de métamodèle.

Deux temps existent et sont indépendants. Le **temps de validité** est celui du monde (l'application X est en
service entre deux dates). Le **temps de transaction** est celui du modèle (saisi le 2026-10-10) : le journal
d'audit et les baselines (ADR 0046, 0047). On peut saisir aujourd'hui une mise hors service de 2028 (validité
future, transaction présente), ou corriger en 2026 une date de 2024. Cette décision ne livre que le premier axe ;
le second se composera plus tard (`known_at`, nom réservé, non livré).

## Décision

- Un élément ou une relation porte **`valid_from`** et **`valid_until`** dans `properties`, au format `YYYY-MM-DD`
  canonique. `valid_until` est **inclusive** ; une borne absente est ouverte. Un seul axe de validité, pas
  d'historique des versions d'un fait.
- `domain/timeline.py` (pur) définit `Lifespan` (`read`, `covers`, `overlaps`) et `validate_lifespan_properties`,
  qui suit le contrat de `validate_ipam_properties` : refuse une date mal formée ou `valid_until < valid_from`
  (`InvalidLifespanError`, 422 `invalid_lifespan`), renvoie les propriétés sous leur forme canonique, **retire**
  une date vide (une chaîne vide stockée cacherait l'élément de toute lecture datée). Le message nomme la clé et
  **ne répète jamais la valeur** reçue ; une valeur de plus de 16 caractères est refusée avant d'être analysée.
- `ArchitectureService.create_element`, `update_element`, `complete_element` et `connect` l'appellent à côté de
  l'IPAM : `PATCH /elements/{id}`, l'import d'un fichier d'échange (qui passe par le service) et le complément du
  pipeline (`complete_element`, jugé sur la ligne verrouillée) ne sont pas des portes dérobées.
- Le format de largeur fixe est ce qui rend le stockage en chaîne légitime : l'ordre lexicographique est l'ordre
  chronologique, sous n'importe quelle collation. Le format partiel (`2027-06`, `2027-Q2`) est refusé précisément
  parce qu'il casserait cette propriété.
- La révision Alembic `0014` crée quatre index d'expression partiels (`properties ->> 'valid_from'` et
  `'valid_until'`, sur `elements` et `relationships`, `WHERE ... IS NOT NULL`). Aucune colonne ajoutée.
  **Elle ne refuse jamais de s'appliquer** : une migration qui échoue au démarrage du conteneur est une panne de
  tout le service. Comme `properties` était libre, une valeur déjà stockée sous ces clés est canonisée si c'est
  un jour, retirée si elle est vide, et **déplacée** sous `legacy_valid_from` / `legacy_valid_until` sinon : rien
  n'est perdu, rien n'est indexé de travers. La migration ne touche pas au journal d'audit (comme la `0013`).
  Elle est écrite pour ne pas dépendre des données : aucun `CAST` en SQL ; une valeur de plus de 16 caractères
  est déplacée par une instruction SQL sans jamais être lue ; les valeurs distinctes plus courtes sont jugées en
  Python puis appliquées en **une** passe par jointure (trois passes par table et par clé, quel que soit le
  nombre de valeurs) ; l'index porte sur `properties ->> clé`, une expression totale qui ne peut lever sur
  aucune ligne. `CREATE INDEX` sans `CONCURRENTLY` bloque l'écriture le temps de la construction (de l'ordre de
  la seconde pour quelques dizaines de milliers de lignes) ; `lock_timeout = 10s` borne l'attente du verrou.
  Une clé `legacy_*` déjà présente n'est jamais écrasée : la valeur prend `legacy_<clé>_2` … `_9`, et si les neuf
  sont prises elle est ajoutée (après un saut de ligne) à la dernière.
  Testée sur 5 000 lignes hostiles (`9`×100 000, `infinity`, chiffres pleine chasse, signes, années hors plage),
  la chaîne descendue puis remontée.
- Une baseline capturée avant la `0014` peut contenir n'importe quelle chaîne sous ces clés : sa lecture
  (`baseline_store`) passe par `readable_lifespan_properties`, qui ne lève jamais et met de côté sous
  `legacy_*` ; une restauration n'écrit donc jamais une date illisible dans le modèle.
- L'appartenance à un plateau reste **calculée** (cible d'une `composition` ou `aggregation` partant du plateau),
  jamais stockée en double (ADR 0061).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| (a) dates dans `properties` | Même schéma que `cidr` / `ip_address` (ADR 0020) ; visible partout (catalogue, voisinage, impact, MCP, import) ; une migration d'index seulement | Chaînes : la validité repose sur la validation d'écriture | Retenu |
| (b) table `element_lifespans` | `DATE` natif, clé étrangère | Une jointure dans chaque CTE, un dépôt, une cascade, un ADR de stockage | Écarté |
| Bitemporel complet | Réponse à « tel que nous le croyions le… » | Hors besoin ; le temps de transaction est l'audit et les baselines | Écarté |
| Migration qui refuse une date mal formée | Rien n'est réécrit | Panne de tout le service au démarrage | Écarté : on déplace, on ne refuse pas |

## Conséquences

- La validité des dates repose sur la validation d'écriture. Tout nouveau chemin d'écriture de `properties` d'un
  élément ou d'une relation doit appeler `validate_lifespan_properties`. La restauration d'une baseline réécrit
  des lignes telles qu'elles étaient capturées : elles ont été validées à leur écriture.
- `PATCH` remplace toute la map `properties` : un client qui n'envoie pas les dates les efface. Le SPA renvoie la
  map complète ; le pipeline n'écrit jamais `properties` (il complète).
- Le jour est la granularité. Les trimestres et les mois sont affichés par l'échelle de la frise, jamais stockés.
- `downgrade` retire les index ; les dates canonisées et les clés `legacy_*` restent, ce sont des propriétés
  ordinaires.

## Limite connue de la révision `0014`

Si une ligne portait déjà une clé `legacy_valid_from` / `legacy_valid_until` **avant** la `0014`, la `0014` la
remplace par la valeur illisible qu'elle met de côté. Ces noms sont introduits par cette révision : aucune
donnée saisie avant elle ne les porte, sauf coïncidence volontaire. La `0014` étant fusionnée et peut-être
appliquée, elle n'est pas modifiée rétroactivement, et une `0015` ne pourrait pas rendre une valeur déjà écrasée :
**limite acceptée, consignée ici**. `readable_lifespan_properties` (lecture des baselines) reste, elle, sans perte
de ce genre : elle ne fait que lire.

## Références

- ADR liés : [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0033](0033-postgresql-seul-pour-le-graphe.md),
  [0060](0060-le-parametre-as-of-des-lectures.md), [0061](0061-plateaux-ecart-et-feuille-de-route.md)
- Code concerné : `backend/src/ea/domain/timeline.py`, `backend/src/ea/services/architecture.py`,
  `backend/migrations/versions/0014_validity_indexes.py`
