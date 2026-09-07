# 9. Associer deux éléments depuis le SPA

Date : 2026-09-07
Statut : Accepté

## Contexte

`docs/adr/0007` a livré le premier écran — le CRUD des éléments — et a remis
l'interface des relations « à la vue graphe ». `docs/adr/0008` a transformé le
SPA en coquille routée et a déclaré « Relations » comme une section *à venir*.
Le besoin est arrivé avant la vue graphe : un catalogue d'éléments sans liens
n'est pas un modèle d'architecture, c'est une liste. Le backend savait déjà créer, lister et supprimer un lien, et publier les
relations que le métamodèle autorise entre deux types
(`GET /metamodel/relationships`). Rien de tout cela n'était atteignable.

Deux obstacles se sont présentés.

**Un lien ne se lit pas seul.** `RelationshipRead` porte les *identifiants* et
les *types* de ses deux extrémités, jamais leurs noms. C'est délibéré : le nom
appartient à l'élément et change sans le lien, donc le dénormaliser sur l'arête
le rendrait faux au premier renommage (`domain/model.py` le documente). Mais
`GET /relationships?element_id=` ne permet alors pas d'afficher une seule ligne
sans une requête supplémentaire par extrémité.

**`neighbourhood?depth=1` répond presque, mais pas à cette question.** Il
renvoie le sous-graphe induit par les voisins, donc *aussi* les arêtes entre
deux voisins, qui ne sont pas des relations de l'élément affiché.

## Décision

**`GET /elements/{id}/relationships` renvoie un `GraphRead`** : les liens
directs de l'élément, et les éléments aux deux bouts.

* Un `GraphView` plutôt qu'une liste de relations, parce que c'est exactement ce
  que le port déclare déjà pour les traversées : les nœuds *et* les arêtes,
  pour que l'appelant n'ait jamais à recoudre deux listes.
* Le Cypher part de l'élément et suit ses arêtes dans les deux sens, sans
  chemin de longueur variable : seules les relations directes remontent.
  `OPTIONAL MATCH`, pour qu'un élément sans lien renvoie quand même lui-même —
  un enregistrement vide serait indistinguable d'un élément absent.
* Le domaine n'apprend rien de nouveau : `Relationship` ne gagne aucun champ.

**L'interface est un panneau porté par un élément**, pas un tableau de liens.
Associer se fait en regardant un élément ; une liste de tous les liens du
référentiel obligerait à choisir les deux bouts à l'aveugle. Ce panneau est donc
atteignable par deux chemins, qui montent le même composant :

* un bouton *Relations* sur chaque ligne du catalogue — le chemin rapide, quand
  on est déjà en train de regarder l'élément ;
* la section `/relations` que `0008` avait déclarée, qui demande d'abord de quel
  élément on parle. C'est ce qui la fait passer d'« à venir » à construite.

**Le formulaire ne connaît aucune règle ArchiMate.** Il demande le couple
(source, cible) à `GET /metamodel/relationships` et n'offre que la réponse. Le
sens du lien est une question posée à l'utilisateur — « cet élément → l'autre »
ou l'inverse — parce qu'il change le couple interrogé, donc la réponse : un
service applicatif *sert* un processus métier, l'inverse n'existe pas.

## Conséquences

- Le SPA ne réimplémente toujours pas les 3721 cellules de l'annexe B : une
  règle corrigée dans `domain/archimate/rules.py` change ce que le formulaire
  propose, sans toucher au frontend.
- L'utilisateur ne peut pas choisir une relation que l'API refuserait. Il peut
  encore en choisir une que l'API refuse pour une raison qui dépasse les types
  — une composition qui fermerait un cycle — et le message revient tel quel
  dans le panneau.
- Deux endpoints répondent maintenant sur les relations d'un élément :
  `GET /relationships?element_id=` (les liens seuls, paginés) et celui-ci (les
  liens et leurs extrémités). Le premier reste utile à un client qui a déjà les
  éléments ; s'il ne trouve jamais d'usage, il pourra être retiré.
- Les traversées `/neighbourhood` et `/impact` restent sans interface. Elles
  demandent un dessin, pas un tableau, et c'est un autre travail.
