---
titre: defusedxml pour lire les fichiers d'échange, lxml en développement pour le XSD
date: 2026-10-11
statut: Proposition
affects: backend/pyproject.toml, backend/uv.lock, backend/tests/unit/test_exchange_xsd.py, backend/tests/unit/test_exchange_structure.py
---

# 49. defusedxml pour lire les fichiers d'échange, lxml en développement pour le XSD

Date : 2026-10-11
Statut : Proposition

## Contexte

L'import d'un fichier ArchiMate Exchange (lot 2) fera lire à l'API du XML venu d'ailleurs. Le XML est le format
d'attaque classique : entités externes (XXE : lecture d'un fichier ou requête réseau du serveur), expansion
d'entités (« billion laughs »), DTD externe. `xml.etree.ElementTree` de la bibliothèque standard est exposé à
l'expansion d'entités. `CLAUDE.md` exige un ADR pour toute dépendance nouvelle.

Par ailleurs, la conformité de l'export au schéma de l'Open Group (`archimate3_Model.xsd`) se vérifie avec un
validateur XSD, que la bibliothèque standard n'a pas. Or la **licence de ce schéma n'a pas été lue** : il n'est ni
téléchargé ni versionné dans ce dépôt, et aucun fichier tiers (modèle d'exemple Archisurance ou autre) n'y entre.

## Décision

- **`defusedxml`** (`defusedxml.ElementTree.fromstring` avec `forbid_dtd=True`, `forbid_entities=True`,
  `forbid_external=True`) est la seule porte d'analyse du XML reçu. Dépendance d'exécution, sans dépendance
  propre, ajoutée dès ce lot (elle était déjà dans `uv.lock` par transitivité). Toute DTD sera refusée
  (`invalid_exchange_file`) ; aucun accès réseau ni disque pendant l'analyse. Les tests d'export s'en servent
  déjà pour relire ce que l'export écrit.
- **`lxml`**, en dépendance de **développement seulement**, valide l'export contre le schéma quand l'opérateur le
  fournit : la variable d'environnement `EA_EXCHANGE_XSD` désigne un `archimate3_Model.xsd` local (avec ses
  voisins). Sans elle le test se **saute avec un message explicite**. `lxml` n'entre jamais dans l'image de
  production. `types-defusedxml` (développement) fournit les stubs que `mypy --strict` exige.
- **Des contrôles structurels écrits à la main s'exécutent toujours** (`test_exchange_structure.py`) : espace de
  noms, sections et leur ordre, `xsi:type` et `xml:lang` présents, identifiants uniques et valides, références de
  relations et de propriétés résolues, aucune colonne interne dans les propriétés. Ils n'équivalent pas au XSD.
- L'écriture du XML ne dépend d'aucune bibliothèque : des chaînes échappées, parce qu'un export est diffusé en
  flux et que le modèle n'est jamais tenu en mémoire (ADR 0048).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| `xml.etree` seul | aucune dépendance | expansion d'entités possible, garde-fous à recoder | écarté |
| `lxml` à l'exécution | rapide, XSD possible en production | surface d'attaque et dépendance native plus grandes, binaire à compiler dans l'image | écarté |
| Analyseur SAX maison | contrôle total | code de sécurité à maintenir soi-même | écarté |
| Versionner le XSD dans le dépôt | le test tourne en CI | licence non lue : redistribution non établie | écarté |
| Télécharger le XSD pendant les tests | pas de fichier versionné | un test qui sort de la machine ; violé par la garde réseau | écarté |
| Valider chaque import contre le XSD | rejette tôt | impose `lxml` et le XSD en production, refuse des fichiers que d'autres outils lisent | écarté |

## Conséquences

- **La conformité XSD réelle de l'export n'est pas vérifiée** tant que personne ne fournit le schéma. Le jour où
  la licence est lue et le dépôt autorisé à le contenir, on le dépose dans `backend/tests/data/exchange/xsd/` et
  on retire la variable d'environnement ; d'ici là, un opérateur peut lancer le test avec `EA_EXCHANGE_XSD=…`.
- Une dépendance directe de plus (`defusedxml`) que `pip-audit` surveille ; `lxml` et les stubs ne sont que dans
  l'extra `dev`.
- La valeur par défaut d'`accessType` (absent = `Write` dans le schéma) n'est pas vérifiée non plus : l'export
  écrit toujours l'attribut pour une relation d'accès.
- La compatibilité réelle avec Sparx EA n'est pas testée en CI.

## Références

- ADR liés : [0048](0048-echanges-formats-collision-et-rejet-par-ligne.md)
- Code concerné : `backend/pyproject.toml`, `backend/tests/unit/test_exchange_xsd.py`, `backend/tests/unit/test_exchange_structure.py`
