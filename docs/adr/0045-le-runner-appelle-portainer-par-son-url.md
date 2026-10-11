---
titre: Le runner appelle Portainer par son URL, sans socket Docker
date: 2026-10-11
statut: Proposition
affects: scripts/portainer-stack.sh, .github/workflows/deploy.yml, docker-compose.runner.yml, Makefile
---

# 45. Le runner appelle Portainer par son URL, sans socket Docker

## Contexte

`docs/adr/0044` fait lancer `scripts/portainer-stack.sh up` par un runner
auto-hébergé sur le Mac. Le script supposait l'Infra dans le même Docker : un
conteneur curl sur `infra-net`, `https://portainer:9443`. Or l'Infra (Portainer,
nginx, Keycloak, PostgreSQL) tourne sur le cluster, 192.168.2.10 ; `infra-net`
est vide sur le Mac. Le premier déploiement a échoué : `curl: (6) Could not
resolve host: portainer`. L'API de Portainer reste joignable depuis le Mac par le
nginx de l'Infra, `https://portainer.infra.famillelallier.net/api`.

## Décision

- `PORTAINER_URL` (https uniquement) fait appeler `curl` **directement** par le
  script, sans `docker run`. Absente, le comportement de 0027 est inchangé
  (`make app-up` depuis l'hôte de l'Infra).
- TLS vérifié. Le certificat présenté (SAN `*.infra.famillelallier.net`) est émis
  par `CN=Infra Local Dev CA`, la CA de `INFRA_CA_CERT` : le script prend
  `PORTAINER_CA_CERT` ou `INFRA_CA_CERT` (`--cacert`). Sans CA, il refuse,
  sauf `PORTAINER_INSECURE=1` (`-k`, avec avertissement). `http://` est refusé.
- La clé reste hors des arguments : un fichier de configuration curl en mode 600,
  supprimé à la sortie.
- Le script n'appelant plus Docker en mode URL et le workflow n'en utilisant pas,
  `docker-compose.runner.yml` **retire le montage de `/var/run/docker.sock`** et
  monte à la place `infra-ca.crt` en lecture seule (`/etc/ea/infra-ca.crt`).
  C'est une réduction de privilèges : un job compromis n'est plus root sur le
  Mac, il ne tient que les deux secrets du déploiement.
- `deploy.yml` fournit `PORTAINER_URL` (variable de dépôt du même nom, sinon
  l'URL ci-dessus) ; les déclencheurs et les permissions ne changent pas.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder `docker run` et `--network host` | Aucun changement de script | Garde root sur le Mac, et le conteneur curl reste là sans raison | Écartée |
| `curl -k` partout | Rien à monter | Une clé d'API root envoyée sans authentifier le serveur | Écartée |
| Runner sur l'hôte de l'Infra | Réseau `infra-net` | Autre machine à administrer | Écartée pour l'instant |

## Conséquences

- Le déploiement dépend de la joignabilité de l'URL depuis le Mac. Mac éteint ou
  runner arrêté : le job reste en file, rien ne part ; le polling de Portainer
  (`docs/adr/0041`), s'il est activé, reste le filet.
- `make runner-up` exige désormais `INFRA_CA_CERT` dans `.runner.env` et, après
  cette PR, un `make runner-up` pour recréer le runner sans le socket.
- Non vérifié ici : un déploiement réel, ni l'appel depuis le conteneur runner.

## Références

- ADR liés : [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md), [0041](0041-portainer-interroge-main.md), [0044](0044-un-runner-auto-heberge-deploie-sur-push.md)
