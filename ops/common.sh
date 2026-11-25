# Sourced by ops/*.sh. Loads ../.env and defines `remote` (run a command on the LoGaRT host).
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
fi
: "${LOGART_SSH_HOST:?set LOGART_SSH_HOST in .env (see .env.example)}"
SSH_OPTS=(-o BatchMode=yes -p "${LOGART_SSH_PORT:-22}")
[ -n "${LOGART_SSH_KEY:-}" ] && SSH_OPTS+=(-i "$LOGART_SSH_KEY")

SCP_OPTS=(-q -o BatchMode=yes -P "${LOGART_SSH_PORT:-22}")
[ -n "${LOGART_SSH_KEY:-}" ] && SCP_OPTS+=(-i "$LOGART_SSH_KEY")

remote() { ssh "${SSH_OPTS[@]}" "$LOGART_SSH_HOST" "$@"; }
upload() { scp "${SCP_OPTS[@]}" "$1" "$LOGART_SSH_HOST:$2"; }

# Files this repository owns on the server:  <repo path>|<installed path>
MANAGED_FILES=(
  "manticore/manticore.conf|/etc/manticoresearch/manticore.conf"
  "manticore/logart_tsv.py|/etc/manticoresearch/logart_tsv.py"
  "mysql/zz-federated.cnf|/etc/mysql/mysql.conf.d/zz-federated.cnf"
  "systemd/flush-federated.conf|/etc/systemd/system/manticore.service.d/flush-federated.conf"
  "cron/logart-mt-flush|/etc/cron.d/logart-mt-flush"
)
