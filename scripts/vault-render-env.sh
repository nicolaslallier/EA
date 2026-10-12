#!/usr/bin/env bash
# Rend les SECRETS de deploy/ea.env depuis OpenBao (KV v2, infra/env) : la base
# du mot de passe de la base et du secret S3 devient le coffre, plus une copie
# faite à la main. Les autres lignes du fichier (chemin du CA, clé d'accès…)
# sont laissées telles quelles. Même jeton que `make vault-env` de l'Infra.
#
# Usage : scripts/vault-render-env.sh   (EA_STACK_ENV, INFRA_DIR, VAULT_URL)
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${EA_STACK_ENV:-deploy/ea.env}"
INFRA_DIR="${INFRA_DIR:-$HOME/OpenCode/Infra}"
VAULT_URL="${VAULT_URL:-https://vault.infra.famillelallier.net}"
# variable de la stack EA <- clé du coffre
MAP="EA_POSTGRES_PASSWORD=EA_DB_PASSWORD EA_S3_SECRET_KEY=EA_S3_SECRET_KEY"

die() { echo "vault-render-env.sh: $*" >&2; exit 1; }
[ -f "$INFRA_DIR/.openbao.env" ] || die "$INFRA_DIR/.openbao.env introuvable (make vault-init dans l'Infra)."
command -v jq >/dev/null || die "jq est requis."

# shellcheck disable=SC1091
BAO_TOKEN="$(. "$INFRA_DIR/.openbao.env" && printf %s "${BAO_TOKEN:?BAO_TOKEN absent}")"
# Le jeton passe par un fichier de config curl lu sur stdin, jamais par argv.
secret="$(printf 'header = "X-Vault-Token: %s"\n' "$BAO_TOKEN" \
  | curl -fsS -m 10 --cacert "$INFRA_DIR/certs/infra-ca.crt" -K - "$VAULT_URL/v1/infra/data/env" \
  | jq -c .data.data)" || die "coffre illisible (scellé ? jeton expiré ?)."

[ -f "$OUT" ] || : > "$OUT"
chmod 600 "$OUT"
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
cp "$OUT" "$tmp"
for pair in $MAP; do
  var="${pair%%=*}"; key="${pair#*=}"
  val="$(jq -er --arg k "$key" '.[$k]' <<<"$secret")" || die "clé $key absente de infra/env."
  grep -v "^$var=" "$tmp" > "$tmp.n" || true
  printf '%s=%s\n' "$var" "$val" >> "$tmp.n"; mv "$tmp.n" "$tmp"
  echo "  $var <- infra/env:$key"
done
cat "$tmp" > "$OUT"
