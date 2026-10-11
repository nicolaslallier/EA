# Publication — rapports (D2) et publication web (D3) — design

Date : 2026-10-10. Statut : proposition. Sous-projet 7 sur 9.

## 1. Intention

**Dit** : générer des documents (PDF, DOCX, HTML) d'un paquet, d'un diagramme ou
d'une requête enregistrée, à la manière du Document Generator de Sparx EA
(modèles de rapport) ; et publier le modèle en lecture seule, équivalent de WebEA,
pour des lecteurs qui n'ont pas besoin de l'application complète.

**Hypothèses** (défauts retenus, voir §11) :
- Les lecteurs ont un compte du realm `ea` : l'application n'a aujourd'hui aucune
  route anonyme et `require_caller` est partout ; on n'en ouvre pas une pour la
  publication.
- Sous-projet 1 fournit `packages` (arbre) et `saved_queries` (filtre structuré
  exécutable par le service) ; sous-projet 6 fournit les diagrammes v2 (formes,
  connecteurs, notes). Ce sous-projet les consomme, il ne les redéfinit pas.
- Un rapport est un *artefact daté* (photographie), la publication web est la
  *vue vivante*. Les deux ne se remplacent pas.

## 2. Périmètre

**Dans** : modèle de rapport déclaratif ; deux modèles intégrés ; rendu HTML
autonome, PDF, DOCX ; dépôt du résultat dans le bucket sous `reports/` avec sa
fiche `file_metadata` ; rendu SVG d'un diagramme côté serveur ; mode « lecteur »
du SPA (`/view/…`) ; outils MCP `list_report_templates`, `generate_report`.

**Hors** : partage par lien à jeton / URL anonyme (§11, ADR requis s'il vient) ;
site statique exporté ; édition de modèles par l'utilisateur (lot ultérieur,
§12) ; planification récurrente de rapports ; signature ou filigrane ; export de
requête en CSV (relève du sous-projet 1) ; mise en page « pixel-perfect » à la
Sparx (en-têtes/pieds par modèle, feuilles de style personnalisées).

## 3. Modèle de domaine

Module pur `domain/reports.py` (aucun import de framework, comme `domain/files.py`).

- `ReportTemplate` : `id` (slug), `version` (int), `title`, `subjects` (ensemble
  parmi `package`, `diagram`, `query`), `sections` (tuple ordonné).
