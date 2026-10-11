# Rétention du journal d'audit et des baselines

Deux choses grossissent sans que rien ne les borne, et il n'existe
**aucune purge automatique** ni pour l'une ni pour l'autre. Ce document est la procédure
d'un opérateur, **hors application** : l'API et les services ne peuvent ni
modifier ni supprimer une ligne du journal (les triggers lèvent), et c'est
voulu. Voir `docs/adr/0046` (journal), `docs/adr/0047` (baselines) et
`docs/adr/0048` (approbation).

## Ordres de grandeur

Estimations, **pas des mesures** : à remplacer par un `SELECT` sur la base réelle
dès qu'elle a servi quelques mois.

- **Journal (`audit_events`)** : de l'ordre de 2 Ko par événement (`before` et
  `after` en `jsonb`, jamais le texte d'un document). Dix mille écritures par
  an donnent une vingtaine de Mo ; une restauration de baseline écrit un
  événement par entité touchée, d'un coup. Le rétro-remplissage de la migration
  `0011` ajoute un événement par élément et par lien existant
  (`correlation = 'backfill'`).
- **Baselines (`baseline_*`)** : une copie complète du graphe par capture, donc
  proportionnelle au catalogue, jusqu'à 64 Mo de texte par baseline (borne de
  `domain/baselines.py`). Il n'y a **aucun plafond** sur leur nombre : l'espace
  croît avec chaque capture, y compris celle de `make baseline-capture`.

Pour mesurer :

```sql
SELECT pg_size_pretty(pg_total_relation_size('audit_events')) AS audit,
       (SELECT count(*) FROM audit_events) AS events,
       (SELECT min(at) FROM audit_events) AS oldest;
SELECT b.name, b.created_at, b.element_count, b.relationship_count, b.box_count
  FROM baselines b ORDER BY b.created_at;
```

## Quand agir

Pour le journal : au-delà de **24 mois**, ou si la table dépasse ~5 Go. Pour les
baselines : quand une baseline n'a plus de raison d'être comparée ou
restaurée. Une baseline ne dépend pas du journal : purger l'un ne casse ni
l'autre, ni aucune restauration.

## Procédure : le journal

1. **Archiver d'abord.** `make pg-backup` écrit un `pg_dump -Fc` de toute la base
   (la table y est), relu avant d'être renommé (`docs/adr/0025`). Un dump qui n'a
   pas quitté l'hôte n'est pas une sauvegarde : le copier ailleurs avant de
   continuer.
2. **Purger**, avec le rôle propriétaire de la base — **vérifier d'abord quel
   rôle la session utilise** (`SELECT current_user;`), car seul le propriétaire
   peut désactiver un trigger — **hors heures d'usage**, en une transaction :

   ```sql
   BEGIN;
   ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete;
   DELETE FROM audit_events WHERE at < now() - interval '24 months';
   ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete;
   COMMIT;
   ```

   Deux choses à savoir avant de lancer :

   - `DISABLE TRIGGER` prend un verrou `ACCESS EXCLUSIVE` sur `audit_events`
     jusqu'au `COMMIT`. Chaque écriture du modèle (éléments, liens, documents,
     baselines…) insère un événement : pendant ce temps, **toutes ces écritures
     attendent**. D'où l'heure creuse, et une plage de suppression bornée.
   - **Cette purge n'est pas tracée dans l'audit** : elle contourne justement ce
     qui l'aurait écrite. Consigner qui l'a faite, quand, les bornes
     (`at < …`) et le nombre de lignes supprimées dans un
     registre d'exploitation hors base (ticket, journal de l'opérateur).

   Le trigger `audit_events_no_truncate` reste actif : ne jamais vider la table
   d'un coup.
3. **Vérifier** : `SELECT count(*), min(at) FROM audit_events;` et que les deux
   triggers sont réactivés (`SELECT tgname, tgenabled FROM pg_trigger WHERE
   tgrelid = 'audit_events'::regclass AND NOT tgisinternal;` — `tgenabled = 'O'`).

## Procédure : les baselines

1. `make pg-backup`, comme ci-dessus.
2. Supprimer la baseline depuis l'écran *Baselines* (ou `DELETE /baselines/{id}`) :
   la copie disparaît, le modèle vivant n'est pas touché, et la suppression est
   elle-même un événement `baseline.deleted` du journal. Son historique reste
   lisible (lien « Historique » de l'écran).
3. Avant la mise en service, `make baseline-capture CONFIRM=yes` prend la baseline
   « Avant mise en service <date> » ; la garder tant que le service n'a pas un
   historique qui la remplace.

## Limites

- Aucune chaîne de hachage : un opérateur de la base peut réécrire le journal, et
  rien dans la base ne le montre. Comparer deux `pg_dump` détecte une divergence
  *seulement* si la sauvegarde la plus ancienne est sortie de l'hôte avant
  l'altération et reste intacte ; un dump pris après, ou stocké au même endroit
  que la base, ne prouve rien.
- Le rétro-remplissage de la migration `0011` est purgé comme le reste.
- Si un rôle PostgreSQL d'écriture `INSERT, SELECT` seul est provisionné (à faire
  hors de ce repo), l'application ne pourra même plus désactiver un trigger ; la
  purge exige alors le rôle propriétaire.
- Rien ici n'est exécuté par l'application ni planifié : une purge oubliée ne
  casse rien, elle laisse seulement la base grossir.
