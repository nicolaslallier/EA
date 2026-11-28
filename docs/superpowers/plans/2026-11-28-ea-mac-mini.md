# EA sur le Mac mini — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** déployer la stack « ea » sur le Mac mini (Portainer, environnement 7), servie à `https://ea.infra.famillelallier.net` par le nginx du laptop.

**Architecture:** le mini publie les ports 8300 (SPA) et 8301 (API) ; son API joint PostgreSQL, S3, Keycloak et Ollama sur `192.168.2.10`. Le script Portainer vise l'environnement 7 et refuse une stack `ea` déclarée ailleurs. Une PR Infra séparée repointe `ea.conf`.

**Tech Stack:** Docker Compose, API Portainer, bash + jq, pytest, NGINX.

**Spec:** `docs/superpowers/specs/2026-11-28-ea-mac-mini-design.md`

## Global Constraints

- Hôte du mini : `192.168.2.68` ; environnement Portainer : `7`.
- Hôte des dépendances : `192.168.2.10` (PostgreSQL `5432`, S3 `8333`, Keycloak `443`, Ollama `11434`).
- Ports publiés : `${EA_WEB_PORT:-8300}:80`, `${EA_API_PORT:-8301}:8000`.
- URL, certificat, realm et réglages MCP inchangés ; le CA de l'Infra reste vérifié.
- Test d'abord (rouge pour la bonne raison). `make check` vert avant la PR ; il ne modifie aucun fichier.
- Branche `chore/deploy-mac-mini` (existe déjà), Conventional Commits, attribution donnée par la session.
- **Aucun merge, aucun `app-up`, aucune action sur le mini, la base ou Infra sans l'accord de l'utilisateur.** Un accord ne vaut que pour l'étape demandée.
- Aucun secret affiché, journalisé ou commité.

## Review Focus

1. `PORTAINER_ENDPOINT_ID` écrit dans un fichier de clé partagé avec Infra (où la cible est 3) : il ne doit pas changer la cible d'EA en silence (tâche 2).
2. Une stack `ea` déclarée sur un autre environnement, seule ou en double : toute commande refuse et nomme l'environnement (tâche 2).
3. Un `deploy/ea.env` ancien qui redéfinit `EA_POSTGRES_HOST=postgres` ou `EA_S3_ENDPOINT=s3:8333` : `app-up` doit le nommer avant de partir (comportement `overrides` existant ; vérifié à la tâche 4, étape 3).
4. Un conteneur du mini qui ne joint pas le LAN : l'API ne démarre pas (PostgreSQL, Keycloak) ou `/health` est `degraded` (S3, Ollama). Aucun test local ne le prouve ; tâche 4, étape 4.
5. Le port 8300 ou 8301 déjà pris sur le mini : le vérifier avant `app-up` (tâche 4, étape 1).

---

### Task 1: la stack publie ses ports et joint le laptop par le LAN

**Files:**
- Modify: `deploy/ea.stack.yml`, `deploy/ea.env.example`
- Test: `backend/tests/unit/test_deploy_stack.py`

**Interfaces:**
- Produces: API sur `192.168.2.68:8301`, SPA sur `192.168.2.68:8300`.

- [ ] **Step 1: écrire les tests**

Dans `test_the_deployed_api_keeps_its_files_in_the_infra_seaweedfs_on_infra_net`, renommé `..._seaweedfs_of_the_laptop` (docstring : le mini n'est pas sur l'`infra-net` du laptop, ADR 0078) :

```python
assert env["EA_S3_ENDPOINT"] == "${EA_S3_ENDPOINT:-192.168.2.10:8333}"
```

Nouveaux tests :

