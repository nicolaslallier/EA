# Exceptions de l'audit npm

`make audit` (et le job « Audit » de la CI) audite le frontend en deux temps :

1. **Production** : `npm audit --omit=dev --audit-level=high`. Aucune exception possible.
2. **Développement** : `scripts/npm_audit.py` lit `npm audit --json`. Tout avis `high` doit figurer, par identifiant GHSA, dans `frontend/audit-exceptions.json` et ne pas être expiré. Un avis `critical` n'est jamais couvert. Une exception expirée fait échouer l'audit ; une exception qui ne correspond plus à aucune alerte est signalée (« à supprimer ») sans échec.

La couverture se fait par identifiant d'avis uniquement, jamais par nom de paquet.

## Exceptions actives

| Avis | Paquet / plage | Portée | Raison | Ajoutée | Expire |
|---|---|---|---|---|---|
| [GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) | braces `<=3.0.3` | développement uniquement : `@vue/eslint-config-typescript` → fast-glob → micromatch → braces | Aucun correctif amont : braces 3.0.3, micromatch 4.0.8 et fast-glob 3.3.3 sont les dernières versions ; la rétrogradation de l'outil de lint exigerait ESLint 10 → 9. | 2026-10-11 | 2027-01-09 |

## Retirer une exception

Supprimer l'entrée de `frontend/audit-exceptions.json` (et la ligne ci-dessus) quand braces publie un correctif ou que l'outil de lint ne dépend plus de cette chaîne. Prolonger une exception est un choix à refaire, par un commit qui change la date `expires`.
