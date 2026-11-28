# Exceptions de l'audit npm

`make audit` (et le job « Audit » de la CI) audite le frontend en deux temps :

1. **Production** : `npm audit --omit=dev --audit-level=high`. Aucune exception possible.
2. **Développement** : `scripts/npm_audit.py` lit `npm audit --json`. Tout avis `high` doit figurer, par identifiant GHSA, dans `frontend/audit-exceptions.json` et ne pas être expiré. Un avis `critical` n'est jamais couvert. Une exception expirée fait échouer l'audit ; une exception qui ne correspond plus à aucune alerte est signalée (« à supprimer ») sans échec.

La couverture se fait par identifiant d'avis uniquement, jamais par nom de paquet. Un audit qui n'a pas eu lieu ou dont le rapport est incohérent échoue (erreur de registre, `auditReportVersion` absent, chiffres de `metadata` qui ne recoupent pas les entrées, entrée high sans avis lisible, gravité inconnue).

## Exceptions actives

Aucune.

## Retirer une exception

Supprimer l'entrée de `frontend/audit-exceptions.json` (et sa ligne ci-dessus) dès qu'un correctif existe ou que la chaîne ne tire plus le paquet. Prolonger une exception est un choix à refaire, par un commit qui change la date `expires`.
