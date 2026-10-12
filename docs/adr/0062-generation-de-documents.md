---
titre: Génération de rapports (HTML, PDF, DOCX) — un seul modèle, trois sérialiseurs, bornés et hors de la boucle
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/reports.py, backend/src/ea/domain/report_html.py, backend/src/ea/domain/markdown_html.py, backend/src/ea/services/reports.py, backend/src/ea/repositories/report_renderers.py, backend/src/ea/api/reports.py, backend/src/ea/mcp/server.py, backend/Dockerfile, .github/workflows/ci.yml, backend/pyproject.toml
---

# 62. Génération de rapports (HTML, PDF, DOCX) — un seul modèle, trois sérialiseurs, bornés et hors de la boucle

Date : 2026-10-11
Statut : Proposition

## Contexte

Sparx EA a un générateur de documents ; ici on veut les mêmes livrables pour un diagramme ou un paquet : un document daté, que l'on télécharge ou que l'on garde dans les fichiers. Le dépôt n'a ni moteur de documents, ni rendu markdown côté serveur, et un rapport mélange des textes saisis par des personnes (noms, descriptions, documents) avec du balisage (HTML, SVG, XML) : c'est une surface d'injection. Générer est aussi un calcul lourd, qui ne doit ni geler la boucle d'événements ni être demandable sans limite (ADR 0050, 0057).

## Décision

1. **Un seul document intermédiaire neutre**, `ReportModel` (titres, paragraphes, tableaux, figures SVG, markdown), construit par une fonction pure du domaine (`build_model`) à partir d'un modèle de rapport et d'un périmètre lu par `ReportService`. **HTML, PDF et DOCX sont trois sérialiseurs de ce seul objet** : la logique de contenu n'existe qu'une fois. Le HTML (autonome : style et SVG en ligne, politique de contenu en `<meta>`) est dans le domaine ; le PDF (WeasyPrint) et le DOCX (`python-docx`) sont dans `repositories/report_renderers.py` parce qu'ils exigent des bibliothèques natives.
2. **Les modèles sont du code, pas des gabarits évalués.** Deux modèles intégrés (`fiche-diagramme`, `inventaire-paquet`), constantes de `domain/reports.py` : la seule substitution est `{{subject.name|description|kind}}`, depuis une table fermée, et la valeur est échappée comme du markdown. Aucune table, aucune migration : un jeu de modèles éditables serait une table `report_templates` append-only, plus tard, sans changer ces types.
3. **Tout texte saisi est échappé** à chaque sérialiseur (HTML, SVG/XML, DOCX) ; le markdown passe par `render_markdown` (`markdown-it-py`) avec HTML brut désactivé, images désactivées et un filtre de schémas de lien qui ne laisse passer que le relatif, `http`, `https` et `mailto` ; WeasyPrint reçoit un récupérateur d'URL (une classe `RefusingFetcher`, WeasyPrint lisant des attributs de son fetcher) qui **refuse tout** sans arrêter le rendu : la ressource est ignorée, le PDF est produit (réseau, fichier, `data:`) ; les noms de fichiers sont produits par le serveur (`reports/<AAAA>/<date>-<slug>-<modèle>-<id8>.<ext>`), jamais fournis par le client.
4. **Chaque plafond est posé avant la lecture qu'il borne**, et un dépassement est un 422 typé `report_too_large`, jamais une troncature : 500 éléments, 2 000 relations, 20 diagrammes, 1 000 boîtes en tout, 2 MiB de documents cumulés, 8 MiB de texte, 12 000 nœuds (blocs, cellules de tableau, blocs markdown — comptés sur le modèle construit et sur la source markdown, sans lancer le moteur), 30 s. Les nombres viennent des compteurs des paquets (`PackageCounts`), du `node_count` d'un diagramme, d'un `LIMIT` d'une ligne de plus que le plafond sur les liens (`link_limit` de `view_of`), de `byte_size` des résumés de documents.
5. **Le calcul lourd est hors de la boucle, sous une admission** : `Admission` de l'ADR 0050 (3 rapports en vol, un par appelant — la clé est le `sub` du jeton vérifié), `off_the_loop` et `Lease.hold` de l'ADR 0057 : un thread ne peut pas être tué, la place reste prise jusqu'à ce qu'il ait fini. Le refus est un 429 `report_busy`. Pas de cache de rendu partagé (une fuite entre droits). Le délai de 30 s est mesuré par une horloge monotone injectée, entre les étapes, plus `asyncio.wait_for` autour du thread.
6. **Lire n'écrit rien, déposer passe par la porte des fichiers.** `GET /reports/render` (tout appelant authentifié, la portée est celle des services de lecture : un rapport ne contient rien qu'une lecture directe n'aurait montré) ne stocke rien ; `POST /reports` et `generate_report` (MCP) sont `require_editor` en première instruction et écrivent par `FileService.upload` et rien d'autre (ADR 0036, 0039) : objet puis fiche, titre/description/étiquettes remplis, un fichier de plus à chaque appel, jamais un écrasement. Un dépôt n'est pas un événement du journal d'audit : les fichiers du bucket en sont hors (ADR 0046) ; la ligne `action=report_generated` (identifiants et compteurs, jamais un nom saisi) en tient lieu. Les rapports téléchargés sont toujours des `attachment` avec `nosniff` et une politique de contenu fermée.
7. **Un format dont le moteur ne se charge pas n'arrête rien** : HTML est toujours servi, `GET /report-templates` liste les formats réellement produits, un format absent est un 501 `format_unavailable`. La détection se fait au démarrage réel (lifespan), pas à la construction de l'application dans les tests.
8. **L'image de l'API change** : `backend/Dockerfile` installe `libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0` et `fonts-dejavu-core` dans l'étage final (les deux `FROM` restent sur la même version de Python) ; la CI installe les mêmes paquets avant `make install`.
9. **Le DOCX n'embarque pas les diagrammes.** Word n'a pas de SVG fiable ; rasteriser exigerait `cairosvg` et `libcairo2`, que Pango n'apporte pas. La figure devient une note qui renvoie au HTML ou au PDF ; les tableaux d'éléments et de relations restent la lecture du diagramme.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Chromium sans tête pour le PDF | Rendu fidèle du HTML | ~400 Mo dans l'image, bac à sable, lent, non déterministe | Écarté |
| « HTML imprimable » seul | Aucune dépendance | Pas de document à transmettre | Gardé comme repli : l'HTML est toujours livrable |
| Worker Prefect | Durée non bornée par la requête | `pipelines/` n'est pas déployé (ADR 0028, Proposition), le worker n'a pas l'identité de l'appelant | Écarté : les plafonds bornent la durée ; si l'un doit monter, c'est la décision de passer en asynchrone |
| Gabarits Jinja | Modèles éditables à l'aise | Expressions libres : une surface d'exécution dans un texte | Écarté |
| Génération synchrone sans admission | Plus simple | N rapports simultanés = N threads CPU et N jeux de lectures ; un client qui raccroche laisse un thread | Écarté |

