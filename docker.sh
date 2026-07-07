#!/bin/bash

set -euo pipefail

IMAGE_NAME="${AIDER_BENCHMARK_IMAGE:-aider-polyglot-benchmark}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
RESULTS_DIR="${AIDER_BENCHMARK_RESULTS_DIR:-$REPO_ROOT/tmp.benchmarks}"
FORCE_BUILD=0
KEEP_CONTAINER=0
LOG_TO_FILE=1

while [[ $# -gt 0 ]]; do
       case "$1" in
              --build)
                     FORCE_BUILD=1
                     shift
                     ;;
              --keep)
                     KEEP_CONTAINER=1
                     shift
                     ;;
              --no-log-file)
                     LOG_TO_FILE=0
                     shift
                     ;;
              *)
                     break
                     ;;
       esac
done

mkdir -p "$RESULTS_DIR"

if [[ $FORCE_BUILD -eq 1 ]] || ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
       docker build \
              --file "$REPO_ROOT/Dockerfile" \
              -t "$IMAGE_NAME" \
              "$REPO_ROOT"
fi

env_args=()
if [[ -f "$REPO_ROOT/.env" ]]; then
       env_args+=(--env-file "$REPO_ROOT/.env")
fi

forward_vars=(
       GITHUB_API_KEY
       OPENROUTER_API_KEY
       OR_SITE_URL
       OR_APP_NAME
)

for name in $(compgen -e); do
       case "$name" in
              OPENAI_API_KEY|OPENAI_API_BASE|OPENAI_API_VERSION|ANTHROPIC_API_KEY|AZURE_API_KEY|AZURE_API_BASE|AZURE_API_VERSION|GITHUB_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|DEEPSEEK_API_KEY|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|AWS_REGION|AWS_DEFAULT_REGION|VERTEXAI_PROJECT|VERTEXAI_LOCATION|GOOGLE_APPLICATION_CREDENTIALS|LITELLM_*|MODEL|OPENAI_BASE_URL)
                     forward_vars+=("$name")
                     ;;
              *)
                     ;;
       esac
done

for name in "${forward_vars[@]}"; do
       env_args+=(-e "$name")
done

default_run_name() {
       date +"auto-%Y-%m-%d-%H-%M-%S"
}

container_cmd=(bash)
if [[ $# -gt 0 ]]; then
       if [[ "$1" == "--shell" ]]; then
              shift
              if [[ $# -gt 0 ]]; then
                     container_cmd=("$@")
              fi
       else
              if [[ "$1" == --* ]]; then
                     container_cmd=(python benchmark.py "$(default_run_name)" "$@")
              else
                     container_cmd=(python benchmark.py "$@")
              fi
       fi
fi

docker_args=(
       run
       -it
       --memory=12g
       --memory-swap=12g
       --add-host=host.docker.internal:host-gateway
       -v "$REPO_ROOT:/aider"
       -v "$RESULTS_DIR:/benchmarks"
)

if [[ $KEEP_CONTAINER -eq 0 ]]; then
       docker_args+=(--rm)
fi

echo "Docker image: $IMAGE_NAME"
echo "Results mount: $RESULTS_DIR -> /benchmarks"
echo "Container command: ${container_cmd[*]}"
echo "Streaming container logs until command exits..."

if [[ $LOG_TO_FILE -eq 1 ]]; then
       timestamp="$(date +"%Y-%m-%d-%H-%M-%S")"
       log_file="$RESULTS_DIR/docker-run-$timestamp.log"
       echo "Host log file: $log_file"
       docker "${docker_args[@]}" \
              "${env_args[@]}" \
              -e HISTFILE=/aider/.bash_history \
              -e PROMPT_COMMAND='history -a' \
              -e HISTCONTROL=ignoredups \
              -e HISTSIZE=10000 \
              -e HISTFILESIZE=20000 \
              -e AIDER_DOCKER=1 \
              -e AIDER_BENCHMARK_DIR=/benchmarks \
              -e PYTHONUNBUFFERED=1 \
              "$IMAGE_NAME" \
              "${container_cmd[@]}" 2>&1 | tee "$log_file"
       exit ${PIPESTATUS[0]}
fi

docker "${docker_args[@]}" \
       "${env_args[@]}" \
       -e HISTFILE=/aider/.bash_history \
       -e PROMPT_COMMAND='history -a' \
       -e HISTCONTROL=ignoredups \
       -e HISTSIZE=10000 \
       -e HISTFILESIZE=20000 \
       -e AIDER_DOCKER=1 \
       -e AIDER_BENCHMARK_DIR=/benchmarks \
       -e PYTHONUNBUFFERED=1 \
       "$IMAGE_NAME" \
       "${container_cmd[@]}"
