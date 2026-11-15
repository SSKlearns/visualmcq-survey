#!/usr/bin/env bash
# Build and (re)start the annotation server on port 8015.
#   ./run.sh                  build + run (prepares data/ from ../cvpr-27 the first time)
#   VMCQ_SRC=/path/to/cvpr-27 ./run.sh
#   NOTA=1 REPREPARE=1 ./run.sh   switch to the *_nota banks (shows "None of the above")
# Responses are kept in ./state/responses.sqlite on the host; rebuilding or restarting never deletes them.
set -euo pipefail
cd "$(dirname "$0")"

NAME=vmcq-human-eval
PORT=${PORT:-8015}
SRC=${VMCQ_SRC:-../cvpr-27}

if [ ! -f data/questions.jsonl ] || [ "${REPREPARE:-0}" = 1 ]; then
  echo "== preparing data/ from $SRC"
  python3 prepare_data.py --src "$SRC" --out data ${NOTA:+--nota}
fi

DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

PROXY_ARGS=()
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  [ -n "${!v:-}" ] && PROXY_ARGS+=(--build-arg "$v=${!v}")
done

echo "== building image $NAME"
$DOCKER build ${PROXY_ARGS[@]+"${PROXY_ARGS[@]}"} -t "$NAME" .

mkdir -p state
$DOCKER rm -f "$NAME" >/dev/null 2>&1 || true
echo "== starting container $NAME"
$DOCKER run -d --name "$NAME" --restart unless-stopped \
  -p "$PORT:8015" \
  -v "$PWD/state:/app/state" \
  -v "$PWD/config:/app/config:ro" \
  --user "$(id -u):$(id -g)" \
  "$NAME" >/dev/null

for i in $(seq 1 20); do
  if curl -fsS --noproxy '*' "http://localhost:$PORT/healthz" >/dev/null 2>&1; then
    echo "== up: http://$(hostname -f 2>/dev/null || hostname):$PORT   (logs: $DOCKER logs -f $NAME)"
    exit 0
  fi
  sleep 1
done
echo "== container started but /healthz did not answer yet; check: $DOCKER logs $NAME"
