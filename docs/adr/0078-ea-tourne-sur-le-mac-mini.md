---
titre: EA tourne sur le Mac mini, derrière le NGINX du laptop
date: 2026-11-28
statut: Proposition
affects: deploy/ea.stack.yml, scripts/portainer-stack.sh, Makefile, nginx/conf.d/ea.conf (dépôt Infra)
---

# 78. EA tourne sur le Mac mini, derrière le NGINX du laptop

Date : 2026-11-28
Statut : Proposition — rien n'a encore été déployé sur le mini, et le vhost du
dépôt Infra n'est pas modifié.

## Contexte

La stack « ea » n'existe plus sur aucun environnement Portainer :
`https://ea.infra.famillelallier.net` répond 502. PostgreSQL, SeaweedFS,
Keycloak et NGINX tournent sur le laptop (`192.168.2.10`). EA doit tourner sur le
Mac mini (`192.168.2.68`, environnement Portainer 7). Son `infra-net` porte le
même nom que celui du laptop, sans y être relié.

## Décision

- La stack publie `${EA_WEB_PORT:-8300}:80` et `${EA_API_PORT:-8301}:8000` ; le
  vhost `ea.conf` du dépôt Infra appelle ces deux ports sur le mini. L'URL, le
  certificat, le realm et les réglages MCP ne changent pas.
- L'API joint PostgreSQL (`192.168.2.10:5432`), S3 (`192.168.2.10:8333`) et
  Ollama (`:11434`) par le LAN. `extra_hosts` fixe `keycloak.famillelallier.net`
  sur `192.168.2.10` ; le CA de l'Infra reste vérifié.
- `scripts/portainer-stack.sh` vise l'environnement 7. Seule la variable
  `PORTAINER_ENDPOINT_ID` du terminal change la cible, jamais le fichier de la
  clé, qui peut être celui de l'Infra. Une stack « ea » déclarée sur un autre
  environnement fait échouer toute commande, sauf `delete` quand la cible en a
  une aussi, pour sortir d'un doublon.

Remplace en partie [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md) :
une seule origine derrière NGINX demeure ; « aucun port publié » et « les
dépendances par leur nom sur `infra-net` » tombent pour EA. Les défauts
d'adresse de 0056 et 0036 changent de même.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Un NGINX TLS sur le mini, DNS vers lui | Le trajet des utilisateurs est chiffré | PostgreSQL et S3 restent en clair ; une clé privée et un proxy de plus | Écartée |
| Un tunnel entre les deux Docker | Tout le trajet chiffré | Trop de pièces pour une application | Écartée |
| Rester sur le laptop | Aucun changement | EA devait tourner sur le mini | Écartée |

## Conséquences

- **Sans TLS entre les deux machines** : requêtes et jetons des utilisateurs
  (de NGINX au mini), trafic PostgreSQL et fichiers S3. Le mode
  d'authentification de PostgreSQL sur ce trajet n'a pas été vérifié.
- Les ports 8300 et 8301 sont joignables par tout le LAN sans passer par NGINX.
  Le jeton reste exigé ; les limites de taille de requête de NGINX ne
  s'appliquent pas. Le routeur ne doit pas les rediriger.
- La garde de boucle de `/mcp` était déjà levée (0034) ; `/mcp` est donc aussi
  joignable sur le port 8301, derrière le jeton et `EA_MCP_ALLOWED_HOSTS`, qui
  n'authentifie personne.
- EA dépend des deux machines.
- `make app-ps` et `app-logs` lisent le Docker local : les logs se lisent dans
  Portainer.
- Changer un port demande de changer le vhost dans le dépôt Infra.
- Le runner `runner-ea` redémarrait en boucle au moment de cette décision ; il
  n'est pas traité ici.

## Références

- ADR liés : [0027](0027-application-deployee-derriere-le-nginx-de-l-infra.md),
  [0034](0034-mcp-derriere-le-nginx-pour-librechat.md), [0041](0041-portainer-interroge-main.md),
  [0045](0045-le-runner-appelle-portainer-par-son-url.md)
- Conception : `docs/superpowers/specs/2026-11-28-ea-mac-mini-design.md`
