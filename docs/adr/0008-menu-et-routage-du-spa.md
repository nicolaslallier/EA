# 8. Menu de sections et routage du SPA

Date : 2026-09-07
Statut : Accepté

## Contexte

Le SPA n'avait qu'un écran — le catalogue d'éléments — monté en dur dans
`App.vue`. Le backend en sert déjà bien plus : les relations, le métamodèle, le
voisinage d'un élément et l'analyse d'impact n'ont aucune interface. D'autres
sections vont suivre.

Sans navigation, chaque nouvel écran demande de modifier `App.vue` et de bricoler
un état « écran courant », il n'y a pas d'URL partageable pour un écran donné, et
rien n'oblige à charger paresseusement ce que l'utilisateur n'ouvre pas.

`CLAUDE.md` interdit d'ajouter une dépendance structurante sans ADR ; `vue-router`
en est une.

## Décision

**`vue-router` 4, en mode `history`, alimenté par un catalogue de sections
déclaré à un seul endroit.**

* `frontend/src/router/sections.ts` déclare `GROUPS` (les intitulés du menu) et
  `SECTIONS`. Une section porte son chemin, son nom de route, son libellé, un
  résumé, son groupe et — facultativement — sa vue, importée paresseusement.
* `frontend/src/router/index.ts` construit les routes **à partir de** `SECTIONS`
  et n'en tient pas une seconde liste. `createAppRouter(history)` prend son
  historique en paramètre pour que les tests tournent sur
  `createMemoryHistory()`.
* `frontend/src/components/AppNav.vue` rend `menu()` et rien d'autre.
* Une section **sans vue** est déclarée mais pas construite : le menu l'annonce,
  grisée, avec un badge « à venir », et le routeur ne lui crée aucune route. Il
  est donc impossible de cliquer vers un écran qui n'existe pas.
* `App.vue` devient une coquille — en-tête, menu, `<RouterView />` — et ne
  contient plus aucun écran.
* Toute URL inconnue tombe sur `router/NotFound.vue`, qui vit à côté du routeur
  parce que le routeur est seul à le monter.

Ajouter une section, c'est donc ajouter une entrée dans `SECTIONS` — plus un
composant le jour où elle est construite. Rien d'autre à toucher.

## Conséquences

- `frontend/src/router/` s'ajoute à la structure décrite dans `CLAUDE.md`, mise à
  jour dans le même commit.
- Le découpage du bundle suit les sections : chaque vue est un `import()`, donc
  un *chunk* que le navigateur ne télécharge qu'à l'ouverture de la section.
- Le mode `history` suppose que le serveur qui sert le SPA renvoie `index.html`
  pour toute URL inconnue. Vite le fait en `dev` et en `preview` ; un
  déploiement statique devra le configurer, sous peine de 404 au rafraîchissement
  d'une page profonde.
- Le menu n'est pas une autorisation. Le jour où il y aura de l'authentification,
  cacher une entrée ne protégera rien : c'est l'API qui décide, comme l'exige
  `CLAUDE.md`.
- Pas de gestionnaire d'état pour autant : `docs/adr/0002` reste valable, et
  Pinia gardera son propre ADR.
- `vue-router` est la seule dépendance ajoutée ; c'est la bibliothèque de
  référence de l'écosystème Vue, maintenue par la même équipe que Vue 3.
