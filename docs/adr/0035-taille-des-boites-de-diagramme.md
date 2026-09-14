---
titre: La taille des boîtes de diagramme, retenue à côté de leur position
date: 2026-09-14
statut: Accepté
affects: backend/src/ea/domain/diagrams.py, backend/src/ea/db/models/diagram.py, backend/migrations/versions/0007_diagram_box_size.py, backend/src/ea/repositories/diagram_store.py, backend/src/ea/api/schemas.py, frontend/src/lib/diagramGeometry.ts, frontend/src/features/diagrams/DiagramCanvas.vue, frontend/src/features/diagrams/useDiagram.ts
---

# 35. La taille des boîtes de diagramme

Date : 2026-09-14
Statut : Accepté

## Contexte

[`0031`](0031-diagrammes-enregistres.md) retient, pour chaque boîte d'un
diagramme, `(element_id, x, y)` : toutes les boîtes ont la taille des anneaux du
voisinage (132 x 46). Un nom long est tronqué, et un architecte ne peut pas
donner plus de place à l'élément central d'un schéma. Il veut agrandir une boîte
à la souris, et la retrouver agrandie le lendemain.

## Décision

**Une boîte retient sa largeur et sa hauteur, dans la même ligne que sa
position** : `diagram_nodes.width` et `height` (migration `0007`), `Double`
non nuls, avec pour défaut serveur la boîte d'avant, si bien que les boîtes déjà
enregistrées gardent la taille qu'elles avaient à l'écran. La taille reste de la
disposition : elle n'est pas un fait, le diagramme n'en possède toujours aucun.

**Dans l'API, les deux champs sont requis** et bornés une fois dans
`api/schemas.py` (`BoxSize`, de 20 à 5 000). Un défaut les rendrait facultatifs
en entrée et présents en sortie : FastAPI publierait alors deux schémas,
`DiagramNode-Input` et `DiagramNode-Output`, et renommerait le type que le SPA
importe. Le seul client qui écrit une disposition est le SPA, mis à jour dans le
même changement.

**Le SPA redimensionne la boîte sélectionnée par une poignée dans son coin
inférieur droit.** Le geste suit celui du déplacement : la toile émet la taille
à chaque mouvement, et la fin du geste déclenche l'enregistrement de toute la
disposition. `lib/diagramGeometry.ts` calcule sur des rectangles (`Rect`) au
lieu d'une taille constante, et `MIN_BOX` (60 x 30) garde une boîte lisible —
une borne d'ergonomie au-dessus de celle de l'API, pas une seconde règle.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Colonnes nullables, `NULL` = taille par défaut | Aucune valeur écrite pour une boîte jamais touchée | Chaque lecteur doit connaître le défaut ; un `NULL` à interpréter partout | Écarté |
| Garder la taille dans le navigateur | Aucune migration | Perdue d'un poste à l'autre, comme la disposition que `0031` refusait d'y laisser | Écarté |
| Champs facultatifs dans l'API | Un ancien client continue d'écrire | Schéma dédoublé, type du SPA renommé ; aucun ancien client n'existe | Écarté |

## Conséquences

- Une disposition envoyée sans taille est un 422. Un onglet resté ouvert sur
  l'ancien SPA pendant un déploiement voit son enregistrement refusé, et
  l'erreur affichée, jusqu'au rechargement.
- Le redimensionnement se fait à la souris ou au doigt seulement, comme le
  déplacement : aucun des deux n'a encore d'équivalent au clavier.

## Références

- ADR liés : [0031](0031-diagrammes-enregistres.md), [0033](0033-postgresql-seul-pour-le-graphe.md)
- Code concerné : `backend/migrations/versions/0007_diagram_box_size.py`, `frontend/src/lib/diagramGeometry.ts`
