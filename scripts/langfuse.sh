#!/usr/bin/env bash
# Self-hosted Langfuse, the tracing UI for benchmark runs.
#
#   scripts/langfuse.sh up      # first time: install, create the account, start; later: start
#   scripts/langfuse.sh login   # print the sign-in details
#   scripts/langfuse.sh down    # stop it; traces stay in Docker volumes
#
# Langfuse lives outside this repository, in $LANGFUSE_HOME (default
# ~/dev/tools/langfuse). `up` writes the project's API keys into this
# repository's .env, so runs are traced from then on. Needs Docker running,
# git and openssl.

set -euo pipefail
cd "$(dirname "$0")/.."

LANGFUSE_HOME=${LANGFUSE_HOME:-$HOME/dev/tools/langfuse}
LANGFUSE_URL=http://localhost:3000
ENV_FILE=$LANGFUSE_HOME/.env

# The value of a KEY=value line in the Langfuse .env file.
setting() {
  sed -n "s/^$1=//p" "$ENV_FILE"
}

# Write KEY=value into this repository's .env, replacing any existing line.
set_repo_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

compose() {
  (cd "$LANGFUSE_HOME" && docker compose "$@")
}

install() {
  if [ ! -d "$LANGFUSE_HOME" ]; then
    echo "Cloning Langfuse into $LANGFUSE_HOME..."
    git clone --quiet --depth 1 https://github.com/langfuse/langfuse.git "$LANGFUSE_HOME"
  fi
  if [ -f "$ENV_FILE" ]; then
    return
  fi
  echo "Writing $ENV_FILE with generated secrets and the first account..."
  local email name minio postgres
  email=${LANGFUSE_EMAIL:-$(git config user.email)}
  name=${LANGFUSE_NAME:-$(git config user.name)}
  # MinIO's root password doubles as the S3 secret the app signs in with.
  minio=$(openssl rand -hex 12)
  postgres=$(openssl rand -hex 12)
  # Langfuse's compose file reads these. The LANGFUSE_INIT_* lines create the
  # organisation, project, API keys and user on the first start (its
  # "headless initialisation"), so nobody fills in a sign-up form. Values
  # must not be quoted.
  cat > "$ENV_FILE" <<EOF
SALT=$(openssl rand -hex 16)
ENCRYPTION_KEY=$(openssl rand -hex 32)
NEXTAUTH_SECRET=$(openssl rand -hex 24)
POSTGRES_PASSWORD=$postgres
DATABASE_URL=postgresql://postgres:$postgres@postgres:5432/postgres
CLICKHOUSE_PASSWORD=$(openssl rand -hex 12)
REDIS_AUTH=$(openssl rand -hex 12)
MINIO_ROOT_PASSWORD=$minio
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=$minio
LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=$minio
LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=$minio
LANGFUSE_INIT_ORG_ID=harness-rl
LANGFUSE_INIT_ORG_NAME=harness-rl
LANGFUSE_INIT_PROJECT_ID=mas-optimiser
LANGFUSE_INIT_PROJECT_NAME=mas-optimiser
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-$(openssl rand -hex 16)
LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-$(openssl rand -hex 16)
LANGFUSE_INIT_USER_EMAIL=$email
LANGFUSE_INIT_USER_NAME=$name
LANGFUSE_INIT_USER_PASSWORD=$(openssl rand -base64 15 | tr -d '/+=')
EOF
  chmod 600 "$ENV_FILE"
}

up() {
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running; start Docker Desktop and try again." >&2
    exit 1
  fi
  install
  echo "Starting Langfuse (the first start pulls its images and takes a few minutes)..."
  compose up -d --quiet-pull
  printf 'Waiting for %s' "$LANGFUSE_URL"
  for _ in $(seq 1 90); do
    if curl -sf "$LANGFUSE_URL/api/public/health" >/dev/null 2>&1; then
      break
    fi
    printf '.'
    sleep 5
  done
  echo
  if ! curl -sf "$LANGFUSE_URL/api/public/health" >/dev/null 2>&1; then
    echo "ERROR: Langfuse is not answering; check: (cd $LANGFUSE_HOME && docker compose logs)" >&2
    exit 1
  fi
  [ -f .env ] || cp .env.example .env
  set_repo_env LANGFUSE_PUBLIC_KEY "$(setting LANGFUSE_INIT_PROJECT_PUBLIC_KEY)"
  set_repo_env LANGFUSE_SECRET_KEY "$(setting LANGFUSE_INIT_PROJECT_SECRET_KEY)"
  set_repo_env LANGFUSE_BASE_URL "$LANGFUSE_URL"
  echo "Project keys written to .env; runs are traced from now on."
  login
}

login() {
  echo "Sign in at $LANGFUSE_URL"
  echo "  email:    $(setting LANGFUSE_INIT_USER_EMAIL)"
  echo "  password: $(setting LANGFUSE_INIT_USER_PASSWORD)"
}

case "${1:-}" in
  up) up ;;
  login) login ;;
  down) compose down ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
