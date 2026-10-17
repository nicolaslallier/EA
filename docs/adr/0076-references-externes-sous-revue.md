---
titre: Les références externes s'écrivent sous revue, et la file de rafraîchissement tourne
date: 2026-10-17
statut: Proposition
affects: backend/src/ea/domain/approval.py, backend/src/ea/repositories/external_store.py, backend/src/ea/services/external.py, backend/src/ea/db/models/external.py, backend/migrations/versions/0021_external_reference_attempts.py, backend/migrations/versions/0022_external_references_frozen.py, backend/src/ea/repositories/frozen_tables.py, backend/src/ea/domain/egress.py, backend/src/ea/mcp/server.py, backend/tests/unit/test_review_guard.py, backend/tests/unit/test_external_review.py, CLAUDE.md
---

# 76. Les références externes s'écrivent sous revue, et la file de rafraîchissement tourne

Date : 2026-10-17
Statut : Proposition

Amende [`0073`](0073-references-externes-et-cibles-nommees.md) sur deux points : l'autorisation
d'écrire une référence sur un élément approuvé, et l'ordre de la file de rafraîchissement. Le reste de
`0073` tient.

## Contexte

`0073` disait que lier, délier ou rafraîchir une référence externe n'était **pas** soumis à
`guard_element_write` : une référence serait un pointeur à côté de l'élément, hors du contenu approuvé. La
revue de la branche a montré que c'est la même position que les documents (`0048`), qui eux sont gelés sur un
élément approuvé : ce que montre la fiche d'un élément approuvé — « réalisé par le ticket JIRA-12, *fermé* » —
changeait sans que personne ne rouvre la revue. Une revue qui laisse réécrire ce qu'on lit sur la fiche n'a
rien approuvé.

La même revue a trouvé que `list_stale` classait par dernière *synchronisation* réussie : cent références
durablement en échec (cible injoignable, ticket supprimé) restaient en tête à chaque passage du flow
`rafraichir-references` et cachaient toutes les suivantes.

## Décision

**Écrire une référence est une écriture sous revue, comme un document.** `WriteKind.EXTERNAL_REFERENCE`
(`domain/approval.py`) : lier, délier, et un rafraîchissement qui change le titre ou l'état en cache passent
par `guard_element_write`, lu sur la ligne de l'élément **sous verrou** dans la transaction qui écrit
(`external_store._guard_the_element`). Refusé sur un élément verrouillé, et sur un élément approuvé tant qu'un
approbateur ne l'a pas rouvert (409 `element_locked`) ; `test_review_guard.py` échoue si une de ces écritures
contourne la garde. Elles ne font **pas** avancer la `revision` : un pointeur vers un ticket n'est pas un
contenu qu'un relecteur relit champ à champ. Un rafraîchissement qui ne change rien passe ; un lot saute les
lignes gelées, et le flow Prefect enregistre le refus d'une ligne sans échouer.

**La revue est lue avant que quoi que ce soit ne sorte.** `open_issue` lit l'élément et passe la garde
**avant** la cible `writable`, avant le `dry_run` et avant tout appel au tiers (idem `link`) ; elle est relue
sous verrou au moment du lien. Si l'élément est approuvé entre-temps, ou si le lien échoue pour quelque raison
que ce soit après la création, l'erreur est `502 external_link_failed` : elle nomme le ticket (jamais son
contenu) et dit de le lier à la main — une nouvelle tentative ouvrirait un doublon. `dry_run` est un booléen
strict en HTTP comme en MCP.

