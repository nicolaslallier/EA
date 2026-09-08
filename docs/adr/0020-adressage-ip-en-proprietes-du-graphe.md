---
titre: L'adressage IP en propriétés du graphe — un sous-réseau est un élément ArchiMate
date: 2026-09-08
statut: Accepté
affects: backend/src/ea/domain/ipam.py, backend/src/ea/services/ipam.py, backend/src/ea/api/ipam.py, backend/src/ea/mcp/server.py, backend/src/ea/db/schema.py, backend/src/ea/repositories/archimate_graph.py, frontend/src/features/ipam/
---

# 20. L'adressage IP en propriétés du graphe

Date : 2026-09-08
Statut : Accepté

## Contexte

Le catalogue décrit déjà l'infrastructure : des `node`, des `device`, du
`system_software`, reliés à ce qu'ils portent. Il ne dit pas **sur quelle
adresse chacun répond**, et c'est précisément la question que l'on pose devant
une alerte à trois heures du matin : « 10.0.1.12, c'est quoi ? ».

Un tableur y répond une fois. Il n'y répond pas deux fois de suite sans
collision : rien n'y empêche deux lignes de revendiquer la même adresse, rien
n'y calcule ce qui est libre, et rien n'y relie l'adresse à ce que la machine
fait tourner. C'est ce dernier point qui décide de l'endroit où l'IPAM doit
vivre : la réponse utile n'est pas « srv-app-01 », c'est « srv-app-01, et la
facturation tourne dessus ». Cette phrase-là, seul le graphe peut l'écrire.

Deux modélisations étaient possibles, et l'arbitrage a été demandé
explicitement.

## Décision

**Une adresse IP est une propriété de l'élément qui répond dessus. Un
sous-réseau est un élément ArchiMate `communication_network` qui porte son
préfixe.** Aucun nouveau label Neo4j, aucun nouveau type d'élément, aucune
seconde base.

| Point | Choix |
|---|---|
| Sous-réseau | Un élément `communication_network`, propriété `p_cidr` |
| Adresse | Propriété `p_ip_address` de l'élément, **une seule par élément** |
| Portée | Propriété `p_vrf`, jamais vide, `default` par défaut |
| Réservations | Propriété `p_ip_reserved` sur le sous-réseau |
| Unicité | Contrainte Neo4j `(p_vrf, p_ip_address)` |
| Types adressables | `node`, `device`, `equipment`, `system_software`, `technology_interface` |
| Appartenance | Calculée par arithmétique de préfixe, jamais stockée |

### Une seule adresse par élément, et c'est ce qui rend l'unicité vraie

C'est le point qui porte tout le reste. Une liste d'adresses dans une propriété
aurait été plus commode à écrire et impossible à contraindre : une contrainte
composite Neo4j ne voit qu'une propriété scalaire. Avec une adresse par
élément, `REQUIRE (e.p_vrf, e.p_ip_address) IS UNIQUE` devient déclarable — et
c'est la seule chose qui survive à deux agents qui lisent « libre » au même
instant. Le service vérifie d'abord, pour donner un message lisible ; la
contrainte tranche.

Une machine multi-domiciliée se modélise alors en deux
`technology_interface` composées dans un `node` — ce qu'ArchiMate demande de
toute façon, et ce qui donne un nom à chaque carte réseau au lieu d'une liste
anonyme.

C'est aussi pourquoi `p_vrf` est **toujours** écrit à côté de l'adresse : une
contrainte composite ne s'applique pas à un nœud auquel il manque l'une de ses
propriétés, et la garantie se serait évaporée exactement là où quelqu'un aurait
oublié de nommer une portée.

### L'appartenance à un sous-réseau se calcule, elle ne se stocke pas

Aucune arête entre une adresse et son sous-réseau, aucune arête entre un
sous-réseau et son parent. Le préfixe le plus long qui contient l'adresse
gagne, comme dans une table de routage : déclarer `10.0.0.0/8` comme plage
d'entreprise ne fait pas basculer les machines de `10.0.1.0/24` sous la plage
large. Une arête stockée aurait été une seconde vérité, libre de contredire
l'arithmétique le jour où quelqu'un modifie un préfixe.

### Une adresse doit tomber dans un sous-réseau déclaré

Refusée sinon, avec un message qui nomme la portée fouillée. Un inventaire qui
accepte des adresses n'appartenant à aucun sous-réseau est un inventaire que
personne ne peut rapprocher de la réalité ; la cause habituelle est une faute
de frappe ou un sous-réseau jamais déclaré, et les deux se corrigent en une
action.

### La convention est vérifiée partout où l'on écrit des propriétés