```python
def test_the_stack_publishes_the_two_ports_the_laptop_nginx_calls() -> None:
    """Le vhost ea.conf d'Infra appelle ces deux ports sur le mini (docs/adr/0078)."""
    assert '- "${EA_API_PORT:-8301}:8000"' in _stack_service("api")
    assert '- "${EA_WEB_PORT:-8300}:80"' in _stack_service("web")


def test_the_deployed_api_reaches_the_laptop_over_the_lan() -> None:
    api = _stack_service("api")

    assert api_environment()["EA_POSTGRES_HOST"] == "${EA_POSTGRES_HOST:-192.168.2.10}"
    assert '- "keycloak.famillelallier.net:192.168.2.10"' in api
```

- [ ] **Step 2: les voir échouer**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q`
Expected: 3 FAIL (endpoint S3, ports, hôte PostgreSQL).

- [ ] **Step 3: modifier la stack**

Ajouter `ports:` aux deux services et `extra_hosts:` à `api`, changer les deux défauts. Les alias `infra-net` et `networks:` restent (le réseau existe sur le mini). Réécrire les commentaires qui disent « rien ne publie de port », « sur infra-net c'est le service `s3` », « alias de `nginx` » et l'en-tête du fichier : mini, ports sur le LAN, trafic sans TLS, renvoi à `docs/adr/0078`.

Dans `deploy/ea.env.example`, corriger les défauts commentés (`EA_POSTGRES_HOST`, `EA_S3_ENDPOINT`) et ajouter `# EA_API_PORT=8301`, `# EA_WEB_PORT=8300`, avec la note que les changer demande de changer `ea.conf` dans Infra.

- [ ] **Step 4: les voir passer**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q`
Expected: tout PASS.

- [ ] **Step 5: commit** — `feat(deploy): run the ea stack on the Mac mini`

---

### Task 2: le script vise l'environnement 7 et refuse les autres

**Files:**
- Modify: `scripts/portainer-stack.sh`, `Makefile`
- Test: `backend/tests/unit/test_deploy_stack.py`

**Interfaces:**
- Produces: cible = variable d'environnement `PORTAINER_ENDPOINT_ID`, sinon `7`. La valeur écrite dans le fichier de clé n'est plus lue.

- [ ] **Step 1: écrire les tests**

Étendre `_run_portainer` avec deux paramètres nommés : `command: str = "delete"` et `stacks: str = "[]"` (le JSON que le faux curl rend pour `/api/stacks`). Pour les cas avec fichier de clé, passer `PORTAINER_API_KEY=""` et `PORTAINER_ENV_FILE`.

```python
ON_THE_MINI = '[{"Id":50,"Name":"ea","EndpointId":7,"Status":1}]'
ON_THE_LAPTOP = '[{"Id":51,"Name":"ea","EndpointId":3,"Status":1}]'
ON_BOTH = ON_THE_MINI[:-1] + "," + ON_THE_LAPTOP[1:]


@needs_tools
def test_by_default_the_stack_is_driven_on_the_mac_mini(tmp_path: Path) -> None:
    done, calls, _ = _run_portainer(tmp_path, env=VERIFIED, stacks=ON_THE_MINI)

    assert done.returncode == 0, done.stderr
    assert any("/api/stacks/50?endpointId=7" in c for c in calls)
    assert not any("/api/endpoints" in c for c in calls)


@needs_tools
@pytest.mark.parametrize("command", ["down", "restart", "delete"])
@pytest.mark.parametrize("stacks", [ON_THE_LAPTOP, ON_BOTH])
def test_a_stack_declared_elsewhere_stops_the_script(
    tmp_path: Path, command: str, stacks: str
) -> None:
    done, calls, _ = _run_portainer(tmp_path, env=VERIFIED, command=command, stacks=stacks)

    assert done.returncode != 0
    assert "environnement 3" in done.stderr
    assert not any(f"-X {m} " in c for c in calls for m in ("POST", "PUT", "DELETE"))


@needs_tools
def test_the_environment_variable_overrides_the_target(tmp_path: Path) -> None:
    done, calls, _ = _run_portainer(
        tmp_path, env={**VERIFIED, "PORTAINER_ENDPOINT_ID": "3"}, stacks=ON_THE_LAPTOP
    )

    assert done.returncode == 0, done.stderr
    assert any("/api/stacks/51?endpointId=3" in c for c in calls)


