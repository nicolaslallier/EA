---
titre: La jonction est un élément — type `junction`, genre ET/OU dans `junction_type`
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/domain/model.py, backend/src/ea/domain/junctions.py, backend/src/ea/domain/exchange.py, backend/src/ea/formats/archimate_xml.py, backend/src/ea/formats/csv_files.py, backend/src/ea/services/architecture.py
---

# 62. La jonction est un élément

Date : 2026-10-11
Statut : Proposition

## Contexte

`junction` est l'un des 61 types d'élément d'ArchiMate 3.2 (Location, Grouping et
Junction compris) : il était dans la taxonomie, dans `rules.py` (une jonction hérite
de la légalité de ce qu'elle relaie, sauf la spécialisation) et dans `/metamodel`,
mais `Element.create` le refusait (« a junction is a connector, not a modelled
element »). Conséquences : aucune porte ET/OU ne pouvait être dessinée dans le
catalogue, et le sous-projet 3 d'échanges mettait les `AndJunction` / `OrJunction`
d'un fichier de côté (`junction_not_supported`, **ignoré**) — avec les relations qui
les touchaient, rejetées en cascade. Le sous-projet 9 (BPMN, simulation) a besoin de
portes : l'utilisateur a validé que `junction` soit autorisé.

## Décision

Une jonction est **un élément comme un autre** : `Element.create` ne la refuse plus.
Le nombre de types reste 61 (le type existait déjà). Ce qui la distingue, ET ou OU,
est une propriété `junction_type` (`and` ou `or`), validée par
`domain/junctions.validate_junction_properties`, appelée à chaque écriture de
propriétés comme `validate_ipam_properties` (`create_element`, `update_element`, et
`accept_element` d'un import). Absente, elle vaut `and` ; une autre valeur est un 422
`invalid_junction` (le message nomme la clé, jamais la valeur). Sa légalité vient du
métamodèle, inchangée : aucune règle n'est ajoutée.

**Échanges (sous-projet 3).** On a décidé de le gérer, parce que l'alternative est
pire : un export qui écrirait `xsi:type="Junction"` (le nom dérivé du type) produirait
un fichier que ni Archi ni notre lecteur n'acceptent.
- Export : `AndJunction` / `OrJunction` selon `junction_type` (XML et CSV).
- Import : ces deux noms (et `Junction` en CSV, lu comme ET) donnent un élément
  `junction` avec `junction_type` posé d'après le type du fichier, qui l'emporte sur
  une propriété du même nom. Exchange ne donne pas de nom à une jonction, alors que
  `UNIQUE(element_type, name)` en exige un : on forme `And junction <identifiant>`
  (ou `Or …`), stable d'un import à l'autre, donc idempotent (un deuxième import
  retrouve l'élément au lieu d'en créer un autre). Un nom présent dans le fichier est
  gardé.
- Le code `junction_not_supported` disparaît ; les relations qui touchent une
  jonction ne sont plus rejetées en cascade.

**Ailleurs, sans changement de code** (inventaire par grep, vérifié) : l'analyse
d'impact et le voisinage traversent une jonction comme n'importe quel nœud
(`impact_follows_direction` est par type de *relation*) ; la recherche filtre par type
`junction` ; les baselines copient `element_type` en texte (capture et restauration
sont agnostiques) ; la colonne `element_type` est un `Text` sans contrainte, donc
**aucune migration** ; le SPA tire sa palette de `/metamodel`, où `junction` figure
déjà (ADR 0012) ; aucune copie de règle en TypeScript.

**Ce que la jonction hérite, et ce qui n'est pas vérifié.** `permits()` laisse passer tout type de relation de ou vers une jonction sauf la spécialisation (ADR 0005 : « du côté permissif quand la spécification est ambiguë »). On soupçonne que la norme ArchiMate 3.2 est plus stricte (même type de relation de chaque côté d'une jonction, composition et agrégation exclues). Le texte de la norme n'est nulle part dans le dépôt, l'ADR 0005 ne le cite pas, et on ne se fie pas à la mémoire : **aucune règle n'est ajoutée**, la jonction reste permissive, et la vérification contre le texte officiel est à faire avant de resserrer. La resserrer ne s'appliquerait qu'à l'écriture (`connect`) et ne casserait aucune donnée stockée. La ligne « une jonction n'est pas un élément catalogable » de l'ADR 0005 est caduque pour ce qui est de la création ; le reste de cet ADR tient.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Un type `and_junction` / `or_junction` | pas de propriété | un 62ᵉ type, hors ArchiMate 3.2 ; matrice, `/metamodel`, SPA et tests à toucher | Écartée |
| Continuer d'ignorer les jonctions à l'import | rien à écrire | un modèle Archi avec portes perd des liens, en silence apparent | Écartée |
| Nom vide pour une jonction | fidèle à Exchange | `UNIQUE(element_type, name)` : une seule jonction possible | Écartée |
| Interdire `junction_type` autre que ET/OU par contrainte SQL | garantie en base | une migration pour une valeur que le service valide déjà ; l'IPAM fait de même | Écartée |

## Conséquences

Une jonction a un nom à saisir dans le SPA (comme tout élément) et un genre à écrire
dans les propriétés : pas de sélecteur dédié pour l'instant. Un ancien fichier ré-importé
après coup crée désormais les jonctions que le premier import avait ignorées ; les
relations qui les touchaient avaient été rejetées et ne reviennent que si le fichier est
ré-importé. Deux jonctions de deux fichiers différents ayant le même identifiant
fusionnent (même nom formé) : c'est la règle générale des éléments, pas une exception.
Un contrôle de qualité « une porte a une entrée et une sortie » reste à écrire avec le
sous-projet 4.

## Références

- ADR liés : [0005](0005-archimate-3-2-comme-metamodele.md), [0012](0012-ecran-du-metamodele.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0053](0053-echanges-csv-disposition-archi.md)
- Code concerné : `backend/src/ea/domain/junctions.py`, `backend/src/ea/domain/exchange.py`, `backend/src/ea/formats/`
