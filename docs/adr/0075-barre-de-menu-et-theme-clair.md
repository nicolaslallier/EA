---
titre: Barre de menu supérieure et thème clair par défaut
date: 2026-10-17
statut: Proposition
affects: frontend/src/App.vue, frontend/src/components/AppNav.vue, frontend/src/components/ThemeToggle.vue, frontend/src/lib/theme.ts, frontend/src/main.ts, frontend/index.html, frontend/src/style.css
---

# 75. Barre de menu supérieure et thème clair par défaut

Date : 2026-10-17
Statut : Proposition

## Contexte

Le menu de l'ADR [0008](0008-menu-et-routage-du-spa.md) était une colonne à
gauche : cinq groupes, une vingtaine de sections, 15 rem pris à chaque écran —
alors que la matrice, le tableau de bord et le canevas des diagrammes veulent
toute la largeur. Les résumés des sections n'étaient visibles qu'en infobulle.

Le thème suivait `prefers-color-scheme` : un poste réglé en sombre recevait un
fond quasi noir (`#17151c`) que personne n'avait choisi pour cet outil, et rien
ne permettait d'en sortir. Trois jetons seulement (`--text`, `--border`, `--bg`)
décrivaient toute la palette.

## Décision

**Une barre supérieure, un menu déroulant par groupe.** `App.vue` pose une bande
bleu profond pleine largeur : la marque « EA », puis `AppNav`, puis la date de
vue, l'état du backend, l'utilisateur et le bouton de thème. Le contenu prend
toute la largeur dessous. `AppNav` rend toujours `menu()` et rien d'autre (la
règle de l'ADR 0008 tient) : un `<button aria-expanded aria-controls>` par groupe,
qui ouvre un panneau listant ses sections, l'intitulé et, dessous, le résumé.
Un seul panneau ouvert ; il se ferme sur Échap (le focus revient au bouton du
groupe ou au « ☰ Menu »), sur un clic hors du menu, quand le focus en sort, sur
le clic d'un lien (y compris celui de la page déjà ouverte) et à chaque
changement de route ; chacun replie aussi le menu des petits écrans. Les deux
derniers groupes ouvrent leur panneau vers la gauche, pour ne pas sortir de la
fenêtre. Le groupe de
l'écran affiché porte un soulignement d'accent (`aria-current="true"`), chemins
imbriqués compris (`/analyse/impact`). Sous 48 rem, les groupes se replient
derrière « ☰ Menu » et s'empilent. En mode lecture (ADR 0064) le menu n'est pas
rendu, la barre reste.

**Clair par défaut, sombre sur demande.** `style.css` définit une palette claire
(`--bg`, `--surface`, `--text`, `--muted`, `--border`, `--accent`, et les jetons
`--bar-*` de la bande, et les encres d'état `--success`, `--warning`,
`--danger`, à 4,5:1 au moins sur `--surface` dans les deux thèmes) appliquée quel que soit le réglage du système. Le sombre
— ardoise, pas noir — n'existe que sous `:root[data-theme='dark']`, et
`color-scheme` suit le thème choisi. `ThemeToggle` pose `data-theme` sur
`<html>` et retient le choix dans `localStorage` (`ea.theme`). Un petit script
en ligne dans `index.html` l'applique avant le premier rendu — le module de
`main.ts`, différé, arriverait après que la feuille de style a peint en clair ;
`main.ts` le réapplique, sans effet de plus. Aucune Content-Security-Policy ne
sert le SPA (ni `frontend/nginx.conf`, ni le vhost de l'Infra) : le jour où
il en aura une, ce script devra y être autorisé par son empreinte ou déplacé.
Tout accès au stockage est gardé : un navigateur qui le refuse reste en clair.
Le bouton garde un seul nom, « Thème sombre », et dit son état par
`aria-pressed`.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder la colonne, la rendre repliable | Aucun déplacement d'habitude | La largeur reste prise par défaut ; les résumés restent cachés | Écarté |
| Suivre `prefers-color-scheme` et ajouter un bouton | Respecte le réglage du poste | Le noir imposé était précisément la plainte ; trois états (système, clair, sombre) pour un besoin à deux | Écarté |
| Une bibliothèque de menus (Headless UI…) | Accessibilité prête | Une dépendance pour un bouton et une liste | Écarté |

## Conséquences

- Le système d'exploitation ne décide plus du thème : quelqu'un qui veut du
  sombre le choisit une fois par navigateur. Le choix ne suit pas la personne
  d'une machine à l'autre — c'est un confort, pas une préférence de compte.
- De nombreux écrans portent encore des couleurs codées en dur (fonds et encres
  de `features/`), pensées pour un fond blanc ; ils restent lisibles en clair,
  mais le thème sombre peut y laisser des contrastes faibles, à reprendre écran
  par écran vers les jetons.
- `--accent` est maintenant défini : la matrice et le canevas des diagrammes,
  qui le lisaient avec un violet de repli, passent au bleu de la palette ; de
  même `--success`, `--warning` et `--danger` remplacent les replis de
  `ApprovalBanner` et `FieldDiff`.
- Le survol n'ouvre pas les panneaux ; clic et clavier suffisent. À ajouter si
  l'usage le demande.

## Références

- ADR liés : [0008](0008-menu-et-routage-du-spa.md), [0064](0064-publication-en-lecture.md)
- Code concerné : `frontend/src/components/AppNav.vue`, `frontend/src/components/ThemeToggle.vue`, `frontend/src/lib/theme.ts`, `frontend/src/style.css`