## Conséquences

- **L'image grossit** (Pango, HarfBuzz, DejaVu, WeasyPrint et ses dépendances Python) : la mesure n'a pas été faite (pas de Docker sur la machine qui a écrit ce changement) ; à relever avant la fusion avec `docker build -t ea-api:report-check backend` et `docker run --rm --entrypoint python ea-api:report-check -c "from weasyprint import HTML; print(HTML(string='<p>é Ελληνικά</p>').write_pdf()[:5])"`.
- Les caractères CJK s'affichent en carrés vides dans le PDF (DejaVu couvre latin, grec, cyrillique) ; le DOCX, lui, laisse Word choisir la police.
- **Le coût d'un PDF suit le nombre de nœuds, pas les octets** : 600 Ko de paragraphes d'une lettre ont coûté 96 s et 3,9 Go, un tableau 300×300 30 s et 2,2 Go (mesures de la revue). Le plafond de nœuds ramène le pire cas admis à environ 3 s et 300 Mo (11 900 paragraphes : 2,6 s ; tableau 100×100 : 2,8 s). Le rendu dans un sous-processus que l'on peut tuer (limite mémoire, délai dur) n'est pas fait dans ce lot : c'est la montée en gamme si ces bornes ne suffisent plus.
- Un thread de rendu qui dépasse 30 s n'est pas tué : le client reçoit son 422, la place reste prise jusqu'à la fin du thread. Les plafonds bornent ce que le thread a à faire ; un processus de travail serait la montée en gamme.
- L'admission est en mémoire, par processus : avec plusieurs workers, les plafonds se multiplient (comme pour les analyses et les imports).
- Le `proxy_read_timeout` de `location /api/` du NGINX de l'Infra doit passer à 60 s (dépôt Infra, hors de ce dépôt) : un rapport peut durer jusqu'à 30 s.
- Les rapports déposés s'accumulent sous `reports/` ; aucune purge automatique, l'éditeur supprime par les fichiers.
- Le sujet « requête enregistrée » n'est pas livré : seuls `diagram` et `package` sont des sujets.

## Références

- ADR liés : [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md), [0032](0032-authentification-par-keycloak.md), [0036](0036-fichiers-dans-minio.md), [0039](0039-metadonnees-des-fichiers-dans-postgresql.md), [0050](0050-echanges-formats-collision-et-rejet-par-ligne.md), [0057](0057-controles-de-qualite-du-modele.md), [0063](0063-un-seul-dessin-de-diagramme.md), [0064](0064-publication-en-lecture.md)
- Code concerné : `backend/src/ea/domain/reports.py`, `backend/src/ea/services/reports.py`, `backend/src/ea/repositories/report_renderers.py`, `backend/src/ea/api/reports.py`