C'est le coût principal du choix, et il est payé dans
`ArchitectureService.create_element` et `update_element`, qui appellent tous
deux `validate_ipam_properties`. L'adressage vit dans des propriétés libres :
`PATCH /elements/{id}` est une seconde porte dessus, et une règle vérifiée
derrière une seule des deux serait une règle sur laquelle l'inventaire ne peut
pas s'appuyer. Écrire `ip_address` sur un `business_process` est refusé quelle
que soit la porte empruntée.

### Ce que l'IPAM n'ajoute pas

Rien. Un sous-réseau apparaît dans le catalogue, dans le voisinage et dans
l'analyse d'impact comme n'importe quel élément. Supprimer une machine emporte
son adresse sans cascade à écrire, parce que l'adresse *était* la machine. Les
deux requêtes que `ElementFilter` ne sait pas exprimer — « tout ce qui porte
une adresse », « tout ce qui déclare un préfixe » — sont un port séparé,
`IpamRepository`, que la même classe Neo4j satisfait structurellement.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Nœuds dédiés `:IpNetwork` / `:IpAddress`, reliés aux éléments par `:ASSIGNED_TO` | Un modèle IPAM propre ; unicité par nœud ; hiérarchie explicite | Un second métamodèle à côté d'ArchiMate ; deux catalogues à maintenir ; le voisinage et l'impact ne les voient pas sans être modifiés | Écarté — arbitrage explicite de l'auteur du dépôt |
| `p_ip` en texte libre, sans convention ni service | Zéro code | Aucune arithmétique, aucune unicité, aucun sous-réseau — le tableur, dans Neo4j | Écarté |
| Liste d'adresses dans une propriété | Une machine multi-domiciliée en un seul élément | Rend la contrainte d'unicité indéclarable ; impose un parsing à chaque lecture ; `CONTAINS` confond `10.0.1.1` et `10.0.1.10` | Écarté |
| Une table PostgreSQL pour l'IPAM | Clés étrangères, contraintes riches | L'adresse serait séparée de la machine par un serveur ; « relié à quoi » redeviendrait deux requêtes dans deux bases | Écarté |

## Conséquences

**Ce qui est gagné.** « 10.0.1.12, c'est quoi ? » se répond en un appel, avec
l'élément *et* ses liens (`GET /ipam/addresses/{address}`,
`locate_ip_address`). « Donne-moi une adresse libre » se répond sans que
personne ne calcule (`POST /ipam/subnets/{id}/allocate`,
`allocate_ip_address`). Les deux passent par les mêmes règles, que l'appelant
soit un humain ou un agent.

**Ce qu'il faut surveiller.**

1. **La fenêtre de course n'est pas nulle.** Le service lit puis écrit ; entre
   les deux, un autre appelant peut écrire. La contrainte Neo4j attrape le
   perdant, qui reçoit `AddressAlreadyAssignedError` — d'où le soin pris à
   distinguer, dans `_rejected`, laquelle des deux contraintes d'unicité a
   refusé l'écriture : un agent à qui l'on répond « ce nom est déjà pris »
   après avoir perdu une allocation renommerait la machine et recommencerait,
   indéfiniment.
2. **L'inventaire se lit en entier.** Calculer l'occupation d'un sous-réseau
   suppose de connaître toutes les adresses attribuées ; une page en aurait
   donné une occupation fausse, silencieusement. Les deux requêtes sont donc
   sans `LIMIT`, bornées par le nombre de machines modélisées et non par la
   taille du graphe. Si ce nombre atteignait la dizaine de milliers, il
   faudrait compter côté Neo4j plutôt que côté Python.
3. **L'allocation linéaire est bornée** à `MAX_ALLOCATION_SCAN` adresses
   parcourues. Un `/8` compte seize millions d'adresses : au-delà de la borne,
   la réponse honnête est qu'on n'alloue pas linéairement dans un préfixe de
   cette taille, pas un nombre qui arrive une minute plus tard.
4. **Deux contraintes cohabitent sur `:Element`.** Ajouter une troisième
   demandera de compléter `_rejected` de la même façon.
5. **Renommer une propriété de la convention est une migration de données**,
   pas une édition — le graphe n'a pas d'Alembic (`0004`).

## Références

- ADR liés : [0004](0004-neo4j-pour-le-graphe-d-architecture.md),
  [0005](0005-archimate-3-2-comme-metamodele.md),
  [0014](0014-serveur-mcp-pour-les-agents.md),
  [0008](0008-menu-et-routage-du-spa.md)
- Code concerné : `backend/src/ea/domain/ipam.py`,
  `backend/src/ea/services/ipam.py`, `backend/src/ea/api/ipam.py`,
  `backend/src/ea/db/schema.py`, `frontend/src/features/ipam/`
