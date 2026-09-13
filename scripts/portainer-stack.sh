#!/usr/bin/env bash
# Pilote la stack « ea » (deploy/ea.stack.yml) par l'API du Portainer de
# l'Infra. Portainer clone GitHub main et construit les deux images : rien de
# ce poste n'est monté, seules les variables de deploy/ea.env partent avec la
# requête. Même mécanique que scripts/portainer-stack.sh du dépôt Infra — voir
# docs/adr/0027.
#
# Usage : scripts/portainer-stack.sh up|down|delete|selftest
#   up      crée la stack, ou la redéploie depuis main (images reconstruites)
#   down    arrête la stack (elle reste déclarée dans Portainer)
#   delete  retire la stack de Portainer
set -euo pipefail
cd "$(dirname "$0")/.."

STACK=ea
REPO_URL=https://github.com/nicolaslallier/EA
REF=refs/heads/main
COMPOSE_FILE=deploy/ea.stack.yml
STACK_ENV="${EA_STACK_ENV:-deploy/ea.env}"
PORTAINER_ENV="${PORTAINER_ENV_FILE:-.portainer.env}"
CURL_IMAGE=curlimages/curl:8.5.0@sha256:08e466006f0860e54fc299378de998935333e0e130a15f6f98482e9f8dab3058

die() { printf 'portainer-stack.sh : %b\n' "$*" >&2; exit 1; }

# Fichier d'environnement → [{name,value}], le format des variables d'une stack.
# Rien n'est interprété : ni `$`, ni commentaire de fin de ligne — un mot de
# passe généré contient les deux. Tolérés : `export `, et une paire de
# guillemets autour de la valeur. Les PORTAINER_* ne partent jamais vers un
# conteneur : la clé est un jeton root sur le démon Docker.
env_json() { # <fichier>
  jq -Rn '
    [inputs
     | sub("^export +"; "")
     | select(test("^[A-Za-z_][A-Za-z0-9_]*="))
     | capture("^(?<name>[^=]+)=(?<value>.*)$")
     | .value |= (if test("^\".*\"$") or test("^'"'"'.*'"'"'$") then .[1:-1] else . end)
     | select(.name | startswith("PORTAINER_") | not)]' <"$1"
}

selftest() {
  local tmp got want
  tmp="$(mktemp -d)"
  printf '%s\n' '# commentaire' '' 'A=1' 'export B="x y"' "C='p\$w#d'" 'EMPTY=' \
    'PORTAINER_API_KEY=secret' '  INDENTED=no' >"$tmp/env"
  got="$(env_json "$tmp/env" | jq -c .)"
  rm -rf "$tmp"
  want='[{"name":"A","value":"1"},{"name":"B","value":"x y"},{"name":"C","value":"p$w#d"},{"name":"EMPTY","value":""}]'
  [ "$got" = "$want" ] || die "selftest : env_json\n  obtenu : $got\n  attendu : $want"
  echo "portainer-stack.sh : selftest ok"
}

# curl tourne dans un conteneur jetable sur infra-net et parle à portainer:9443
# directement : le NGINX et l'oauth2-proxy de l'Infra ne sont pas sur le
# chemin. La clé arrive à curl par -K (un fichier écrit dans le conteneur),
# jamais en argument, donc jamais dans une liste de processus.
api() { # <méthode> <chemin> [corps-json]
  printf '%s' "${3:-}" | docker run --rm -i --network infra-net \
    -e PORTAINER_API_KEY --entrypoint sh "$CURL_IMAGE" -c '
      printf "header = \"X-API-Key: %s\"\n" "$PORTAINER_API_KEY" >/tmp/curl.cfg
      out="$(curl -sSk -K /tmp/curl.cfg --fail-with-body -X "$1" \
        -H "Content-Type: application/json" \
        --data-binary @- "https://portainer:9443/api$2" 2>&1)" \
        || { printf "%s\n" "$out" >&2; exit 1; }
      printf "%s" "$out"' sh "$1" "$2" \
    || die "$1 $2 a échoué (Portainer tourne ? make portainer-up dans l'Infra)"
}

cmd="${1:-}"
case "$cmd" in
  selftest) selftest; exit 0 ;;
  up|down|delete) ;;
  *) die "usage : scripts/portainer-stack.sh up|down|delete|selftest" ;;
esac

