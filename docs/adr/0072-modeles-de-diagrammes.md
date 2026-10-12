---
titre: Modèles de diagrammes — des schémas récurrents en squelette typé, sans élément, instanciés en une transaction
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/diagram_templates.py, backend/migrations/versions/0018_diagram_templates.py, backend/src/ea/db/models/diagram_template.py, backend/src/ea/repositories/template_store.py, backend/src/ea/services/templates.py, backend/src/ea/api/diagram_templates.py, backend/src/ea/mcp/server.py, frontend/src/features/diagrams/
---

# 66. Modèles de diagrammes

Date : 2026-10-11
Statut : Proposition

## Contexte

Un schéma revient : une application, son hôte, sa base. Le redessiner à la main coûte du temps et
produit des variantes. On veut le garder comme **squelette** et en tirer un diagramme, sans que le
squelette contienne ni n'invente un fait du catalogue ([`0031`](0031-diagrammes-vues-sans-fait.md) :
un diagramme est une vue).

## Décision

**Un modèle est un corps `jsonb` versionné — emplacements typés, groupes, notes, liens par type de
relation — validé par le domaine, jamais interrogé à l'intérieur. Il ne contient aucun élément.**

| Point | Choix |
|---|---|
| Table | `diagram_templates` (migration `0018`) : nom unique, description, `body jsonb`. Table neuve, aucune donnée existante lue ni transformée : la révision ne peut pas échouer sur le contenu de la base |
| Emplacement | Un type ArchiMate, une étiquette, éventuellement un stéréotype exigé. On le remplit par un élément existant **du bon type** (et portant le stéréotype), ou en en créant un — sauf si un stéréotype est exigé (un élément neuf ne le porterait pas) |
| Instanciation | Tout est refusé avant d'écrire (types, stéréotypes, liaisons doubles) ; puis, en **une transaction** (`ArchitectureService.transaction`), le diagramme, les éléments, les relations demandées et la scène. Un échec à mi-chemin ne laisse ni diagramme, ni élément, ni événement d'audit |
| Relations | Créées **seulement** si `create_relationships` est vrai (case décochée par défaut, faux par défaut en MCP) : les liens d'un modèle sont ce que l'auteur a dessiné, pas des faits que personne n'a décidés. Un lien déjà présent n'est pas recréé |
| Règles | Toujours celles du service : ArchiMate, IPAM, `guard_element_write` — l'instanciation ne passe jamais par un dépôt |
| Écriture | Réservée à `ea-editor`, comme toute écriture ; un lecteur voit la bibliothèque seule |
| Journal | `template.created/updated/deleted` : nom et comptes (emplacements, groupes, notes, liens), jamais le texte d'une note |

### Les plafonds (revue de sécurité « resource-cap-defeat »)

| Plafond | Valeur | Où et quand |
|---|---|---|
| Modèles dans la bibliothèque | 200 | Dans le dépôt, à l'écriture, `COUNT` en SQL **sous un verrou consultatif de transaction** : deux écritures concurrentes à 199 ne passent pas toutes deux (test d'intégration). La bibliothèque est listée en entier, par l'API et par un agent : son cardinal est donc la borne de ces lectures |
| Emplacements / groupes+notes / liens d'un corps | 50 / 100 / 100 | `parse_body` compte les listes **avant** de construire un seul objet, et `parse_body` sert à la lecture d'une ligne stockée comme à l'écriture : une ligne éditée à la main ne passe pas non plus |
| Imbrication de groupes | `MAX_GROUP_DEPTH` | Celle de la scène, vérifiée dans le corps |
| Liaisons d'une instanciation | `MAX_SLOTS` | Schéma partagé `InstantiateBindings` (HTTP **et** MCP) et garde du service avant toute lecture d'élément |
| Construction simultanée | 4 au total, 2 par appelant | `Admission` (`services/exchange_admission.py`), par `sub` du jeton ; refus `429 template_busy`, jamais une file |
| Amplification | Aucune par construction | Un modèle ne référence pas un modèle ; une instanciation écrit au plus 50 éléments, 100 relations et un diagramme, comptés avant d'écrire. N instanciations écrivent N fois cela — c'est le prix de N appels à `create_element`, et chacun reste un acte d'éditeur, audité |

La transaction unique dépasse les 64 sous-transactions que PostgreSQL suit en cache ; les bornes ci-dessus
la maintiennent sous quelques centaines de points de sauvegarde, ce qui est correct mais plus lent : c'est
un plafond connu, pas un défaut.

## Conséquences

- Un modèle enregistré depuis un diagramme garde la forme (types, positions, groupes, notes, liens), pas
  les éléments ni leurs noms.
- Les modèles ne sont ni exportés ni capturés par les baselines (ce sont des données de référence).
- Changer un plafond est une constante de domaine et une ligne dans `TestTheSameBoundsAsTheHttpAdapter`.
- L'API et l'outil MCP n'ont aucun contrôle propre : `list_diagram_templates` et
  `instantiate_diagram_template` appellent `TemplateService`.
