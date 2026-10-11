---
titre: Les contrôles de qualité du modèle, calculés à la demande
date: 2026-10-10
statut: Proposition
affects: backend/src/ea/domain/quality.py, backend/src/ea/services/analysis.py, backend/src/ea/api/analysis.py, backend/src/ea/core/config.py, backend/src/ea/mcp/server.py, frontend/src/features/quality, CLAUDE.md
---

# 57. Les contrôles de qualité du modèle, calculés à la demande

Date : 2026-10-10
Statut : Proposition

## Contexte

Le métamodèle refuse à l'écriture ce qu'ArchiMate interdit. Il ne dit rien de
ce qui est seulement *suspect* : un élément relié à rien, deux noms qui se
ressemblent à un accent près, une description absente, un lien que le
métamodèle ne permettrait plus aujourd'hui parce qu'il a évolué ou qu'un import
l'a laissé passer. Sparx EA appelle cela les contrôles du modèle ; ici le
pipeline `alimenter-catalogue` (`adr/0028`) en a un besoin immédiat : lire ce
qu'un import a laissé.

Deux tentations ont été écartées : stocker le résultat d'un contrôle (il serait
périmé à la modification suivante d'un élément, donc une seconde vérité), et
laisser chaque client recalculer les règles (trois adaptateurs, trois définitions
d'un « doublon »).

## Décision

1. **Les contrôles sont calculés à chaque appel**, sur les éléments du
   catalogue et les liens qui les touchent (deux lectures), puis passés à des
   fonctions pures de `domain/quality.py`. Aucune table, aucune migration.
2. **Sept règles au plus, chacune identifiée par un code stable**
   (`orphan`, `duplicate_probable`, `naming`, `missing_property`,
   `no_description`, `inconsistent_relationship`, plus `unreadable_property` au
   lot suivant). Un code ne se renomme pas : le SPA, un agent et le pipeline
   s'appuient dessus. Les gravités sont fixées par la règle (`error`, `warning`,
   `info`), pas par l'appelant.
3. **Rien n'est corrigé automatiquement.** Un constat nomme l'élément et un
   `fix_hint`, route de l'application ; un lien devenu incohérent est signalé en
   `error`, jamais supprimé.
4. **Deux paramètres seulement, en configuration** : `EA_QUALITY_SIMILARITY`
   (seuil de ressemblance, 0,9 par défaut) et `EA_QUALITY_REQUIRED` (JSON, les
   propriétés qu'un type doit porter). Validés au démarrage par
   `QualityParams.from_settings` : un type inconnu ou une clé qui n'est pas un
   identifiant arrête le boot. Une table `quality_rules` n'existe que si des
   paramètres par périmètre sont demandés.
5. **Doublons** : même type, noms égaux une fois la casse, les accents et la
   ponctuation retirés, ou ressemblance (`difflib.SequenceMatcher`) au moins égale
   au seuil. Comparaison par blocs (type, première lettre normalisée) ; un
   `duplicate_probable` est un avertissement, jamais une erreur, et il n'y a pas
   de liste d'exceptions (rien n'est stocké).
6. **Plafond** : 5 000 éléments par exécution (`MAX_QUALITY_ELEMENTS`). Au-delà,
   422 `scope_too_large` : un rapport partiel se lirait comme un rapport propre.
7. **Les compteurs décrivent tout l'ensemble**, les filtres `severity` et `code`
   ne restreignent que la liste, paginée par un `cursor` (page de 1 000 au plus,
   `MAX_FINDINGS`).
8. **Les orphelins ne passent pas par un `NOT EXISTS`** : avec le plafond ci-dessus,
   la règle pure sur `links_touching` donne le même résultat sans second chemin.
9. **Hors de la boucle d'événements, avec un plafond d'admission.** Le calcul des
   règles est lié au CPU : il tourne dans un thread (`asyncio.to_thread`), et la
   place qu'il occupe (`Admission` de `services/exchange_admission.py`, 4 en tout, 2
   par appelant, refus 429 `analysis_busy`) n'est rendue qu'à la fin réelle du
   thread, même si le client raccroche ou si le bail échoit (`lease.hold`). Un
   calcul qui tourne encore ne cesse jamais de compter.
10. **Une seule échelle de gravité** : `Severity` est celle de `domain/exchange.py`,
   réutilisée telle quelle (mêmes valeurs, même sens) plutôt que dupliquée.
11. **Trace** : une ligne `action=quality_run` avec `duration_ms` et le nombre de
   constats ; jamais de valeur de propriété.

12. **Le rapprochement des doublons a un budget.** Au plus 50 000 comparaisons floues par
   exécution (`MAX_DUPLICATE_COMPARISONS`), sur un préfixe de 64 caractères du nom normalisé ;
   deux noms normalisés égaux ne coûtent rien. Le budget est un compte, pas un délai : le
   domaine reste pur et le résultat reproductible. Épuisé, la règle s'arrête et le rapport porte
   `partial: true` (API, outil MCP, bandeau du SPA) : un résultat court ne prouve pas l'absence
   de doublons. Mesuré avant : 5 000 noms de 20 caractères en 35 s, 1 500 anagrammes de 200
   caractères en 78 s.
13. **Les lectures sont bornées dans la requête** (catalogue : `LIMIT` plafond + 1 ; liens :
   50 000 au plus pour une exécution, 10 000 pour la matrice), jamais après chargement.

## Limites connues

- Faux positifs : « v1 » et « v2 » se ressemblent assez pour être signalés ; le seuil est le seul
  remède, il n'y a pas de liste d'exceptions.
- Une faute sur la première lettre d'un nom échappe au rapprochement (blocs par type et initiale).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Stocker les constats | lecture immédiate, historique | périmés à chaque édition, deux vérités | écartée |
| Règles définies par l'utilisateur | souple | hors périmètre, surface d'injection | écartée |
| Détection d'orphelins en SQL (`NOT EXISTS`) | tient un très grand modèle | second chemin à garder identique ; inutile sous 5 000 éléments | écartée, à rouvrir si le plafond monte |
| Auto-correction | gain de temps | peut détruire une donnée voulue | écartée |
| Liste d'exceptions de doublons | zéro faux positif | demande un stockage | écartée |

## Conséquences

- Un contrôle est aussi frais que le modèle ; il coûte deux requêtes et du CPU
  quadratique par bloc de noms (bornés par le plafond).
- Un faux positif de doublon se règle en montant le seuil pour tout le monde,
  pas pour un cas.
- Une erreur de frappe sur la *première lettre* d'un nom échappe au rapprochement ;
  acceptée, c'est un avertissement.
- Quand les paquets existeront (sous-projet 1), `GET /quality` gagnera un
  périmètre sans changer le contrat des constats.
- Les messages contiennent des noms d'éléments : le SPA les rend en texte.

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0028](0028-pipelines-python-dans-le-depot-ea.md), [0032](0032-authentification-par-keycloak.md), [0056](0056-matrice-de-relations.md)
- Code concerné : `backend/src/ea/domain/quality.py`, `backend/src/ea/services/analysis.py`
