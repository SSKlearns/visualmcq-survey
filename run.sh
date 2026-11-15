#!/usr/bin/env bash
# Build and (re)start the annotation server on port 8015.
#   ./run.sh                  build + run (unpacks data/ from data_zip/ the first time)
#   VMCQ_SRC=/path/to/cvpr-27 REPREPARE=1 ./run.sh   rebuild data/ from the cvpr-27 banks instead
#   VMCQ_PREFIX=/other-path ./run.sh   URL path it is served under (default /visualmcq-survey)
#   NOTA=1 REPREPARE=1 ./run.sh   switch to the *_nota banks (shows "None of the above")
# Responses are kept in ./state/responses.sqlite on the host; rebuilding or restarting never deletes them.
set -euo pipefail
cd "$(dirname "$0")"

NAME=vmcq-human-eval
PORT=${PORT:-8015}
SRC=${VMCQ_SRC:-../cvpr-27}
PREFIX=${VMCQ_PREFIX-/visualmcq-survey}   # URL path behind the reverse proxy; VMCQ_PREFIX= ./run.sh to serve at /

if [ "${REPREPARE:-0}" = 1 ] || [ -n "${NOTA:-}" ]; then
  echo "== preparing data/ from $SRC"
  python3 prepare_data.py --src "$SRC" --out data ${NOTA:+--nota}
elif [ ! -f data/questions.jsonl ]; then
  if ls data_zip/data.zip.part-* >/dev/null 2>&1; then
    echo "== unpacking data/ from data_zip/"
    cat data_zip/data.zip.part-* > data.zip
    if command -v sha256sum >/dev/null &&
       [ "$(sha256sum data.zip | cut -d' ' -f1)" != "$(cut -d' ' -f1 data_zip/data.zip.sha256)" ]; then
      echo "data.zip checksum mismatch: data_zip/ is incomplete (re-clone or git pull)"; rm -f data.zip; exit 1
    fi
    if command -v unzip >/dev/null; then unzip -q -o data.zip; else python3 -m zipfile -e data.zip .; fi
    rm -f data.zip
  else
    echo "== preparing data/ from $SRC"
    python3 prepare_data.py --src "$SRC" --out data
  fi
fi

DOCKER=docker
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

PROXY_ARGS=()
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  if [ -n "${!v:-}" ]; then PROXY_ARGS+=(--build-arg "$v=${!v}"); fi
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
  -e VMCQ_PREFIX="$PREFIX" \
  --user "$(id -u):$(id -g)" \
  "$NAME" >/dev/null

for i in $(seq 1 20); do
  if curl -fsS --noproxy '*' "http://localhost:$PORT/healthz" >/dev/null 2>&1; then
    echo "== up: http://$(hostname -f 2>/dev/null || hostname):$PORT$PREFIX/   (logs: $DOCKER logs -f $NAME)"
    exit 0
  fi
  sleep 1
done
echo "== container started but /healthz did not answer yet; check: $DOCKER logs $NAME"