# Avant tout appel à Portainer : un fichier incomplet se refuse ici, en clair,
# plutôt que dans une réponse d'API. Les variables marquées `:?` sont lues dans
# la stack même, pour que la liste ne puisse pas diverger.
if [ "$cmd" = up ]; then
  [ -f "$STACK_ENV" ] || die "$STACK_ENV introuvable — cp deploy/ea.env.example $STACK_ENV, puis le remplir"
  env="$(env_json "$STACK_ENV")"
  missing="$(grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*:\?' "$COMPOSE_FILE" | sed -E 's/^\$\{(.*):\?$/\1/' | sort -u |
    while read -r n; do
      jq -e --arg n "$n" 'any(.[]; .name == $n and .value != "")' <<<"$env" >/dev/null || printf '%s ' "$n"
    done)"
  [ -z "$missing" ] || die "à renseigner dans $STACK_ENV : $missing"
fi

# La clé vient de l'environnement, ou à défaut de .portainer.env (ignoré par
# git). Le Portainer est celui de l'Infra : son .portainer.env convient aussi,
# PORTAINER_ENV_FILE=~/OpenCode/Infra/.portainer.env.
if [ -z "${PORTAINER_API_KEY:-}" ]; then
  [ -f "$PORTAINER_ENV" ] \
    || die "$PORTAINER_ENV introuvable — y mettre PORTAINER_API_KEY=… (Portainer → My account → Access tokens)"
  PORTAINER_API_KEY="$(jq -Rn -r '[inputs | select(startswith("PORTAINER_API_KEY=")) | sub("^[^=]*="; "")] | last // empty' <"$PORTAINER_ENV")"
  PORTAINER_ENDPOINT_ID="${PORTAINER_ENDPOINT_ID:-$(jq -Rn -r '[inputs | select(startswith("PORTAINER_ENDPOINT_ID=")) | sub("^[^=]*="; "")] | last // empty' <"$PORTAINER_ENV")}"
fi
[ -n "$PORTAINER_API_KEY" ] || die "PORTAINER_API_KEY vide dans $PORTAINER_ENV"
export PORTAINER_API_KEY

eid="${PORTAINER_ENDPOINT_ID:-$(api GET /endpoints | jq -r '[.[] | select(.Type == 1)][0].Id // empty')}"
[ -n "$eid" ] || die "aucun environnement Docker local dans Portainer"
stack="$(api GET /stacks | jq -c --arg n "$STACK" 'first(.[] | select(.Name == $n)) // empty')"
sid=""
[ -z "$stack" ] || sid="$(jq -r .Id <<<"$stack")"

case "$cmd" in
  up)
    # Portainer déploie GitHub main, pas cette copie de travail : on le dit.
    remote="$(git ls-remote origin "$REF" 2>/dev/null | cut -c1-7)" || true
    echo "portainer-stack.sh : déploie $REPO_URL main${remote:+ @ $remote} (un commit non poussé n'y est pas)"

    if [ -z "$sid" ]; then
      body="$(jq -n --arg name "$STACK" --arg url "$REPO_URL" --arg ref "$REF" --arg file "$COMPOSE_FILE" --argjson env "$env" \
        '{Name: $name, RepositoryURL: $url, RepositoryReferenceName: $ref,
          ComposeFile: $file, RepositoryAuthentication: false, Env: $env}')"
      api POST "/stacks/create/standalone/repository?endpointId=$eid" "$body" >/dev/null
      echo "portainer-stack.sh : stack '$STACK' créée"
    else
      if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
        api POST "/stacks/$sid/start?endpointId=$eid" >/dev/null
      fi
      body="$(jq -n --arg ref "$REF" --argjson env "$env" \
        '{RepositoryReferenceName: $ref, RepositoryAuthentication: false, Env: $env,
          Prune: false, RepullImageAndRedeploy: false}')"
      api PUT "/stacks/$sid/git/redeploy?endpointId=$eid" "$body" >/dev/null
      echo "portainer-stack.sh : stack '$STACK' redéployée"
    fi
    ;;
  down)
    [ -n "$sid" ] || die "aucune stack '$STACK' dans Portainer"
    if [ "$(jq -r .Status <<<"$stack")" = 2 ]; then
      echo "portainer-stack.sh : stack '$STACK' déjà arrêtée"; exit 0
    fi
    api POST "/stacks/$sid/stop?endpointId=$eid" >/dev/null
    echo "portainer-stack.sh : stack '$STACK' arrêtée"
    ;;
  delete)
    [ -n "$sid" ] || { echo "portainer-stack.sh : aucune stack '$STACK' dans Portainer"; exit 0; }
    api DELETE "/stacks/$sid?endpointId=$eid" >/dev/null
    echo "portainer-stack.sh : stack '$STACK' retirée de Portainer"
    ;;
esac
