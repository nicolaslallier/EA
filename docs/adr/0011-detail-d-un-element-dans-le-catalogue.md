# 11. Le détail d'un élément, ouvert depuis le catalogue

Date : 2026-09-07
Statut : Accepté

## Contexte

Le catalogue (`adr/0007`) est un tableau : nom, type, couche, description
tronquée, date. Un `ElementRead` en dit davantage — la documentation, les
attributs libres que l'utilisateur a posés sur l'élément (`p_*` dans le graphe,
voir `db/schema.py`), l'aspect ArchiMate, l'identifiant. Rien de tout cela n'est
visible nulle part dans le SPA : pour lire la documentation d'un élément il
fallait ouvrir le formulaire de modification, c'est-à-dire ouvrir en écriture ce
qu'on voulait seulement lire.

Le nom, dans la ligne, était du texte inerte, alors que c'est exactement ce
qu'on essaie de cliquer.

Deux questions se posaient.

**Où vit « quel élément est détaillé » ?** Les deux panneaux existants du
catalogue — le formulaire, les relations — vivent dans des `ref` locaux. Mais
`CLAUDE.md` pose la règle inverse pour un certain genre d'état : « un écran dont
l'état est une question que l'utilisateur voudrait partager ou défaire garde cet
état dans l'URL ». *Voici cet élément* est précisément cette question ; *voici
mon formulaire à demi rempli* ne l'est pas.

**D'où vient l'élément affiché ?** La ligne du tableau est déjà en mémoire, et
s'en servir aurait évité une requête.

## Décision

**Le nom d'une ligne est un `<button>` qui écrit `?element=<id>` dans l'URL**, et
le catalogue affiche le détail de ce que l'URL nomme. Ni `@click` qui remplit un
`ref`, ni composant ouvert « par en dessous » : un seul chemin de code, que
l'élément ait été cliqué ou que l'adresse ait été collée.

Ouvrir *empile* (`push`) : le bouton *Précédent* referme le détail. Fermer
*remplace* (`replace`) : une dizaine d'éléments ouverts puis refermés
n'enterrent pas la page d'où l'on vient.

**Le détail est relu à l'API** (`GET /elements/{id}`), il n'est pas recopié de la
ligne. Un lien partagé nomme un élément que la page courante — filtrée, paginée
— ne contient pas forcément, et une ligne affichée depuis dix minutes peut être
périmée. Une seule provenance, donc un seul comportement.

**`ElementDetail.vue` ne va rien chercher** : il reçoit l'élément et le dessine.
La lecture appartient à l'écran qui répond de l'URL, ce qui laisse le composant
réutilisable par le prochain écran qui aura un élément à montrer.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Un `ref` local, comme le panneau de relations | Aucun routeur à injecter, tests inchangés | Le détail ne s'envoie pas par lien, *Précédent* quitte le catalogue | Écarté : c'est l'état que `CLAUDE.md` veut dans l'URL |
| Une route à part, `/elements/:id` | Adresse plus jolie, écran plein | Le catalogue disparaît, donc on perd la place dans la liste et les filtres au retour ; deux écrans à charger au lieu d'un panneau | Écarté pour l'instant ; à reprendre si le détail devient éditable en place |
| Réutiliser la ligne déjà chargée | Zéro requête | Un lien partagé ne trouve rien hors de la page courante ; deux chemins de code | Écarté |
| Étendre le formulaire de modification en lecture seule | Un composant de moins | Confond lire et écrire ; le formulaire ne montre ni identifiant, ni dates, ni attributs | Écarté |

## Conséquences

- **Le catalogue dépend désormais du routeur.** Ses tests le montent avec
  `createAppRouter(createMemoryHistory())`, comme ceux du voisinage. Un test qui
  monterait `ElementCatalogue` sans routeur échouerait à `useRoute()`.
- **Une requête de plus par ouverture.** C'est le prix du chemin unique. Elle est
  ponctuelle, déclenchée par un clic, et l'élément affiché est frais.
- **`ASPECT_LABELS` rejoint `LAYER_LABELS`** dans `features/metamodel` : l'aspect
  était stocké et jamais écrit en toutes lettres.
- **Le détail est un cul-de-sac assumé** : il ne propose ni *Modifier*, ni
  *Supprimer* — ces boutons restent dans la ligne — seulement un lien vers le
  voisinage de l'élément, qui répond à la question suivante la plus fréquente.
  Le jour où le détail devient l'endroit d'où l'on édite, c'est une route à part
  qu'il faudra, et cet ADR sera à reprendre.
- **Un panneau à la fois.** Ouvrir le formulaire ou les relations referme le
  détail — sans quoi le même élément apparaîtrait deux fois, une fois en
  lecture et une fois en écriture. C'est la règle que le catalogue appliquait
  déjà entre ses deux panneaux ; le détail s'y plie, en effaçant `?element=`.
- **Les attributs libres sont affichés bruts**, dans l'ordre où l'API les rend.
  Il n'existe encore aucun écran pour les *saisir* ; ce détail est la première
  chose qui les montre.

## Références

- ADR liés : [0007](0007-client-openapi-genere-pour-le-spa.md),
  [0009](0009-association-des-elements-dans-le-spa.md),
  [0010](0010-dessiner-le-voisinage-d-un-element.md)
- Code concerné : `frontend/src/features/elements/ElementDetail.vue`,
  `frontend/src/features/elements/useElementDetail.ts`,
  `frontend/src/features/elements/ElementCatalogue.vue`,
  `backend/src/ea/api/architecture.py` (`read_element`)
