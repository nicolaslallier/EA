# 2. Frontend en Vue 3 plutôt qu'en React

Date : 2026-09-07
Statut : Accepté
Remplace : la ligne « Frontend : React + TypeScript + Vite » de `CLAUDE.md`

## Contexte

`CLAUDE.md` verrouillait initialement React + TypeScript + Vite. L'analyse
fonctionnelle de la structuration du scaffolding (« Structuration de l'Analyse »)
décrit en revanche un frontend Vue.js, et c'est cette cible qui a été confirmée
au moment de démarrer l'implémentation.

Aucune ligne de frontend n'ayant encore été écrite, le coût du changement est
nul — ce qui n'est vrai qu'aujourd'hui.

## Décision

Le frontend est **Vue 3 + TypeScript + Vite**, en `<script setup>` et
Composition API.

Le reste de la pile frontend est inchangé : Vitest et Testing Library
(`@testing-library/vue`) pour l'unitaire et le composant, Playwright pour l'E2E
le jour où il y aura un parcours à couvrir.

`CLAUDE.md` est mis à jour dans le même commit, comme ce fichier l'exige.

## Conséquences

- Le contrat front/back est inchangé : le schéma OpenAPI du backend reste la
  source de vérité, et `src/api/` restera réservé au client généré. Le générateur
  devra viser un client TypeScript agnostique du framework — la contrainte
  « ne jamais écrire à la main une interface TypeScript qui reflète un modèle
  Pydantic » s'applique identiquement.
- `src/lib/health.ts` est un wrapper `fetch` écrit à la main, assumé comme
  provisoire : il vit dans `lib/` et non dans `api/` précisément pour ne pas
  polluer le répertoire du client généré. Il migrera quand
  `npm run generate:api` existera.
- Pas de gestionnaire d'état pour l'instant. Le jour où il en faut un, ce sera
  Pinia, et cela mérite son propre ADR.
- Revenir à React demanderait de réécrire les composants ; cette décision doit
  être remplacée, pas contournée par l'ajout de React à côté de Vue.
