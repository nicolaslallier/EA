# Sous-projet 8 — Intégrations (références externes) — conception

Date : 2026-10-10. Statut : proposition. Équivalent Sparx EA : *Element Links to external items*, intégration Jira/TFS, hyperliens, Model Mail.

## 1. Intention

**Dit.** Relier un élément du catalogue à des objets d'outils externes (Jira, GitHub, ServiceNow, Confluence) : « cette application est suivie par ce ticket, décrite par cette page ».

**Hypothèses.**
- **On ne sait pas quels outils l'utilisateur emploie.** La spec définit donc un *mécanisme* générique (table, port, panneau) et un seul connecteur de référence ; les autres sont des lots ultérieurs qui n'ajoutent ni table ni écran.
- Lecture seule d'abord : EA affiche titre et état d'un objet externe, il ne le modifie pas.
- Le *Model Mail* de Sparx est hors sujet : le partage passe par les liens profonds (`?element=`) et le sous-projet 3.
- L'audit est celui du sous-projet 2 : toute création ou suppression de référence y est journalisée. L'import/export des références rejoint le sous-projet 3.

## 2. Périmètre

**Dans** : table `external_references` ; port `ExternalSystem` ; connecteur **GitHub Issues** (lot 1) ; type `url` (lien libre, jamais appelé) ; actualisation du cache par bouton ; panneau SPA ; 3 outils MCP ; section `/health`.

