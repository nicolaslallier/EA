---
titre: Références externes — des cibles nommées, une seule porte de sortie, des écritures chez un tiers fermées par défaut
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/external.py, backend/src/ea/domain/egress.py, backend/src/ea/repositories/external_http.py, backend/src/ea/repositories/external_providers.py, backend/src/ea/repositories/external_store.py, backend/src/ea/services/external.py, backend/src/ea/api/external.py, backend/src/ea/mcp/server.py, backend/src/ea/core/config.py, backend/migrations/versions/0015_external_references.py, frontend/src/features/external, pipelines/src/pipelines/references.py
---

# 62. Références externes — des cibles nommées, une seule porte de sortie, des écritures chez un tiers fermées par défaut

Date : 2026-10-11
Statut : Proposition

## Contexte

Un élément du catalogue doit pouvoir citer l'objet d'un autre outil (un ticket GitHub, Jira ou ServiceNow, une
page Confluence), avec son titre et son état. C'est la première fois que le serveur émet des requêtes vers des
systèmes tiers avec des jetons : toute adresse qu'un appelant pourrait influencer est une SSRF, tout texte reçu est
une injection possible (pour l'interface comme pour un agent MCP), tout jeton une fuite possible. La spec
(`docs/superpowers/specs/2026-10-10-08-integrations-design.md`) ne connaissait qu'un connecteur GitHub à
configuration plate ; l'utilisateur a validé que les systèmes joignables sont des **cibles nommées déclarées en
configuration**.

## Décision

**Cibles nommées.** `EA_EXTERNAL_TARGETS` (JSON : nom → `kind`, `api_url`, `web_url`, `allow`, `user`, `writable`,
`ports`, `allow_networks`) déclare ce qui est joignable ; `EA_EXTERNAL_TOKENS` (JSON : nom → jeton) est le **seul**
endroit où un secret s'écrit (`SecretStr`, `hide_input_in_errors`, jamais en base, jamais renvoyé, masqué par
`RedactingFilter`). Un appelant **nomme** une cible ; il ne fournit jamais d'adresse. Une cible sans jeton, sans
périmètre (`allow` : dépôts, projets, préfixes de table, espaces) ou sans compte (Jira, ServiceNow) est déclarée
mais inactive ; une cible dont la sonde de démarrage échoue est écartée et `external` entre dans `degraded` de
`/health` (ADR 0037) — l'API démarre.

**Une seule porte de sortie** (`repositories/external_http.py`), `httpx` : https uniquement, hôte fixé à la
construction ; chemin simple (pas de requête, pas de `//`), paramètres encodés ; **le nom est résolu une fois et
chaque adresse vérifiée** (`domain/egress.py` : loopback, link-local et métadonnées cloud jamais ; plages privées
seulement si la configuration déclare leur réseau ; formes IPv6 embarquant de l'IPv4 écartées) ; la connexion va à
l'**adresse validée** avec le nom d'origine en `Host` et en SNI (pas de DNS rebinding, certificat vérifié contre le
nom) ; TLS toujours vérifié (CA configurable), `trust_env=False` ; **aucune redirection suivie** ; réponse lue en
flux, **brute** (`Accept-Encoding: identity`) sous un plafond, rejetée dès que la longueur annoncée ou lue le
dépasse ; délais par phase et total ; **aucune retentative**.

**Seul un titre et un état sont gardés** (cache rafraîchi à la demande, jamais à la lecture ; périmé après 24 h).
L'adresse affichée est **construite** par le serveur depuis l'identifiant validé ; une adresse collée n'est que
*lue* pour en extraire l'identifiant, et refusée si elle n'est pas sur le site configuré. Le titre est du texte
tiers : nettoyé, borné, rendu en texte par la SPA (jamais `v-html`, test de garde) et rendu à un agent MCP dans un
champ `untrusted_title` d'un **registre** vérifié par test (`UNTRUSTED_FIELDS` / `TRUSTED_TEXT_FIELDS`), avec une
mention « données, pas instructions » et un nombre borné.

**Écrire chez un tiers est fermé par défaut.** Une cible est en lecture seule sauf `writable` strictement `true`
(toute autre valeur est lue comme `false`) ; seule la lecture GitHub peut l'être aujourd'hui (`WRITABLE_KINDS`).
`ensure_writable`, **la** garde du service, porte sur la cible résolue côté serveur, avant tout appel, pour un
éditeur et pour un `dry_run` (test AST : aucun service n'appelle `create_issue` sans elle ; ni `api/` ni `mcp/` ne
le touchent). `open_issue` est un `dry_run` par défaut, en HTTP et en MCP (booléen strict) ; il n'envoie **que**
titre et texte fournis par l'appelant, bornés et listés dans la réponse du `dry_run` — aucun contenu du catalogue
n'est joint — une seule fois, sans retentative (502 `external_write_failed`, 504 `external_write_timeout` : le
ticket peut exister, à vérifier chez le fournisseur). Le lien local est écrit après le ticket ; s'il échoue, le
message le dit.