@needs_tools
def test_a_shared_key_file_cannot_retarget_the_stack(tmp_path: Path) -> None:
    key_file = tmp_path / "portainer.env"
    key_file.write_text(f"PORTAINER_API_KEY={THE_KEY}\nPORTAINER_ENDPOINT_ID=3\n")
    done, calls, _ = _run_portainer(
        tmp_path,
        env={**VERIFIED, "PORTAINER_API_KEY": "", "PORTAINER_ENV_FILE": str(key_file)},
        stacks=ON_THE_MINI,
    )

    assert done.returncode == 0, done.stderr
    assert any("endpointId=7" in c for c in calls)
```

avec `VERIFIED = {"PORTAINER_URL": THE_URL, "PORTAINER_CA_CERT": "$CA"}`. `up` partage le même contrôle, placé avant le `case` final : pas de test séparé.

- [ ] **Step 2: les voir échouer**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q`
Expected: FAIL (cible `1` lue de `/endpoints`, aucun refus).

- [ ] **Step 3: modifier le script**

- Constante `DEFAULT_ENDPOINT_ID=7` ; `eid="${PORTAINER_ENDPOINT_ID:-$DEFAULT_ENDPOINT_ID}"`. Supprimer l'appel `GET /endpoints` et la lecture de `PORTAINER_ENDPOINT_ID` dans le fichier de clé.
- Après `GET /stacks` : lister les `EndpointId` des stacks `ea` différents de `$eid` ; s'il y en a, `die` avec « stack 'ea' déclarée sur l'environnement N, la cible est M » et la consigne `PORTAINER_ENDPOINT_ID=N … delete`. Sinon, choisir la stack `ea` d'`EndpointId == $eid`.
- Mettre à jour l'en-tête du script.
- `Makefile` : textes de `app-stack` et commentaire au-dessus de `PORTAINER_STACK` (mini, `PORTAINER_URL` obligatoire depuis un autre poste que celui de Portainer). `app-ps` et `app-logs` affichent d'abord « la stack tourne sur le Mac mini : cette cible lit le Docker local ; voir Portainer, environnement Mac-mini », et leur aide `##` le dit.

