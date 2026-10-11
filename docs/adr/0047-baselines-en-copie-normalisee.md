---
titre: Baselines en copie normalisée du graphe, restauration en une transaction SERIALIZABLE
date: 2026-10-11
statut: Proposition (partiellement remplacée par 0049)
affects: backend/src/ea/domain/baselines.py, backend/src/ea/services/baselines.py, backend/src/ea/repositories/baseline_store.py, backend/src/ea/db/models/baseline.py, backend/src/ea/api/baselines.py, backend/src/ea/api/schemas.py, backend/src/ea/mcp/server.py, backend/migrations/versions/0012_baselines.py, frontend/src/features/baselines
---

# 47. Baselines en copie normalisée du graphe, restauration en une transaction SERIALIZABLE

Date : 2026-10-11
Statut : Proposition (partiellement remplacée par 0049, sur `protected_ids`)

## Contexte

Il faut pouvoir figer le modèle avant un changement risqué, comparer deux états
et revenir en arrière. Le journal d'audit (`docs/adr/0046`) répond à « qui a
changé quoi » mais pas à « rends-moi le modèle tel qu'il était » : il n'a pas
d'historique avant sa propre création, et une purge d'opérateur le tronquerait.
Volume visé : des centaines à quelques milliers d'éléments. Le déploiement n'a
qu'une base PostgreSQL et un rôle propriétaire `ea` (`docs/adr/0029`, `0033`).

## Décision

- **Une baseline est une copie normalisée** : `baselines`, `baseline_elements`,
  `baseline_relationships`, `baseline_boxes` (migration `0012`), qui répètent les
  colonnes de `elements`, `relationships` et `diagram_nodes` sous un
  `baseline_id`. Aucune clé étrangère vers le graphe vivant : la copie doit
  survivre à ce qu'elle a copié.
- **La capture** est trois `INSERT … SELECT` dans une transaction
  `REPEATABLE READ` : les données ne passent pas par Python, toutes les
  instructions voient le même instant du modèle (un lien n'est jamais copié sans
  son élément), et une baseline est complète ou absente. Les bornes sont
  déclarées une fois dans `domain/baselines.py` (20 000 éléments, 100 000
  relations, 100 000 boîtes, 5 000 diagrammes, 64 Mo de texte) et vérifiées par
  comptage *avant* de charger une ligne : `422 baseline_too_large`.
- **Le diff** est la comparaison de deux ensembles de valeurs
  (`diff_snapshots`), insensible aux horodatages, trié par libellé.
  `GET /baselines/{id}/diff?against=<id|current>`.
- **La restauration** est décidée par une fonction pure (`plan_restore`) et
  appliquée par `apply_restore` en **une transaction `SERIALIZABLE`** : le plan
  est recalculé dans la transaction et comparé à celui que l'appelant a
  confirmé ; différent, c'est `409 restore_conflict` et rien n'est écrit. Les
  éléments sont recréés sous leur **id d'origine**, les éléments modifiés remis
  à la valeur de la baseline, ceux créés depuis supprimés (`keep_new=false` par
  défaut, une baseline étant globale), avec leurs liens, documents et boîtes
  par cascade. Une relation devenue illégale est un avertissement, pas un
  échec. Un échange de noms entre deux éléments passe par une passe
  intermédiaire. Une collision d'unicité (nom, adresse IP) annule tout.
- **Deux gestes, et le plan lu.** `dry_run` est vrai **par défaut**, à l'API
  comme à l'outil MCP : un appel sans le paramètre n'écrit rien. Pour appliquer,
  l'appelant passe `dry_run=false` **et** le `digest` du plan qu'il a lu
  (`expected_digest`, un SHA-256 du contenu du plan, horodatages exclus). Le
  service recalcule le plan et refuse (`restore_conflict`) tout autre que
  celui-là : on n'applique jamais autre chose que ce qui a été montré. La
  réponse d'un plan est bornée : comptes exacts, et seulement les vingt
  premiers éléments de chaque sorte (`PLAN_LISTED_ITEMS`).
- **Autorisation.** Lire (liste, détail, diff) demande un appelant ; capturer,
  supprimer et restaurer — `dry_run` compris — demandent `ea-editor`. Décidé
  dans `services/` (`require_caller` / `require_editor`), pas dans la route.
- **Audit** : `baseline.created`, `baseline.deleted`, puis pour une
  restauration un `baseline.restored` (résumé, avec les boîtes déplacées) et un
  événement par entité touchée, même `request_id`, `correlation =
  'restore:<id>'`. Une restauration **refusée** n'écrit pas d'événement (la
  transaction est annulée) : elle laisse un log structuré borné
  (`action=restore_blocked|restore_conflict`).
- **MCP** : quatre outils (`list_baselines`, `diff_baselines` en lecture seule ;
  `create_baseline` en écriture non destructive ; `restore_baseline` annoté
  **destructif**), de 46 à 50 outils. Les `INSTRUCTIONS` disent au modèle de
  commencer *toujours* par un `dry_run`, de montrer le plan à la personne, et de
  n'appliquer qu'avec le `digest` lu. Pas d'outil de suppression de baseline :
  l'agent n'en a pas besoin et la suppression passe par l'écran.