**Autorisation.** Lecture : tout appelant authentifié ; lier, délier, actualiser, ouvrir un ticket : `ea-editor`
(`services/external.py`). Un plafond d'admission global et par appelant (`Admission`) borne les appels sortants :
429 `external_busy` plutôt qu'une file. Les liens ne sont **pas** soumis à `guard_element_write` ni ne font
avancer la `revision` : une référence est un pointeur à côté de l'élément, hors du contenu qu'un relecteur a
approuvé.

**Journal.** `external_reference.linked|unlinked|refreshed` dans `audit_events` (dans la transaction de
l'écriture) ; un `refresh` qui ne change rien n'écrit rien ; le titre n'y figure que comme longueur et empreinte.

**Persistance.** Table `external_references` (migration `0015`, table neuve : aucune donnée existante lue ni
convertie), clé étrangère `ON DELETE CASCADE`, unicité `(element_id, target, external_id)`.

**Prefect.** Le flow `rafraichir-references` (`pipelines/`) liste les références périmées et demande à l'API de les
rafraîchir — REST seulement (ADR 0028) ; il ne détient aucun jeton de fournisseur et n'ouvre jamais de ticket.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Une URL fournie par l'appelant | Souple | SSRF par construction | Écarté |
| Réglages plats par fournisseur (`EA_EXTERNAL_GITHUB_*`) | Simple | Une instance par type ; pas de multi-cibles | Écarté |
| Suivre les redirections après revalidation | Tolère un dépôt renommé | Surface d'attaque ; un 301 se règle en config | Écarté |
| Webhook entrant (HMAC) | État sans clic | Point d'entrée public sans Keycloak, hors périmètre réseau du déploiement | Reporté |
| Introspection de schémas de bases de données (A4) | — | Exige un pilote de base : nouvelle dépendance, spike du sous-projet 9 | Hors de ce lot ; les cibles nommées en sont le mécanisme |

## Conséquences

- Les jetons sont des comptes de service : un lecteur d'EA voit titre et état d'un ticket que son propre compte
  n'aurait peut-être pas le droit de voir ; la parade est le périmètre (`allow`) et un cache limité à titre/état.
- Un changement de configuration demande `make app-up` ; rien ne re-sonde après une panne (`make app-restart`).
- Confluence : la liste blanche porte sur l'identifiant d'espace renvoyé par l'API, vérifié sur la réponse.
- Le panneau SPA et les outils MCP n'ont aucune règle de format : le serveur décide.
- Jamais exécuté contre un vrai service tiers : les tests utilisent `httpx.MockTransport`.

## Références

- ADR liés : [0028](0028-pipelines-python-dans-le-depot-ea.md), [0032](0032-authentification-par-keycloak.md), [0037](0037-un-magasin-peripherique-degrade-ne-bloque-pas-le-demarrage.md), [0050](0050-echanges-formats-collision-et-rejet-par-ligne.md)
- Code concerné : voir `affects`.
