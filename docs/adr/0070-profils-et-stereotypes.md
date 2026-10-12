---
titre: Profils et stéréotypes — étendre le catalogue par la donnée, sans toucher au métamodèle ni exécuter l'expression d'un utilisateur
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/profiles.py, backend/src/ea/domain/model.py, backend/src/ea/domain/approval.py, backend/src/ea/db/models/profile.py, backend/migrations/versions/0016_profiles.py, backend/src/ea/repositories/profile_store.py, backend/src/ea/services/profiles.py, backend/src/ea/services/architecture.py, backend/src/ea/api/profiles.py, backend/src/ea/mcp/server.py, frontend/src/features/profiles/, frontend/src/features/elements/StereotypePanel.vue
---

# 64. Profils et stéréotypes

Date : 2026-10-11
Statut : Proposition

## Contexte

Un architecte type finement ses éléments : « base de données » sur un composant applicatif, « API
externe » sur une interface, avec les propriétés attendues sur chacun (niveau de criticité, code
d'application, responsable). Sparx EA et UML appellent cela un profil et des stéréotypes. Le
métamodèle ([`0005`](0005-archimate-3-2-comme-metamodele.md)) est complet et figé : 61 types, 11 relations,
des règles validées. Ajouter des types pour chaque besoin le rendrait faux ; laisser les propriétés
libres le rendrait inutilisable (la même clé s'écrit de trois manières).

## Décision

**Un stéréotype est une étiquette supplémentaire posée sur un élément d'un type de base, jamais un type
nouveau. Les valeurs restent dans `elements.properties`. La déclaration de ce qui est attendu est de la
donnée de référence. Aucune expression régulière d'un utilisateur n'est jamais exécutée.**

| Point | Choix |
|---|---|
| Modèle | `profiles` → `profile_stereotypes` → `profile_properties` (migration `0016`) ; `element_stereotypes(element_id, stereotype_id)`. Un stéréotype a **un** type de base (`base_type`, chaîne contrôlée par le domaine : un enum PostgreSQL figerait le métamodèle dans le schéma) |
| Règles de relation | Celles du type de base. Un stéréotype n'ouvre ni ne ferme aucune relation |
| Valeurs | Dans `elements.properties`, chaînes ≤ 500 caractères, sans préfixe, sous la clé déclarée — comme l'IPAM ([`0020`](0020-adressage-ip-en-proprietes-du-graphe.md)). Clés réservées refusées à la déclaration : celles de l'IPAM (`IPAM_PROPERTIES`) et `valid_from` / `valid_until` (dates de validité) |
| Forme d'une propriété | `free`, `enum` (1 à 50 valeurs) ou `template`. Le format d'une valeur **présente** est vérifié partout où des propriétés s'écrivent (`update_element`, `apply_stereotypes`) — la leçon de l'IPAM : une règle derrière une seule porte n'est pas une règle |
| **Pas d'expression régulière** | Une première version acceptait un motif `re` filtré par une inspection du texte (groupes quantifiés, alternances, références arrière). Un tel filtre se contourne (`(a\|a)*`, `(a\|aa)+`, quantificateurs empilés `a*a*a*a*b`, classes équivalentes, Unicode) et ne protège pas la boucle d'événements : il est **supprimé**. Le seul « motif » est un **gabarit** : texte littéral où `*` vaut n'importe quelle suite, `?` un caractère, `#` un chiffre. Il se compare par un algorithme à deux pointeurs écrit ici, au plus `len(gabarit) × len(valeur)` pas, avec un gabarit ≤ 64 caractères et une valeur testée ≤ 256. Aucune dépendance (`re2`) n'est ajoutée |
| `required` | **Jamais bloquant à l'écriture** : on crée l'élément, puis on le complète. `missing_required` est exposé pour que le contrôle qualité en fasse une règle |
| Collision de clé | Deux stéréotypes d'un même élément ne peuvent pas déclarer la même clé (422 à l'application) |
| Appliquer = écrire l'élément | `PUT /elements/{id}/stereotypes` verrouille la ligne de l'élément, la met à `guard_element_write(STEREOTYPES)` — un élément approuvé ou verrouillé n'en reçoit aucun tant qu'un approbateur ne l'a pas rouvert — puis la « touche » (nouvelle révision, auteur) dans la transaction qui écrit. Les définitions visées sont verrouillées dans la même transaction : leur type n'a pas changé entre la vérification du service et l'écriture |
| Suppression | `element_stereotypes.stereotype_id` est `RESTRICT` : supprimer une définition appliquée est un 409 `stereotype_in_use` qui donne le décompte. Retirer l'application sur un élément ne touche pas ses propriétés. Changer le type de base d'un stéréotype appliqué est refusé, dans la transaction du dépôt |
| Journal | `profile.created/updated/deleted` (avant/après : le profil et la forme de ses stéréotypes, jamais une valeur) et `element.stereotypes_changed` (les noms des étiquettes). Chaque écriture du dépôt nomme son `audit=` |
| Rendu | `«nom»` au-dessus du libellé, liseré de la couleur `#rrggbb` du stéréotype, icône d'un jeu fermé embarqué par le SPA (`IconKey`, un enum de l'API). La couleur de couche reste le fond |
| Portage | Export et import JSON versionnés (`profile_format: 1`, ≤ 256 Ko lus par morceaux et abandonnés au dépassement), validés par les constructeurs qui servent l'écriture manuelle ; pas de synchronisation entre environnements |
| Agents | `list_profiles`, `apply_stereotype`, `remove_stereotype`. La définition d'un profil reste dans le SPA/HTTP : donnée rare, relue par des humains |

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Ajouter des types d'éléments au métamodèle | Natif | Le métamodèle est ArchiMate 3.2, complet ; un type de plus est une règle de plus à tenir | Écarté |
| Propriétés libres + convention orale | Rien à construire | La même clé s'écrit de trois manières ; aucune aide à la saisie | Écarté |
| Une table `element_properties` typée | Requêtes typées | Casse la règle « un jsonb, sans préfixe » et l'unicité par index partiel de l'IPAM | Écarté |
| Plusieurs types de base par stéréotype | Moins de doublons | Les règles de relation deviennent ambiguës ; on duplique le stéréotype si besoin | Écarté |
| Héritage entre stéréotypes | Réutilisation | Complexité de résolution des clés et des collisions ; hors périmètre | Écarté |
| `required` bloquant | Données complètes d'emblée | On ne peut plus créer un élément avant de connaître tout de lui | Écarté |
| Regex filtrée par inspection du motif | Expressivité | Contournable ; exécutée dans le processus, sur la boucle d'événements, à chaque écriture d'élément | **Écarté** (revue de sécurité) |
| Regex dans RE2 (`google-re2`) | Temps linéaire garanti | Une dépendance native (ADR, `pip-audit`) pour un champ court dont le besoin réel est « trois lettres, un tiret, quatre chiffres » | Écarté |
| Regex dans un sous-processus avec délai | Expressivité | Un processus par validation, à chaque écriture, et à l'import | Écarté |
| Icônes SVG fournies par l'utilisateur | Liberté | Un SVG arbitraire est un vecteur d'injection ; jeu fermé | Écarté |