- [ ] **Step 4: vérifier**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q && ../scripts/portainer-stack.sh selftest`
Expected: tout PASS, `selftest ok`.

- [ ] **Step 5: commit** — `feat(deploy): target the Mac mini environment and refuse any other`

---

### Task 3: ADR, CLAUDE.md et PR

**Files:**
- Create: `docs/adr/0078-ea-tourne-sur-le-mac-mini.md` (gabarit `docs/adr/TEMPLATE.md`)
- Modify: `CLAUDE.md`

- [ ] **Step 1: écrire l'ADR** — statut `Proposition` ; contexte, décision, alternatives et sécurité repris de la spec ; remplace en partie 0027 (une origine derrière nginx demeure ; « aucun port publié » et « dépendances par `infra-net` » tombent). Ne pas modifier les ADR existants.
- [ ] **Step 2: corriger `CLAUDE.md`** — la ligne `deploy/ea.stack.yml` de l'arborescence et le paragraphe « Deployed, the app is one origin behind the Infra NGINX » (ports, hôtes LAN, environnement 7, `app-ps`/`app-logs`). Ne réécrire que ce que ce changement rend faux.
- [ ] **Step 3: la barrière qualité**

Run: `make check && git status --short`
Expected: code de sortie 0, aucun fichier modifié par la commande. Tout échec est rapporté avec sa sortie, y compris s'il préexiste sur `main`.

- [ ] **Step 4: commit** — `docs(adr): 0078, EA runs on the Mac mini` (avec ce plan)
- [ ] **Step 5: pousser et ouvrir une PR en brouillon** — après accord. `git push -u origin chore/deploy-mac-mini && gh pr create --draft`. Le corps dit : à merger avant `app-up` ; la PR Infra vient après le déploiement.

---

### Task 4: mise en service (chaque écriture avec accord)

**Files:**
- Modify (dépôt Infra, branche et PR séparées, à partir d'`origin/main` dans un worktree) : `nginx/conf.d/ea.conf`

- [ ] **Step 1: préflight, en lecture seule**
  - PostgreSQL du laptop : rôle et base `ea`, extension `vector`, `alembic_version`, et `SELECT count(*) FROM elements`. Passer par `GET /endpoints/3/docker/...` exige un `exec`, qui est une écriture d'API : préférer `psql` depuis ce Mac, hôte `192.168.2.10`, avec le mot de passe du coffre jamais affiché.
  - Bucket `ea-catalogue` présent.
  - Environnement Portainer 7 actif et réseau `infra-net` présent sur le mini (la stack l'exige) ; noter tout conteneur qui y tourne.
  - Ports libres sur `192.168.2.68` : `for p in 8300 8301; do nc -z -G3 192.168.2.68 $p && echo "$p PRIS" || echo "$p libre"; done`.
  - `~/Code/Infra/.openbao.env` lisible.
  - **Base absente ou vide : s'arrêter et en parler** (les données ont pu rester dans `infra_pgdata` du mini). Ne rien provisionner ni copier.
- [ ] **Step 2: sauvegarde, puis merge de la PR EA** (accord)
  Le merge touche `deploy/`, `scripts/` et `Makefile` : le workflow `Deploy` met en file un `up`, qui créerait la stack et migrerait la base dès que `runner-ea` répond. Dans l'ordre : `make pg-backup POSTGRES_HOST=192.168.2.10`, puis `gh workflow disable deploy.yml` et vérifier `gh run list --workflow deploy.yml` (aucun run en file), puis le merge (CI verte). Réactiver (`gh workflow enable deploy.yml`) seulement après l'étape 6, avec accord. L'accord de merge ne vaut pas accord de déploiement.
- [ ] **Step 3: déploiement** (accord)

```bash
PORTAINER_URL=https://portainer.infra.famillelallier.net \
PORTAINER_CA_CERT=$HOME/Code/Infra/certs/infra-ca.crt \
PORTAINER_ENV_FILE=$HOME/Code/Infra/.portainer.env \
INFRA_DIR=$HOME/Code/Infra make app-up
```

`deploy/ea.env` n'existe pas sur ce poste : le créer depuis l'exemple avec `EA_S3_ACCESS_KEY=ea` ; `app-env` y rend les deux secrets. Lire la sortie d'`overrides` : aucune ligne n'est attendue.

- [ ] **Step 4: vérifier sur le LAN**

Run: `curl -sS http://192.168.2.68:8301/health` et `curl -sS -o /dev/null -w '%{http_code}' http://192.168.2.68:8300/`
Expected: `"status":"ok"` et `200`. `degraded` ou aucune réponse : lire les logs du conteneur par Portainer et appliquer superpowers:systematic-debugging, sans passer à l'étape suivante.

- [ ] **Step 5: PR Infra** — remplacer les trois `http://ea-api:8000` par `http://192.168.2.68:8301` et `http://ea-web:80` par `http://192.168.2.68:8300` ; corriger le commentaire de tête. Rien d'autre. Lancer les vérifications que le dépôt Infra prévoit pour nginx. PR en brouillon ; merge sur accord (il redéploie la stack `infra`, donc coupe brièvement tout ce que ce nginx sert).
- [ ] **Step 6: vérifier par l'URL publique**

Run: `curl -sS --cacert ~/Code/Infra/certs/infra-ca.crt https://ea.infra.famillelallier.net/api/health` ; puis `/.well-known/oauth-protected-resource/api/mcp` (JSON, pas le HTML du SPA) et `/api/me` sans jeton (401). La connexion dans le navigateur est confirmée par l'utilisateur.

**Retour arrière :** `make app-delete CONFIRM=yes` (mêmes variables) et annulation de la PR Infra ; la sauvegarde de l'étape 2 couvre la base.