**Hors v1** :
- Webhook entrant (un ticket fermé met l'état à jour). Il demande un point d'entrée public, un secret HMAC, la gestion des rejeux et de l'ordre, et une exposition réseau que le déploiement (NGINX Infra, Mac) n'a pas ; le cache rafraîchi à la demande suffit pour une v1. Candidat au lot 5.
- Création d'un ticket depuis un élément (port prévu, méthode non implémentée en v1).
- Écriture vers le fournisseur (commentaires, changement d'état).
- Authentification de l'utilisateur auprès du fournisseur (OAuth par utilisateur).

## 3. Modèle de domaine

`domain/external.py`, pur, sans framework :
- `ExternalKind` : `url` | `github_issue` (lot 1) ; `jira_issue`, `confluence_page`, `servicenow_ticket` ajoutés par lots. L'ensemble connu vit dans le domaine, et `/external/kinds` l'expose : la SPA ne le recopie pas (même règle que le métamodèle).
- `ExternalReference` (valeur) : `id`, `element_id`, `kind`, `external_id`, `url`, `title`, `status`, `synced_at`, `created_by`, `created_at`.
- `ExternalSnapshot` : `title`, `status` (`open` | `closed` | `unknown`), `url`. Résultat d'une résolution.
- `parse_reference(kind, raw) -> (external_id, url)` : règles pures par type. GitHub accepte `owner/repo#123` ou l'URL d'une issue et rend l'identifiant canonique `owner/repo#123`. L'URL stockée est **reconstruite par le serveur** à partir de l'URL de base configurée et de l'identifiant, jamais prise telle quelle de l'utilisateur (seul `kind=url` stocke l'URL saisie, validée `http`/`https`, 2048 caractères max, sans identifiants intégrés).
- `ExternalRefused` / `ExternalUnavailable` : erreurs de domaine (422 / 503), ajoutées à `_STATUS` dans `api/errors.py`.

Port, dans `domain/ports.py`, comme `Embedder` :
- `ExternalSystem` : `kind: ExternalKind` ; `async resolve(external_id) -> ExternalSnapshot` ; `async probe() -> None`. Une méthode `create_from_element` est réservée au lot ultérieur, pas déclarée en v1.
- `ExternalReferenceRepository` : `add`, `list_for(element_id)`, `get`, `delete`, `update_snapshot`.

**Temps réel ou cache : cache rafraîchi à la demande.** Ouvrir un élément ne fait *aucun* appel sortant : la lecture reste rapide et indépendante du fournisseur (les quotas de GitHub et un SaaS lent ne ralentissent pas le catalogue). La résolution se fait à la création (on voit tout de suite si l'identifiant existe), puis par le bouton « Actualiser » (par référence ou pour tout l'élément). Un flow Prefect `rafraichir-references` (pipelines/, via l'API REST) est un lot ultérieur, utile seulement si l'on veut des états à jour sans clic ; rien dans le mécanisme ne l'exige.

## 4. Données

Une migration (non numérotée ici), modèle `db/models/external.py` importé par `db/models/__init__.py`, convention de nommage gelée de `db/base.py` :

```
external_references(
  id uuid pk,
  element_id uuid not null  FK elements.id ON DELETE CASCADE (index),
  kind varchar(32) not null,
  external_id varchar(300) not null,
  url varchar(2048) not null,
  title varchar(500) not null default '',   -- cache
  status varchar(16) not null default 'unknown',  -- cache : open|closed|unknown
  synced_at timestamptz null,               -- null = jamais résolu
  created_by varchar(200) not null, created_at timestamptz not null)
unique (element_id, kind, external_id)
check (kind <> '') ; check (status in ('open','closed','unknown'))
```

- Supprimer un élément emporte ses références (même mécanisme que `element_documents`, une transaction).
- Aucun secret en base, aucun jeton, aucune URL de base : tout cela est de la configuration.
- Le titre en cache est du texte venu d'un tiers : il est stocké tel quel, borné, et rendu par la SPA en texte (jamais en HTML).

## 5. API

Routeurs sous `api/`, lecture = tout appelant authentifié, écriture = `ea-editor`, décidé dans `services/external.py` (`require_caller` / `require_editor` en première instruction, vérifié par `test_service_guards.py`).

| Route | Rôle |
|---|---|
| `GET /elements/{id}/external-references` | liste depuis le cache, sans appel sortant |
| `POST /elements/{id}/external-references` | `{kind, reference}` : parse, résout, enregistre ; 409 si doublon, 422 si introuvable chez le fournisseur ; si le fournisseur est injoignable, 503 et **rien n'est créé** pour un type résolu (pas de lien non vérifié) |
| `POST /external-references/{id}/refresh` | re-résout ; injoignable : 200 avec la ligne inchangée et `stale: true` |
| `DELETE /external-references/{id}` | retire le lien (jamais l'objet externe) |
| `GET /external/kinds` | types connus et lesquels sont actifs (connecteur configuré et joignable) |

`ExternalReferenceRead` porte `stale` (calculé : `synced_at` absent ou plus ancien qu'un seuil `EXTERNAL_STALE_AFTER`, 24 h). Corps en `extra="forbid"`, bornes déclarées une fois dans `api/schemas.py` et réimportées par `mcp/server.py`. Client TypeScript régénéré (`make openapi`).

## 6. MCP

Trois outils, adaptateur frère de `api/`, un fournisseur de service de plus (`get_external`, requis comme `get_files`) :
- `list_external_references(element_id)` : lecture, depuis le cache.
- `link_external_reference(element_id, kind, reference)` : écriture, non destructive ; la docstring dit que le serveur résout l'identifiant et qu'une URL arbitraire ne sera pas appelée.
- `unlink_external_reference(reference_id)` : suppression, annotée comme telle.

Entrée dans la liste complète de `tests/unit/test_mcp_server.py` ; les `INSTRUCTIONS` mentionnent le lien vers le ticket quand un agent documente une décision. Pas de `refresh` en MCP (un agent n'a pas à consommer le quota du fournisseur).

## 7. SPA

Section existante *Catalogue*, détail d'un élément (`?element=`) : panneau « Références » à côté de *Documents*, dans `features/catalogue/` (composable `useExternalReferences` via `useLatestRequest`).
- Une ligne par référence : icône du type, titre en cache (lien `target="_blank" rel="noopener noreferrer"` vers l'URL stockée), pastille d'état (ouvert/fermé/inconnu), « mis à jour il y a… », bouton Actualiser.
- Référence périmée ou fournisseur dégradé : bandeau « Fournisseur injoignable — titre et état affichés viennent du cache » ; le reste de l'écran reste utilisable.
- Ajout : liste des types actifs (issue de `/external/kinds`) et un champ ; un lecteur ne voit ni l'ajout ni la suppression (le serveur décide, l'UI masque).
- Pas de nouvelle section du menu ; aucune règle de format recopiée en TypeScript (la validation est celle du serveur).

## 8. Sécurité

- **Secrets** : un jeton par fournisseur dans `Settings` (`SecretStr`, ex. `EA_EXTERNAL_GITHUB_TOKEN`), `.env.example` laissé vide, masqué par `RedactingFilter`, jamais en base, jamais renvoyé par l'API, jamais journalisé.
- **Identité sortante** : un *compte de service* (jeton à portée minimale : lecture des issues du dépôt, p. ex. fine-grained PAT ou GitHub App en lecture seule), jamais le jeton de l'utilisateur, que EA ne possède pas. Conséquence pour l'autorisation : un lecteur EA voit le titre et l'état d'un ticket que son compte GitHub n'aurait peut-être pas le droit de voir. La parade n'est pas un contrôle par utilisateur (impossible sans OAuth délégué) mais de limiter ce que le compte de service peut lire (liste blanche de dépôts `EA_EXTERNAL_GITHUB_REPOS`, refus 422 hors liste) et de ne mettre en cache que titre et état, jamais le corps. Documenté dans l'ADR.
- **SSRF** : le serveur n'appelle que l'URL de base configurée (`EA_EXTERNAL_GITHUB_API_URL`, par défaut `https://api.github.com`) avec un chemin construit depuis un identifiant validé par expression régulière stricte (`owner/repo#n`). Une URL fournie par un utilisateur n'est jamais requêtée ; `kind=url` n'est jamais résolu, il reste un lien affiché. Pas de redirections suivies (`follow_redirects=False`). Un test vérifie qu'aucune requête ne part vers un hôte autre que la base configurée.
- **Écriture** : seul `ea-editor` lie ou délie ; l'audit (sous-projet 2) journalise `external_reference.created|deleted` avec l'appelant. Les actualisations ne sont pas auditées.
- **Contenu tiers** : titre rendu en texte ; longueurs bornées à la lecture de la réponse ; timeout court (`EA_EXTERNAL_TIMEOUT_SECONDS`, 10 s) ; un seul client HTTP, `httpx`, sans nouvelle dépendance.
- **Quota** : `refresh` d'un élément limité à 20 références par appel ; en cas de 403/429 du fournisseur, la ligne reste inchangée et le journal note `action=external_rate_limited`.

## 9. Tests

TDD ; aucun réseau (garde `socket`), doubles `httpx.MockTransport` comme `test_ea.py` côté pipelines.
- **Unitaires** : `parse_reference` (formes valides/invalides, canonicalisation, URL reconstruite, rejet d'URL hors base) ; calcul de `stale` ; horloge injectée.
- **Connecteur GitHub** (`MockTransport`) : 200 ouverte/fermée, 404 → `ExternalRefused`, 401/403/429/5xx/timeout → `ExternalUnavailable` ; l'en-tête `Authorization` est présent, le jeton absent des journaux et des messages d'erreur ; requête uniquement vers l'hôte configuré ; pas de redirection suivie.
- **Service** : gardes `require_caller`/`require_editor` ; doublon 409 ; création refusée si injoignable ; `refresh` injoignable renvoie la ligne en cache ; suppression d'un élément emporte les références (intégration).
- **Intégration** (PostgreSQL jetable) : migration montée/descendue, contrainte unique, cascade, check `status`.
- **API** : 401 sans jeton, 403 pour un lecteur qui écrit, enveloppes d'erreurs typées, `GET /external/kinds`.
- **Démarrage** : `_reachable(EXTERNAL, probe, degraded)` avec une sonde qui échoue : l'application démarre, `/health` renvoie 200, `status: degraded` et nomme `external` sans raison.
- **MCP** : liste complète des outils, `TestTheSameBoundsAsTheHttpAdapter`.
- **SPA** (Vitest + Testing Library) : panneau, état « périmé », masquage pour un lecteur, titre rendu en texte.

## 10. ADR à écrire (non numérotés)

1. *Références externes* : table, port `ExternalSystem`, cache rafraîchi à la demande plutôt que temps réel, URL reconstruite par le serveur, identité de service et ses limites d'autorisation, GitHub Issues comme connecteur de référence, pas de webhook en v1.
2. Le cas échéant, extension de `/health` : constante `EXTERNAL` à côté de `SEARCH` et `FILES` (peut être traitée dans l'ADR 1, à la suite de l'ADR 0037).

Aucune dépendance nouvelle, donc pas d'ADR de dépendance.

## 11. Risques et questions ouvertes (défaut retenu)

| Question | Défaut retenu |
|---|---|
| Quel outil en premier ? | **GitHub Issues** : API REST simple et stable, jeton à portée fine, le dépôt est déjà sur GitHub, testable sans instance à installer. Jira est le plus demandé en entreprise, mais exige instance, version Cloud/Data Center, schémas d'authentification (Basic + jeton / OAuth) et champs d'état personnalisables : il passe en lot 2. |
| Un fournisseur par type ou plusieurs instances du même type ? | Une instance par type en v1 (une base d'URL par fournisseur). |
| Dépôts autorisés | Liste blanche obligatoire pour GitHub ; vide = connecteur inactif. |
| Fraîcheur de l'état | 24 h puis marqué « périmé » ; pas de rafraîchissement automatique en v1. |
| Référence vers un objet supprimé côté fournisseur | Le `refresh` reçoit 404 : la ligne passe à `unknown`, titre conservé, jamais supprimée automatiquement. |
| Fuite d'information via le compte de service | Liste blanche + cache limité à titre/état ; risque résiduel accepté et écrit dans l'ADR. |
| Plusieurs éléments sur un même ticket | Permis (la contrainte est par élément) ; la recherche inverse « quels éléments citent ce ticket ? » est un lot ultérieur. |
| Le dépôt est public, l'API sans jeton marche | Jeton tout de même exigé pour éviter la limite de 60 requêtes/heure ; sans jeton, connecteur inactif. |

## 12. Dépendances et taille

Dépend de : sous-projet 2 (audit, pour journaliser) ; sous-projet 3 (import/export des références) en aval. Aucun prérequis d'infrastructure hors un jeton GitHub. Taille : lot 1 moyen (une table, un service, un connecteur, 5 routes, 3 outils MCP, un panneau).

**Lots livrables**
1. **Mécanisme + GitHub Issues** : tout ce qui précède, plus `kind=url`.
2. **Jira** (`jira_issue`) : Cloud d'abord ; jeton d'API et courriel du compte de service ; correspondance de la catégorie de statut Jira vers `open|closed`. Clé `PROJ-123`, liste blanche de projets.
3. **Confluence** (`confluence_page`) : identifiant de page ou URL d'espace, titre et dernière modification ; même compte Atlassian que Jira, donc nouvelle classe mais configuration partagée.
4. **ServiceNow** (`servicenow_ticket`) : table et numéro (`INC0012345`), compte de service en Basic ou OAuth client credentials, état traduit depuis le champ `state`.
5. **Automatisation** : flow Prefect `rafraichir-references` écrivant via l'API REST ; création d'un ticket depuis un élément (méthode du port) ; éventuel webhook entrant GitHub si l'exposition réseau le permet ; recherche inverse « éléments citant ce ticket ».