**La file de rafraîchissement tourne.** `external_references.attempted_at` (migration **`0021`**, une colonne
nullable — la `0019` est déjà appliquée à la base partagée et n'est pas réécrite) est mise à jour par toute
tentative, réussie, ratée ou refusée par la revue ; `list_stale` ne propose que les cibles actives et classe par
`coalesce(attempted_at, synced_at)`. Une ligne dont la cible ne sert plus son type (absente ou d'un autre
type) est notée tentée elle aussi, sinon elle resterait en tête de la file. Un lot `refresh_element` est borné à
20 lignes et à 60 s ; un élément cite au plus 50 objets, compté par le magasin dans la transaction qui insère,
après le verrou `FOR NO KEY UPDATE` qu'il prend sur l'élément : deux liens concurrents se suivent, le second
voit le premier (le service garde son contrôle d'avant l'appel au tiers, qui n'est qu'un raccourci).

**Une tentative n'écrit rien au journal — exception assumée à « chaque écriture laisse une ligne ».**
`note_attempt` ne change que `attempted_at`, une horloge d'ordonnancement : rien de ce qu'un lecteur voit de la
référence. L'auditer écrirait une ligne par passage du flow et par ligne en échec, pour aucun fait. Le test
`test_an_attempt_is_a_clock_for_the_queue_and_writes_no_audit_event` fige ce choix.

**Une référence est gelée avec le sous-arbre publié de son élément, comme un document** (`docs/adr/0074`).
La migration **`0022`** pose `external_references_frozen`, sur la fonction `ea_guard_of_element` de la `0020`
(le package de l'élément décide, `SQLSTATE EA001`, `409 package_frozen` par `work()`, refus audité
`package.write_refused_frozen`), et la table rejoint `repositories/frozen_tables.py`. Lier (`INSERT`) et délier
(`DELETE`) sont refusés ; un `UPDATE` seulement s'il touche à ce que la référence *est* —
`UPDATE OF element_id, kind, target, external_id, url`. Le rafraîchissement du cache (titre, état, `synced_at`,
`attempted_at`) reste permis : c'est la donnée du tiers, la geler ferait échouer pour toujours le rafraîchissement
d'un élément publié. Il n'y a pas de garde Python : `open_issue` sur un élément publié (et non approuvé) crée le
ticket puis voit le lien refusé, ce qui donne le `502 external_link_failed` qui nomme le ticket.

**Trois formes IPv6 de plus ne sont jamais joignables** (`domain/egress.py`, `NEVER`, non déclarables) :
`::ffff:0:0:0/96` (IPv4 traduite, RFC 2765), `64:ff9b:1::/48` (NAT64 à usage local, RFC 8215) et `2001::/32`
(Teredo) — chacune porte une adresse IPv4 qu'une passerelle joindrait à notre place.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder `0073` : les références hors revue | Un rafraîchissement automatique n'est jamais bloqué | La fiche d'un élément approuvé change sans revue | Écartée |
| Sous revue **et** avancer la `revision` | Toute écriture se voit dans la révision | Un rafraîchissement d'état rendrait périmée toute approbation en cours (`approval_stale`) | Écartée |
| Ajouter `attempted_at` à la `0019` | Une migration de moins | Une révision déjà appliquée ne se rejoue pas : la base partagée n'aurait jamais la colonne | Écartée |

## Conséquences

- Sur un élément approuvé, un éditeur ne peut plus citer un ticket sans qu'un approbateur ne rouvre l'élément :
  c'est voulu, et c'est la règle des documents.
- Une référence d'un élément approuvé dont l'état change chez le tiers garde l'ancien état en cache jusqu'à la
  réouverture ; la ligne est tentée (`attempted_at`) et ne bloque pas la file.
- Limites connues, inchangées depuis `0073` et dites ici : une configuration `EA_EXTERNAL_TARGETS` /
  `EA_EXTERNAL_TOKENS` mal formée arrête le démarrage plutôt que de dégrader `external` ; un lien libre
  (`kind=url`) est le texte d'un éditeur et un agent le reçoit dans `untrusted_link` ; l'admission est en
  mémoire, par processus ; un `410`/`404` met l'état à `unknown` sans supprimer la ligne ; rien n'a été vérifié
  contre une instance réelle de Jira, Confluence ou ServiceNow.

## Références

- ADR liés : [0048](0048-approbation-et-verrouillage-des-elements.md), [0073](0073-references-externes-et-cibles-nommees.md)
- Code concerné : `backend/src/ea/domain/approval.py`, `backend/src/ea/repositories/external_store.py`,
  `backend/src/ea/services/external.py`, `backend/migrations/versions/0021_external_reference_attempts.py`
