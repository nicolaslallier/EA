---
titre: Échanges — deux outils MCP, `export_model` et `import_model`
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/mcp/server.py, backend/src/ea/mcp/errors.py, backend/src/ea/services/exchanges.py, backend/src/ea/main.py
---

# 53. Échanges — deux outils MCP, `export_model` et `import_model`

Date : 2026-10-11
Statut : Proposition

## Contexte

L'échange du modèle (ADR 0048 à 0052) existe en HTTP. Un agent doit pouvoir lire un fichier d'échange ou le proposer à
l'import sans passer par l'écran « Échanges », avec les mêmes règles et sans qu'aucune soit récrite dans l'adaptateur
(`mcp/` est un frère de `api/`, pas un client de `api/`).

## Décision

- **Deux outils, un appel de service chacun.** `export_model` (lecture seule) appelle `ExchangeService.export_text` ;
  `import_model` (écriture, non destructive : `ADDS`) appelle `ExchangeService.import_exchange`. `build_mcp_server`
  reçoit un neuvième fournisseur, `get_exchanges`, requis comme les autres : l'outil est toujours listé et échoue avec la
  faute de câblage plutôt que de disparaître. Le serveur propose désormais cinquante-trois outils.
- **`dry_run` vaut `true` par défaut, dans la signature et dans le schéma envoyé à l'agent, et il est strict** : seul un
  booléen JSON est une réponse (la chaîne `"no"` n'en est pas une) ; une valeur illisible est une erreur de validation,
  jamais une écriture. L'outil et les `INSTRUCTIONS` disent d'abord de simuler, de lire le rapport, de le **montrer** à la
  personne, puis seulement de rappeler avec `dry_run=false`.
- **Bornes d'octets.** Le contenu arrive en texte dans des arguments JSON (corps du SDK ≤ 4 Mio, NGINX `location = /api/mcp`
  ≤ 2 Mo) : `MAX_MCP_UPLOAD_BYTES` (1 Mo) pour la somme des textes d'un import, vérifiée en caractères avant d'encoder
  puis en octets, et `MAX_MCP_EXPORT_BYTES` (1 Mo) pour la réponse d'un export, appliquée **pendant le rendu** (un modèle
  trop gros pour la réponse n'est pas construit en entier pour le savoir). Au-delà, une erreur d'outil claire renvoie
  vers l'écran Échanges. XML en texte seulement ; CSV en trois arguments texte. Les vues ne sont ni exportées ni importées
  par MCP (`with_views=false` forcé).
- **Mêmes places que HTTP.** `/mcp` ne traverse pas la porte ASGI où HTTP prend sa place (ADR 0048) ; le service la prend
  pour tout appelant qui arrive sans place, dans la **même** admission et avec la même clé, le `sub` du jeton vérifié : un
  appelant qui a un import HTTP en vol ne peut pas en lancer un second par MCP, et le bail est rendu à la fin de
  l'analyse, y compris sur exception et annulation. `require_editor` passe en premier, même en simulation : un lecteur voit
  l'outil et son appel est refusé (un lecteur ne prend aucune place). `export_model` est une lecture (`require_caller`) ;
  l'export est rendu dans un fichier avant que le message parte, sans connexion tenue.
- **Journaux.** Les arguments `content`, `elements`, `relations` et `properties` ne sont journalisés que par leur longueur ;
  une défaillance imprévue d'`import_model` est journalisée par `safe_failure` (structure seulement).
- **Erreurs.** Les refus du domaine deviennent des `ToolError` à message exploitable (`invalid_exchange_file` sans écho du
  contenu, `file_too_large`, `import_in_progress`, `too_many_refused_rows`, `unknown_scope`) ; aucune trace brute.

## Conséquences

- `INSTRUCTIONS` apprend à un modèle l'existence de l'échange, le `dry_run` d'abord, la limite d'1 Mo et `safe_cells`.
- Ce qui n'est pas garanti : comme en HTTP, les places sont en mémoire par processus.

## Références

- ADR liés : [0014](0014-serveur-mcp-pour-les-agents.md), [0048](0048-echanges-formats-collision-et-rejet-par-ligne.md), [0051](0051-echanges-csv-disposition-archi.md), [0052](0052-echanges-vues-paquets-et-perimetres.md)
- Code concerné : `backend/src/ea/mcp/server.py`, `backend/src/ea/mcp/errors.py`