- Sections, union discriminée par `kind`, toutes immuables :
  `text` (markdown de l'auteur du modèle, avec `{{subject.name}}`), `element_table`
  (colonnes parmi nom/type/couche/description/propriétés choisies ; portée : le
  sujet, ou une requête enregistrée nommée), `diagram` (le diagramme du sujet,
  ou tous ceux d'un paquet), `documents` (les markdown attachés aux éléments de
  la portée, profondeur limitée), `relations` (liens de la portée, regroupés par
  type), `matrix` (éléments × éléments de la portée, type de lien dans la case).
- `ReportSubject(kind, id)` et `ReportRequest(template_id, subject, format)` ;
  `ReportFormat = html | pdf | docx`.
- Plafonds (constantes, comme `MAX_FILE_BYTES`) : `MAX_REPORT_ELEMENTS = 500`,
  `MAX_REPORT_DIAGRAMS = 20`, `MAX_REPORT_DOCUMENT_BYTES = 2 MiB` cumulés,
  `REPORT_TIMEOUT_SECONDS = 30`. Dépassement = `ReportTooLargeError` (422 typé,
  message disant quel plafond et comment réduire la portée), jamais une troncature
  silencieuse.
- `ReportModel` : le document intermédiaire neutre (titres, paragraphes, tables,
  figures SVG) construit par le service à partir du modèle ; **les trois formats
  sont trois sérialiseurs de ce seul objet**, ce qui évite trois logiques de contenu.
- **Où vit le dessin SVG (tranché)** : un module de domaine pur
  `domain/diagram_render.py`, `render_svg(view: DiagramView) -> str`, déterministe
  (mêmes entrées, même chaîne, pas d'horloge ni d'aléa), échappement XML strict.
  Il est l'*unique* dessin statique : le rapport l'incorpore, la route
  `GET /diagrams/{id}/render.svg` le sert, le mode lecteur l'affiche. Palette des
  couches et couleurs d'encre y vivent (source unique), et `/metamodel` les
  expose en plus ; `LAYER_COLOURS` du SPA en est lu, plus restaté. L'éditeur
  interactif (`DiagramCanvas.vue`) garde sa géométrie TS pour la manipulation ;
  un test de parité (§9) compare ses positions de boîtes et celles du SVG serveur.
  Écartés : navigateur sans tête (Chromium ~400 Mo dans l'image, bac à sable,
  lent, non déterministe) ; réutiliser le rendu du SPA côté serveur (exige Node).

## 4. Données

Lot 1 : **aucune table, aucune migration**. Les modèles intégrés sont des
constantes de `domain/reports.py` ; la forme (Pydantic strict, `extra="forbid"`,
champ `version`) est exactement celle qu'une table stockerait plus tard, donc
passer aux modèles éditables ne casse rien (YAGNI : un jeu d'édition n'a pas de
demande aujourd'hui).

Rapport généré : un objet du bucket `ea-catalogue`, clé
`reports/<AAAA>/<AAAA-MM-JJ>-<slug-du-sujet>-<template>-<id8>.<ext>`, déposé par
`FileService.upload` (une seule porte, aucune URL présignée, ADR 0036). Sa fiche
`file_metadata` (ADR 0039) porte : `uploaded_by` = l'appelant, `created_at`,
`title` = « Rapport <modèle> — <sujet> », `description` = sujet (kind + id),
version du modèle et version de l'application, `tags` = `["rapport", template_id]`.
Le préfixe `reports/` est ajouté aux chemins autorisés de `domain/files.py` s'il
y a une liste ; il apparaît dans le navigateur de fichiers sans code SPA de plus.

Lot ultérieur (jeu d'édition) : `report_templates(id uuid pk, slug, version int,
title, definition jsonb, created_by, created_at, unique(slug, version))` —
append-only (une nouvelle version = une nouvelle ligne), migration alors, pas avant.

## 5. API

Routeur `api/reports.py`, bornes dans `api/schemas.py` (importées par MCP).

| Méthode | Chemin | Corps / paramètres | Réponse |
|---|---|---|---|
| GET | `/report-templates` | — | `200 list[ReportTemplateRead]` (id, version, titre, sujets acceptés) |
| GET | `/reports/render` | `template`, `subject_kind`, `subject_id`, `format` | `200` flux (`text/html`, `application/pdf`, DOCX), **sans dépôt** ; `404` sujet ; `422` plafond |
| POST | `/reports` | `ReportCreate { template, subject, format }` | `201 FileRead` (le fichier catalogué déposé) ; `403` pour un lecteur |
| GET | `/diagrams/{id}/render.svg` | — | `200 image/svg+xml` ; `404` |

`GET /reports/render` ne persiste rien : un lecteur peut obtenir son PDF sans
écrire dans le bucket. `POST /reports` écrit : éditeur seulement. Les deux passent
par `ReportService.build(request) -> RenderedReport(bytes, content_type, filename)`.
`Content-Disposition` est construit avec le nom assaini (pas d'entrée brute).
Le SVG est servi avec `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`
et `X-Content-Type-Options: nosniff` (il peut contenir des noms saisis par des
personnes). NGINX : la route dure jusqu'à 30 s ; `location /api/` garde son
`proxy_read_timeout` (à porter à 60 s dans le dépôt Infra, noté dans l'ADR).
Génération **synchrone, plafonnée**, la partie CPU (WeasyPrint, python-docx) dans
`asyncio.to_thread` avec `asyncio.wait_for(REPORT_TIMEOUT_SECONDS)`.
Le worker Prefect est écarté : `pipelines/` n'est pas déployé (ADR 0028
Proposition), il passerait par l'API avec son propre jeton (donc ne saurait pas
agir « comme l'appelant »), et les plafonds rendent la durée bornée. Si un plafond
doit monter, c'est la décision de passer en asynchrone, pas avant.

Le contrat OpenAPI change : `make openapi`, client régénéré.

## 6. MCP

Deux outils dans `mcp/server.py` (`@server.tool` + `@speaking_plainly`), le total
passe de 34 à 36 ; entrée dans l'assertion de liste de `tests/unit/test_mcp_server.py`.

- `list_report_templates()` — lecture, `readOnlyHint`.
- `generate_report(template, subject_kind, subject_id, format="pdf")` — dépose
  le rapport, répond par la clé et la fiche (`FileRead`), **jamais les octets**
  (plafond MCP de 1 Mo, enveloppe base64). L'agent lit ensuite avec les outils de
  fichiers existants ou donne la clé à la personne. Annotation : écriture, non
  destructive (un rapport de plus, jamais d'écrasement : la clé contient la date
  et un `id8`). Le docstring dit les plafonds et l'erreur à attendre.
Pas d'outil pour rendre un SVG : un agent n'en a pas l'usage.

## 7. SPA

**Rapports** : pas de nouvelle section. Un bouton « Générer un rapport… » dans
trois endroits — fiche d'un paquet, en-tête d'un diagramme, liste de requêtes
enregistrées — ouvre une petite boîte : modèle (liste de `/report-templates`
filtrée par sujet), format. « Télécharger » appelle `GET /reports/render`
(visible de tous) ; « Déposer dans les fichiers » (éditeur seulement, masqué
sinon — le service décide, le SPA cache) appelle `POST /reports` puis renvoie
vers `/files?prefix=reports/`. Composable `features/reports/useReport.ts` via
`useLatestRequest()` ; échecs dans l'état.

**Publication web = option A, mode lecteur du SPA (tranché)** : routes
`/view/…` dans la même application, même origine, même authentification
`oidc-client-ts`, mêmes endpoints. Aucun nouveau jeton, aucune nouvelle
surface réseau. Écartées : B (site statique) — copie périmée dès l'écriture
suivante, droits figés à la génération, et un second rendeur à tenir ; C (lien à
jeton) — voir §11.

- Entrée `sections.ts` : chemin `/view`, nom `view`, libellé « Consultation »,
  groupe lecture, vue paresseuse `features/view/ViewShell.vue`. Une mise en page
  allégée (pas de menu d'édition, pas de palette) ; `/view` est la route d'arrivée
  proposée aux comptes sans `ea-editor`.
- Pages : `/view` (arbre des paquets, sous-projet 1) ; `/view/package/:id`
  (contenu, sous-paquets, diagrammes) ; `/view/element/:id` (fiche, relations
  entrantes/sortantes cliquables, documents) ; `/view/diagram/:id` (le SVG de
  `render.svg` en `<img>` avec zoom ; chaque boîte cliquable via une carte
  d'image tirée de `DiagramRead`, ou SVG inline sanitisé — défaut : `<img>` +
  liste d'éléments sous le dessin, plus accessible).
- État dans l'URL, règle du dépôt : sujet en chemin, `?relation=` pour le filtre
  des liens. Recherche : réutilise `search_documents` côté HTTP s'il existe, sinon
  `GET /elements?search=` — rien de neuf en v1.
- Documents markdown : rendu **côté serveur** en HTML sûr (`markdown-it-py`, HTML
  brut désactivé, schémas de lien `javascript:`/`data:` refusés) par un
  `GET /documents/{id}/html` ; le SPA l'insère par `v-html` seulement parce que la
  sortie est déjà neutralisée et testée (§9). Le rapport utilise le même rendeur.
- Aucune action d'écriture dans `/view/*` : pas de bouton, pas d'appel POST/PUT/
  PATCH/DELETE (testé) — mais l'API reste l'autorité (§8).

## 8. Sécurité

- Lecture : `require_caller` ; `ReportService.build` en lecture, `deposit` en
  `require_editor` en première instruction (test d'AST existant
  `test_service_guards.py` le vérifie). La règle de portée (« ce qu'un lecteur
  voit ») est celle des services de lecture : un rapport ne contient rien qu'une
  lecture directe n'aurait montré.
- **Aucune URL publique anonyme par défaut.** `/view/*` est une route du SPA qui
  exige le login ; le contenu vient d'appels à l'API déjà protégés.
- Injection : tout texte saisi (noms, descriptions, propriétés) est échappé en
  HTML/XML ; le markdown passe par un rendeur à HTML brut désactivé ; les modèles
  intégrés sont du code, pas des gabarits évalués (pas de Jinja avec expressions
  libres : substitution de `{{subject.name}}` par une table fermée de champs).
  WeasyPrint : récupérateur d'URL personnalisé qui **refuse tout accès réseau
  ou fichier** (pas de SSRF ni de lecture locale via `<img src>`/`@import`).
- Déni de service : plafonds du §3, délai de 30 s, un seul rapport simultané par
  appelant (sémaphore par `sub`, 429 typé au-delà), pas de rendu en cache partagé
  (sinon fuite entre droits).
- Journal : une ligne `action=report_generated` (template, subject, format,
  octets, `duration_ms`, `deposited`) dans `services/`, comme toute écriture
  (ADR 0021). Si le sous-projet d'audit existe, l'événement y est aussi écrit ; il
  n'est pas une dépendance dure.
- Aucun secret, aucun jeton dans une URL ; le téléchargement passe par le fetch
  du SPA avec l'en-tête `Authorization`, converti en `Blob`.

## 9. Tests (TDD)

- **Unité** (sans E/S) : `domain/reports.py` (validation des sections, plafonds,
  substitution fermée) ; `domain/diagram_render.py` (instantané textuel fixe d'un
  diagramme, déterminisme deux appels identiques, échappement d'un nom contenant
  `<script>` et `&`, boîtes sans lien, diagramme vide) ; sérialiseur HTML d'un
  `ReportModel` ; rendeur markdown (HTML brut neutralisé, `javascript:` refusé,
  blocs de code) ; garde-fous AST des services.
- **Intégration** (PostgreSQL + MinIO jetables) : `POST /reports` dépose sous
  `reports/…`, la fiche porte appelant, date, tags ; deux appels = deux clés ; un
  rapport sur un paquet de 501 éléments → 422 sans objet écrit ; suppression d'un
  élément pendant un rendu ne produit pas un rapport incohérent (lecture en une
  transaction).
- **API** : 401 sans jeton, 200 `render` pour un lecteur, 403 `POST` pour un
  lecteur, 404 sujet inconnu, PDF commence par `%PDF`, DOCX est un zip contenant
  `word/document.xml`, `Content-Disposition` assaini, en-têtes CSP du SVG.
- **Parité** : un test Vitest charge un `DiagramRead` figé, calcule les boîtes par
  `diagramGeometry` et les compare aux `<rect>` du SVG serveur du même jeu
  (fixture JSON partagée) — c'est ce qui garde « un seul dessin ».
- **SPA** (Vitest + Testing Library) : `/view/*` n'émet aucune requête
  d'écriture ; boutons d'écriture absents pour un lecteur ; rapport : modèle
  filtré par sujet. **E2E** : un parcours lecture (arbre → élément → diagramme) et
  un téléchargement PDF, quand Playwright existera.
- Réseau : le garde `socket` reste ; le récupérateur d'URL WeasyPrint est testé
  pour refuser `http://` et `file://`.

## 10. ADR à écrire

1. **Génération de documents** : choix des dépendances (WeasyPrint pour le PDF,
   `python-docx` pour le DOCX, `markdown-it-py`), comparés à Chromium sans tête et
   à « HTML imprimable seul » ; synchrone plafonné vs Prefect ; modèle
   déclaratif intégré avant table ; changement de l'image `backend/Dockerfile`
   (paquets système Pango/Cairo ; les deux `FROM` partagent une même version de
   Python).
2. **Un seul dessin de diagramme** : `domain/diagram_render.py` source unique,
   palette exposée par `/metamodel`, test de parité.
3. **Publication en lecture** : mode lecteur du SPA ; rejet explicite du site
   statique et du lien anonyme tant qu'aucun besoin externe n'est exprimé.
Chaque ADR doit référencer 0027 (déploiement), 0032 (auth), 0036/0039 (fichiers).

## 11. Risques et questions ouvertes (défaut retenu)

- **PDF sans moteur dans la pile** : WeasyPrint ajoute des bibliothèques système
  et ~80 Mo d'image. Défaut : l'accepter ; **HTML autonome reste livrable même
  si l'image ne peut pas changer** (le serveur répond alors 501 typé `format_unavailable`
  pour `pdf`/`docx`, listé par `/report-templates`).
- **DOCX et diagrammes** : Word n'a pas de SVG fiable. Défaut : rasteriser en PNG
  par `cairosvg` pour le DOCX seulement ; sinon repli documenté sur une liste
  d'éléments et de liens sous un diagramme non inclus. Décision prise au lot 3
  selon le coût d'image.
- **Typographie** : polices absentes dans l'image → rendu différent d'une machine
  à l'autre. Défaut : embarquer une police libre (DejaVu) dans l'image, pas de
  polices système.
- **Partage externe** (« envoyer à un client ») : non fourni. Si exigé, défaut :
  partager un *rapport déposé*, jamais le modèle vivant ; jeton aléatoire 256 bits
  stocké haché, expiration ≤ 30 jours, révocable, événement d'audit à la création,
  à l'ouverture et à la révocation, route dédiée anonyme limitée à cet objet — sous
  un ADR qui amende le principe « tout appelant est authentifié » (0032).
  Contourner : exporter le PDF et le transmettre soi-même.
- **Rapports qui s'accumulent** dans `reports/` : pas de purge automatique ;
  l'éditeur supprime via les fichiers. Défaut : une ligne dans le navigateur de
  fichiers suffit.
- **Volume de lecture** : `/view/diagram` appelle un rendu à chaque ouverture.
  Défaut : `ETag` = hachage du contenu (mise à jour du diagramme + éléments),
  `Cache-Control: private, no-cache`, pas de cache serveur.
- **Dépendance au sous-projet 1** (paquets, requêtes) : sans lui, seuls les sujets
  `diagram` et la requête ad hoc sont livrables. Défaut : le lot 1 s'ouvre sur le
  sujet `diagram`, le sujet `package`/`query` s'active quand ses services existent.
- **Rejeu** : « régénérer le même rapport » est un nouveau dépôt, pas une
  mise à jour : la fiche garde sa date de génération véridique.

## 12. Dépendances et taille, lots

Dépendances : sous-projet 1 (paquets, requêtes) et 6 (diagrammes v2, pour que le
SVG dessine les formes définitives — le module prend un `DiagramView` neutre et
suivra). Nouvelles bibliothèques : WeasyPrint, python-docx (+ cairosvg si PNG),
markdown-it-py. Taille globale : **moyenne-grande** (≈ 3 à 4 semaines d'une
personne, dominée par le rendeur SVG et l'image).

**D2 avant D3**, parce que le rendeur SVG serveur et le rendeur markdown sont
nés dans D2 et que D3 ne fait que les consommer ; l'inverse obligerait D3 à
inventer un second dessin dans le navigateur. Lots livrables, chacun testé et
fusionnable seul :

1. **Lot 1 — socle et HTML** : `domain/diagram_render.py` + route `render.svg` +
   rendeur markdown + `ReportModel` + HTML autonome + 2 modèles (« Fiche de
   diagramme », « Inventaire d'un paquet ») + `GET /reports/render` + `POST /reports`.
   Aucune dépendance lourde.
2. **Lot 2 — PDF** : WeasyPrint, image Docker, plafonds et délai, récupérateur
   d'URL fermé, bouton SPA, outil MCP `generate_report` et `list_report_templates`.
3. **Lot 3 — mode lecteur** (D3) : routes `/view/*`, `GET /documents/{id}/html`,
   tests « aucune écriture ».
4. **Lot 4 — DOCX** : sérialiseur `python-docx`, diagrammes PNG ou repli.
5. **Lot 5 (si demandé)** : `report_templates` et son éditeur ; ou partage à jeton
   (§11), chacun avec son ADR et sa migration.