- **SPA** : section « Baselines » (`?a=` la baseline, `?b=` la comparée, absent =
  le modèle courant) et une boîte de dialogue de restauration qui affiche les
  comptes exacts, dit que les documents ne sont pas capturés, liste les
  avertissements, exige la saisie du nom, relit le plan sur `restore_conflict`
  et n'existe pas pour un lecteur.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| JSONB unique par baseline | Écriture simple | Diff et recherche dans du JSON, ligne qui grossit sans borne | Écartée |
| Reconstruction depuis l'audit | Aucun stockage dupliqué | Exacte seulement depuis le début du journal, cassée par une purge | Écartée |
| Capturer aussi les documents | Restauration complète | Textes lourds dupliqués à chaque baseline | Reportée : le plan annonce les documents perdus |
| Un drapeau `confirm` booléen pour appliquer | Simple | Ne dit pas *quel* plan a été lu : un modèle qui a bougé entre le plan et le clic serait appliqué | Écartée au profit du `digest` |
| Verrouiller les tables pendant la restauration | Exact par construction | Bloque toutes les écritures de l'API | Écartée |

## Conséquences

Limites connues, dites sans détour :

- **`SERIALIZABLE` ne protège que contre les autres transactions
  `SERIALIZABLE`.** Les écritures ordinaires de l'API tournent en `READ
  COMMITTED` : entre le recalcul du plan et le commit, il reste une fenêtre de
  quelques millisecondes. Un `UPDATE` concurrent de la *même ligne* donne un
  `40001` (traduit en `restore_conflict`), mais un verrou posé ou un lien ajouté
  dans cette fenêtre ne ferait pas échouer la restauration. Le `digest` couvre
  le temps qui va de la lecture du plan à la confirmation, pas cette fenêtre.
- **Ce qui est capturé et ce qui ne l'est pas.** Les **documents** ne le sont
  pas : ceux d'un élément supprimé par une restauration sont perdus (le plan en
  donne le compte), ceux d'un élément recréé ne reviennent pas. L'**arbre des
  paquets** et les **requêtes enregistrées** ne sont ni capturés ni restaurés.
  `package_id` d'un élément est stocké avec son `package_path`, sans clé
  étrangère : si le paquet a disparu, l'élément revient **à la racine**, avec un
  avertissement dans le plan. Le `package_id` des **diagrammes** n'est pas
  capturé. Les boîtes ne reviennent que sur un diagramme qui existe encore.
- **Aucun plafond sur le nombre de baselines.** Chaque capture peut peser
  jusqu'à 64 Mo ; l'espace croît avec leur nombre. Limite connue, à surveiller
  (et à purger à la main) ; un plafond ou une rétention seraient une décision
  à part.
- La réponse du diff n'est ni paginée ni bornée en octets : avec les bornes de capture (20 000 éléments, 100 000 relations, 100 000 boîtes, 64 Mo par baseline), deux baselines entièrement différentes donnent au pire une réponse de l'ordre de 100 à 130 Mo construite en mémoire côté serveur ; cas jugé irréaliste pour un catalogue d'architecture, limite connue. Le SPA n'affiche que les cent premières lignes de chaque colonne jusqu'à ce qu'on demande le reste.
- L'écran montre les 20 premiers éléments par sorte et des comptes pour les liens (jamais nommés), mais le digest et la confirmation portent sur le plan COMPLET : la personne confirme le plan entier dont elle ne voit qu'un extrait ; le journal permet de reconstituer l'effet.
- **La porte `protected_ids` est vide jusqu'à la tâche d'approbation** (lot
  suivant, `docs/adr` à venir) : aujourd'hui aucun élément n'est protégé, donc
  `restore_blocked` ne se produit pas en pratique. L'écran affiche
  `plan.blocked` si l'API le renvoie, sans rien supposer sur les verrous.
- Une restauration refusée n'est pas un événement d'audit mais une ligne de log.
- Le plan est exact à l'instant du calcul ; la restauration le recalcule dans sa
  transaction, mais la vérification ne vaut que pour cet instant.
- Le SPA n'a pas été vérifié à l'écran : Keycloak était injoignable dans
  l'environnement où il a été écrit. Il est couvert par Vitest et par les tests
  d'API, pas par un navigateur.

## Références

- ADR liés : [0031](0031-diagrammes-enregistres.md), [0033](0033-postgresql-seul-pour-le-graphe.md), [0042](0042-les-paquets-classement-en-liste-d-adjacence.md), [0046](0046-journal-d-audit-append-only.md)
- Code : `backend/src/ea/domain/baselines.py`, `backend/src/ea/repositories/baseline_store.py`, `backend/src/ea/services/baselines.py`, `backend/src/ea/api/baselines.py`, `frontend/src/features/baselines/`
