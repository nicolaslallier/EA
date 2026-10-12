---
titre: Un seul dessin statique de diagramme, côté serveur, gardé par un test de parité
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/diagram_render.py, backend/src/ea/api/diagrams.py, frontend/src/lib/diagramGeometry.ts, frontend/src/features/diagrams/DiagramCanvas.vue, frontend/tests/diagramParity.spec.ts, frontend/tests/fixtures/diagram-parity.json
---

# 63. Un seul dessin statique de diagramme, côté serveur, gardé par un test de parité

Date : 2026-10-11
Statut : Proposition

## Contexte

Le rapport (ADR 0062) et le mode lecteur (ADR 0064) ont besoin d'un dessin de diagramme qui ne soit pas l'éditeur interactif : fixe, imprimable, sans bibliothèque de graphes (aucune n'est admise, ADR 0010). L'éditeur (`DiagramCanvas.vue`) a déjà sa géométrie en TypeScript (boîtes, tronquage des liens aux bords, nom raccourci, taille de la toile). Deux dessins qui divergent d'un pixel sont deux vérités.

## Décision

1. **`domain/diagram_render.py` est le dessin statique, unique** : `render_svg(DiagramDetail) -> str`, une fonction pure et déterministe (mêmes entrées, même chaîne ; pas d'horloge, pas d'aléa, pas d'élément `<style>` — des attributs de présentation seulement, de sorte qu'une politique de contenu stricte suffit). Le rapport l'incorpore, `GET /diagrams/{id}/render.svg` le sert, le mode lecteur l'affiche. `DiagramDetail` est déjà le type neutre : le sous-projet des diagrammes v2 l'étendra, pas un doublon.
2. **L'éditeur garde sa géométrie TypeScript** pour la manipulation (glisser, redimensionner, relier) — la réécrire côté serveur n'aurait aucun sens. Ce qui est partagé est exporté, plus copié : `CANVAS_MARGIN`, `CHARACTER_WIDTH`, `shorten` sont dans `lib/diagramGeometry.ts` et le canevas les importe.
3. **Un test de parité tient « un seul dessin »** : une fixture JSON unique (`frontend/tests/fixtures/diagram-parity.json`) porte un jeu de boîtes, d'éléments et de liens (un nom trop long, un lien horizontal, un lien vertical, deux boîtes qui se chevauchent) et le SVG que le serveur en tire. Le test Python vérifie que le serveur produit exactement ce SVG (régénérable : `UPDATE_PARITY_FIXTURE=1`) ; le test Vitest relit ce SVG et vérifie que chaque rectangle, chaque trait, la taille de la toile et le nom raccourci sont ceux que `diagramGeometry.ts` calcule, et que la palette des couches est celle que le canevas peint. Modifier un côté sans l'autre casse l'un des deux tests.
4. **Tout texte saisi est échappé en XML** (`html.escape` après retrait des caractères que XML 1.0 interdit) ; un nombre non fini (une ligne peut en contenir) est lu 0, jamais `inf` ; un lien dont une extrémité n'est pas dessinée, ou dont les boîtes se chevauchent, n'a pas de trait — comme dans l'éditeur.
5. **Servi fermé** : `Content-Type: image/svg+xml`, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`, `X-Content-Type-Options: nosniff`, `ETag` = hachage du contenu rendu, `Cache-Control: private, no-cache` (pas de cache serveur partagé : un cache croiserait les droits).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Chromium sans tête | Réutilise le rendu du SPA | ~400 Mo, bac à sable, lent, non déterministe | Écarté |
| Rendre le SPA côté serveur | Un seul code | Exige Node dans l'image | Écarté |
| Palette lue de `/metamodel` par le SPA | Source unique stricte | Cinq composants lisent `LAYER_COLOURS` à l'affichage ; la rendre asynchrone est une refonte sans bénéfice | Écarté : la palette est une constante TypeScript, comparée à celle du domaine par la fixture de parité |
| Éditeur et rapport tous deux réécrits sur un seul module | Zéro duplication | L'interaction n'est pas du dessin statique | Écarté |

## Conséquences

- Un emoji (hors plan multilingue de base) dans un nom ferait diverger la règle de `shorten` (unités UTF-16 en JavaScript, points de code en Python) : la fixture de parité n'en contient pas, et la limite est connue.
- La palette est dite à deux endroits, vérifiés égaux par un test des deux côtés ; `/metamodel` n'est pas modifié.
- Le dessin ne montre pas les éléments hors de période d'un `as_of` (le diagramme est une vue : la boîte reste, ADR 0060) ; le dessiner grisé est une suite possible.

## Références

- ADR liés : [0010](0010-dessiner-le-voisinage-d-un-element.md), [0031](0031-diagrammes-enregistres.md), [0062](0062-generation-de-documents.md), [0064](0064-publication-en-lecture.md)
- Code concerné : `backend/src/ea/domain/diagram_render.py`, `frontend/src/lib/diagramGeometry.ts`