## Conséquences

- **Un changement de règle n'est pas rétroactif** : durcir l'énumération d'un stéréotype ne revalide pas
  les valeurs déjà stockées ; elles seront refusées à la prochaine écriture de l'élément.
- **Le gabarit est volontairement pauvre** : pas d'alternative, pas de classe de caractères. C'est le prix
  de ne rien exécuter qu'un utilisateur ait écrit ; les tests nomment les motifs adversariaux classiques
  (`(a+)+$`, `(a|a)*$`, `(a|aa)+$`, `(.*a){20}`) et vérifient qu'ils ne sont que du texte, contre une
  valeur de la longueur maximale, en temps borné.
- **`ElementRead` gagne `stereotypes`** : le client est régénéré ; la décoration est faite par
  `ArchitectureService` (un lot de requêtes pour une page), pas par le dépôt du graphe. Le voisinage, les
  analyses et les échanges ne la portent pas.
- **Ni les baselines ni les échanges ne portent les étiquettes** : comme les documents, un élément
  recréé par une restauration n'a plus de stéréotype ; l'export ArchiMate Exchange ne les connaît pas.
- **L'import crée des définitions et n'en applique aucune.**
- Nouvelles tables de profils, donc `make pg-backup` les contient : la sauvegarde reste « tout le modèle »
  ([`0025`](0025-sauvegardes-des-deux-bases.md)).

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md),
  [0033](0033-postgresql-seul-pour-le-graphe.md), [0046](0046-journal-d-audit-append-only.md),
  [0069](0069-objets-de-vue-notes-groupes-et-trace.md)
- Code concerné : `backend/src/ea/domain/profiles.py`, `backend/src/ea/services/profiles.py`,
  `backend/migrations/versions/0016_profiles.py`, `frontend/src/features/profiles/`
