# 7. Client OpenAPI généré, et premier écran de CRUD

Date : 2026-09-07
Statut : Accepté

## Contexte

`CLAUDE.md` impose depuis le début que le schéma OpenAPI du backend soit la
source de vérité du contrat front/back, et interdit d'écrire à la main une
interface TypeScript qui reflète un modèle Pydantic. `docs/adr/0002` ajoute que
`frontend/src/api/` est réservé au client généré et que `src/lib/health.ts` —
un wrapper `fetch` écrit à la main — est provisoire.

Rien n'implémentait cette règle : `npm run generate:api` n'existait pas. Le
premier écran qui consomme réellement l'API (le CRUD des éléments
d'architecture) obligeait à trancher, parce qu'il touche `ElementRead`,
`ElementCreate`, `ElementUpdate`, `ElementPage`, `MetamodelRead` et l'enveloppe
d'erreur — six modèles qu'il aurait fallu retaper.

## Décision

**`openapi-typescript` génère les types, `openapi-fetch` les appelle.**

* `backend/src/ea/openapi.py` produit le document OpenAPI. Il se construit sans
  base de données — rien n'entre dans le *lifespan* de l'application — et ne lit
  pas l'environnement pour les deux réglages qui atteignent le document, afin
  que deux machines génèrent le même fichier.
* `backend/openapi.json` et `frontend/src/api/schema.d.ts` sont **versionnés**.
  `make openapi` les régénère, `make openapi-check` échoue s'ils ne sont plus à
  jour, et `make check` l'inclut. C'est le « CI must fail if the committed
  client differs from a fresh generation » de `CLAUDE.md`, rendu exécutable.
* Le générateur tourne avec `--default-non-nullable false` : un champ qui a une
  valeur par défaut dans le schéma est un champ que le client peut omettre.
  Sans ce drapeau, `description` deviendrait obligatoire dans une création que
  l'API accepte sans elle.
* `frontend/src/lib/api.ts` est la moitié écrite à la main — l'URL de base et la
  traduction d'un échec en message — que le générateur ne connaît pas. Rien
  d'autre n'y décrit une forme de donnée.
* `src/lib/health.ts` passe par le client généré, comme l'annonçait `0002`.

**Le premier écran couvre les éléments, pas les relations.** Le backend sait
déjà créer des liens ; l'interface qui les dessine viendra avec la vue graphe,
et n'a pas sa place dans un tableau.

## Conséquences

- Un changement de schéma qui casse le SPA se voit à la compilation plutôt qu'à
  l'exécution : `vue-tsc` échoue sur le champ disparu.
- `make check` couvre désormais le frontend (`vue-tsc`, Vitest) en plus du
  backend. Il est plus lent, et c'est le prix de la garantie.
- `openapi-typescript@7` déclare encore un pair `typescript@^5` alors que le
  projet est en TypeScript 6. Un `overrides` dans `frontend/package.json` le
  résout ; il disparaîtra quand la borne amont sera élargie.
- Le client est appelé via une `fetch` résolue à chaque appel plutôt que
  capturée à la création. Capturée, un test qui remplace `globalThis.fetch`
  atteindrait quand même le vrai backend — donc, le graphe étant partagé,
  écrirait dans les données de tout le monde depuis un test unitaire. Cela
  s'est produit avant d'être corrigé.
- Ajouter un second client HTTP à côté de celui-ci demanderait un ADR qui
  remplace celui-ci.
