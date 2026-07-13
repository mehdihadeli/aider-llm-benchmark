# Aider benchmark harness

Fork of Aider's benchmark harness from [https://github.com/Aider-AI/aider/tree/main/benchmark](https://github.com/Aider-AI/aider/tree/main/benchmark), adapted to make it simpler to run, add some new features, and support integration tests for [Exercism-style](https://github.com/exercism/csharp) coding tasks.
This repo does not require cloning the full Aider source tree. It uses the published `aider` Python package plus the local benchmark scripts and locally cloned Exercism tracks.

## Table of contents

- [Aider benchmark harness](#aider-benchmark-harness)
  - [Table of contents](#table-of-contents)
  - [Repo layout](#repo-layout)
  - [Original documentation](#original-documentation)
  - [Background](#background)
  - [Included dataset](#included-dataset)
  - [Usage](#usage)
    - [Concurrency model](#concurrency-model)
    - [Setup for benchmarking](#setup-for-benchmarking)
    - [Clone Exercism tracks](#clone-exercism-tracks)
    - [Model provider setup](#model-provider-setup)
    - [Concurrency parameter guide](#concurrency-parameter-guide)
    - [Recommended starting profiles](#recommended-starting-profiles)
    - [Real-world guidance](#real-world-guidance)
    - [Tuning workflow](#tuning-workflow)
      - [GitHub Models](#github-models)
      - [GitHub Copilot (LiteLLM `github_copilot/...`)](#github-copilot-litellm-github_copilot)
      - [OpenRouter](#openrouter)
    - [Run in Docker](#run-in-docker)
    - [Run locally](#run-locally)
    - [Benchmark report](#benchmark-report)
    - [Clean benchmark temp files](#clean-benchmark-temp-files)
    - [Generate stats for a specific benchmarking directory](#generate-stats-for-a-specific-benchmarking-directory)
    - [Testing](#testing)
  - [Credits](#credits)

## Repo layout

This repo is intentionally trimmed to the files needed for the current benchmark workflow:

- implementation source lives under [src/aider_polyglot_benchmark](src/aider_polyglot_benchmark).
- installed console entry points expose the CLI surfaces: `benchmark`, `clone-exercism-tracks`, `cleanup-exercism-tracks`, and `leaderboard-report`.
- [docker.sh](docker.sh) and [Dockerfile](Dockerfile) provide the recommended sandboxed execution path for the bundled C# track.
- `exercises/` is the default ignored destination for cloned Exercism language tracks.

Legacy upstream helper scripts that were not part of the documented Docker or local benchmark flow have been removed.

## Original documentation

This repo is a standalone harness, but the underlying model/runtime behavior comes from upstream projects.
Use these original docs as the source of truth when local README guidance and upstream behavior diverge:

- [Aider documentation](https://aider.chat/docs/)
- [Aider usage guide](https://aider.chat/docs/usage.html)
- [Aider LLM/provider docs](https://aider.chat/docs/llms.html)
- [Aider configuration and API key docs](https://aider.chat/docs/config.html)
- [Aider advanced model settings](https://aider.chat/docs/config/adv-model-settings.html)
- [LiteLLM provider docs](https://docs.litellm.ai/docs/providers)
- [LiteLLM supported model/provider overview](https://docs.litellm.ai/docs/completion/supported)
- [Original polyglot benchmark writeup](https://aider.chat/2024/12/21/polyglot.html)
- [Original polyglot benchmark dataset repo](https://github.com/Aider-AI/polyglot-benchmark)

## Background

The benchmark is based on [Exercism](https://github.com/exercism) coding exercises.
It measures whether an LLM, working through Aider's editing flow, can modify starter files so
the exercise tests pass.

See [this writeup for a longer discussion about the benchmark](https://aider.chat/2024/12/21/polyglot.html).

The benchmark is intended to be run _inside a docker container_.
This is because the benchmarking harness will be
taking code written by an LLM
and executing it without any human review or supervision!
The LLM could generate dangerous code that harms your system.
Running inside a docker container helps limit the damage that could be done.

## Included dataset

The default workflow does not keep committed copies of Exercism language tracks in this repo.
Instead, clone the language tracks you want into the ignored `exercises/` folder and let
[src/aider_polyglot_benchmark/benchmark.py](src/aider_polyglot_benchmark/benchmark.py) discover exercises from each track's `exercises/practice` directory.

For example:

```text
exercises/
  csharp/
    exercises/
      practice/
  java/
    exercises/
      practice/
```

This repo also does not include the official SWE-bench evaluation harness; it focuses on the local Exercism-style benchmark flow driven by the `benchmark` console script.

## Usage

There are 3 main tasks involved in benchmarking:

1. Install and setup for benchmarking.

2. Run the benchmark to measure performance across all the exercises.

3. Generate a summary report of how many of the exercises succeeded or failed.

You can do this in either of these ways:

- run multiple models in one `benchmark` command and let it emit aggregate reports at the end
- run each model separately at different times, then rebuild the final aggregate report later from the previously completed runs

When you pass multiple `--model` flags, the benchmark can now also run those model batches in parallel with `--model-parallelism`.
Use that together with `--max-llm-concurrency` to keep request bursts below provider quotas.

### Concurrency model

The benchmark now has 3 separate concurrency controls, and they affect different layers of the run:

- `--threads`: how many exercise test cases can be active at once inside one model run.
- `--model-parallelism`: how many selected models can run at once.
- `--max-llm-concurrency`: how many in-flight LLM requests are allowed at once per provider scope such as `github`, `openai`, or `anthropic`.

Report generation is now separated from those concurrency knobs:

- `--report-mode auto` is the default and resolves to end-only report generation for parallel runs
- that means worker threads write only their own `.aider.results.json` files while the run is active
- `--report-mode end` forces that same end-only behavior even if the run is otherwise serial
- each model writes its `benchmark-report.yml` once when that model finishes
- the batch writes root aggregate reports once when all models finish
- `--report-mode live` forces live report refresh during the run

These are intentionally separate because active test cases are not the same thing as live API pressure.
You can keep many exercises moving in parallel while still limiting real provider traffic.

In rough terms:

- maximum active test cases is approximately `threads * model-parallelism`
- maximum live LLM calls per provider is capped separately by `--max-llm-concurrency`

Default behavior is conservative:

- if `--threads 1`, default LLM concurrency is `1`
- if `--threads > 1`, default LLM concurrency is `2`
- if `--model-parallelism` is omitted, only `1` model run is active at a time
- if any parallelism is active through `--threads`, `--model-parallelism`, or multiple `--model` flags, default report mode is end-only
- if the run is fully serial, default report mode is live

### Setup for benchmarking

Docker is the recommended default for all benchmark runs. Local execution is only for debugging
or quick smoke tests when you accept the risk of running untrusted model-generated code on your
host machine.

This README assumes Git Bash on Windows for local shell commands.

Prerequisites:

- install `uv` first if it is not already available in your shell
- use Python `3.11` or newer; the recommended local setup below uses Python `3.12`
- Docker in this repo currently uses Python `3.11`, which is also supported by the package metadata

If your shell was previously using another repo's virtual environment, clear it first to avoid
`uv` warnings about `VIRTUAL_ENV` not matching this project's `.venv`:

```bash
deactivate 2>/dev/null || true
unset VIRTUAL_ENV
```

Provision a compatible Python environment and install dependencies once:

```bash
uv python install 3.12
uv sync --python 3.12
```

This installs the published `aider-chat` package from PyPI plus the harness dependencies declared
in [pyproject.toml](pyproject.toml).

Activate the local `.venv` before running `benchmark` directly:

```bash
source .venv/Scripts/activate
```

On Windows Git Bash, the installed entry points are `.exe` launchers inside `.venv/Scripts`.
That means the most portable invocation is still `uv run ...`, even after activation. If you
want to call the entry points directly, use `benchmark.exe`, `clone-exercism-tracks.exe`,
`cleanup-exercism-tracks.exe`, and `leaderboard-report.exe`.

### Clone Exercism tracks

Clone only the language tracks you want to benchmark from [github.com/exercism](https://github.com/exercism):

```bash
uv run clone-exercism-tracks csharp java go python
```

If you omit the language names, the script prompts for a comma-separated list:

```bash
uv run clone-exercism-tracks
```

This clones each selected track into `exercises/<language>`, which already matches the
folder shape expected by `benchmark`: `<root>/<language>/exercises/practice`.

To remove cloned tracks you no longer want locally:

```bash
uv run cleanup-exercism-tracks csharp java go python
```

If you run `benchmark` with `--languages` and one of those language tracks is
missing under the exercises root, the benchmark now attempts to clone that missing track
automatically into the exercises root before scanning `exercises/practice`.

The downloaded tracks live in the ignored `exercises/` directory and should never be committed.

If you want to refresh already cloned tracks later:

```bash
uv run clone-exercism-tracks csharp java --update-existing
```

Run local commands through `uv run`:

```bash
uv run benchmark --help
```

By default, `benchmark` reads exercises from `exercises/`. You can still override this with `--exercises-dir` if your track clones live elsewhere. The resolver also tolerates the older `exercism-tracks/` location for backward compatibility.

For example, if `exercism-tracks/java` is missing, this command will try to clone the Java track
implicitly before starting the benchmark:

```bash
uv run benchmark my-run --languages java --model github/gpt-4.1 --unsafe
```

### Model provider setup

Recent Aider releases route model calls through LiteLLM. This harness accepts any
LiteLLM-supported model name that Aider can use, including providers such as OpenAI,
Anthropic, Azure OpenAI, GitHub Models, Google Gemini, DeepSeek, OpenRouter, and
GitHub Copilot.

For official provider syntax, credential rules, and provider-specific caveats, see:

- [Aider LLM docs](https://aider.chat/docs/llms.html)
- [Aider API key and config docs](https://aider.chat/docs/config/api-keys.html)
- [LiteLLM provider docs](https://docs.litellm.ai/docs/providers)

Pass the model name directly with `--model`, for example:

```bash
uv run benchmark my-run --model openai/gpt-4o --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model anthropic/claude-sonnet-4 --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model azure/my-gpt-4o-deployment --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model github/gpt-4.1 --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model gemini/gemini-2.5-flash --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model deepseek/deepseek-chat --languages csharp --num-tests 3 --unsafe
uv run benchmark my-run --model github_copilot/gpt-4 --languages csharp --num-tests 3 --unsafe
```

If you want model-level parallelism, repeat `--model` and add `--model-parallelism`.
For example, this runs two providers at once while still limiting each provider scope to one in-flight LLM request at a time:

```bash
uv run benchmark my-run \
  --model github/gpt-4.1 \
  --model openai/gpt-4o \
  --model-parallelism 2 \
  --threads 4 \
  --max-llm-concurrency 1 \
  --languages csharp \
  --unsafe
```

### Concurrency parameter guide

Use these flags together:

- `--threads N`: exercise-level parallelism inside one model run.
- `--model-parallelism N`: model-level parallelism across repeated `--model` values.
- `--max-llm-concurrency N`: provider-scoped request cap. This is usually the most important rate-limit safety knob.
- `--report-mode auto|end|live`: report refresh strategy. `auto` is the default and chooses `end` for parallel runs and `live` for serial runs.
- `--rate-limit-retries N`: how many times to retry after a detected rate-limit response.
- `--rate-limit-backoff-initial SECONDS`: initial cooldown after a detected rate-limit response.
- `--rate-limit-backoff-max SECONDS`: maximum cooldown after repeated rate-limit responses.

How to think about them:

- raise `--threads` when local file preparation and test execution are the bottleneck
- raise `--model-parallelism` when you want multiple models to progress at once
- raise `--max-llm-concurrency` only when your provider quotas are known to support it
- keep `--report-mode auto` or force `--report-mode end` when you want the lowest shared-file contention during parallel runs
- lower `--max-llm-concurrency` before lowering `--threads` if you see `429` errors

### Recommended starting profiles

If you do not know your provider quotas yet, start with one of these profiles.

Safe single-provider profile:

```bash
uv run benchmark my-run \
  --model github/gpt-4o \
  --model github/gpt-4.1 \
  --languages csharp \
  --threads 4 \
  --model-parallelism 2 \
  --max-llm-concurrency 1 \
  --rate-limit-retries 4 \
  --rate-limit-backoff-initial 5 \
  --rate-limit-backoff-max 60 \
  --unsafe
```

Balanced mixed-provider profile:

```bash
uv run benchmark my-run \
  --model github/gpt-4.1 \
  --model openai/gpt-4o \
  --model anthropic/claude-sonnet-4 \
  --languages csharp \
  --threads 6 \
  --model-parallelism 3 \
  --max-llm-concurrency 2 \
  --rate-limit-retries 4 \
  --rate-limit-backoff-initial 5 \
  --rate-limit-backoff-max 60 \
  --unsafe
```

Very conservative profile for debugging or unknown quotas:

```bash
uv run benchmark my-run \
  --model github/gpt-4.1 \
  --languages csharp \
  --threads 2 \
  --model-parallelism 1 \
  --max-llm-concurrency 1 \
  --rate-limit-retries 6 \
  --rate-limit-backoff-initial 10 \
  --rate-limit-backoff-max 90 \
  --unsafe
```

### Real-world guidance

There is no single safe concurrency number that works for every provider and account tier.
The practical limit depends on:

- provider quota policy
- account or org tier
- model family
- request size and latency

Good starting assumptions in practice:

- unknown quota: `--max-llm-concurrency 1`
- known healthy quota: try `--max-llm-concurrency 2`
- higher than `2`: only after a stable run with no `429` responses

For same-provider model batches, start with:

- `--threads 4`
- `--model-parallelism 2`
- `--max-llm-concurrency 1`

For mixed-provider batches, start with:

- `--threads 4` or `6`
- `--model-parallelism 2` or `3`
- `--max-llm-concurrency 1` or `2`

Because different providers usually have separate quota buckets, mixed-provider runs can often tolerate more overall throughput than many models stacked on one provider.

### Tuning workflow

Use a staged ramp-up instead of jumping straight to high concurrency:

1. Start with `--max-llm-concurrency 1`.
2. Run a small batch such as `--num-tests 20` to `50`.
3. If no `429` responses appear, raise `--max-llm-concurrency` to `2`.
4. Only after that is stable, consider raising `--model-parallelism` or `--threads`.
5. If rate limits appear, lower `--max-llm-concurrency` first.

This order matters because the limiter sits closer to the true provider bottleneck than exercise threads do.

If both models use the same provider, start more conservatively:

```bash
uv run benchmark my-run \
  --model github/gpt-4o \
  --model github/gpt-4.1 \
  --model-parallelism 2 \
  --threads 4 \
  --max-llm-concurrency 1 \
  --rate-limit-retries 6 \
  --rate-limit-backoff-initial 5 \
  --rate-limit-backoff-max 60 \
  --languages csharp \
  --unsafe
```

Practical tuning rules:

- `--threads` controls how many exercises are worked on in parallel inside one model run.
- `--model-parallelism` controls how many selected models run at once.
- `--max-llm-concurrency` caps concurrent LLM calls per provider scope such as `github`, `openai`, or `anthropic`.
- Active test cases can exceed live provider calls because the limiter applies separately from exercise scheduling.
- For mixed providers, try `--model-parallelism 2` or `3` first.
- For multiple models on one provider, keep `--max-llm-concurrency 1` first, then raise slowly only if you see no `429` errors.
- If rate limits still happen, lower `--max-llm-concurrency` before lowering `--threads`, because the limiter is closer to the real bottleneck.

If you prefer, set `MODEL=...` in your shell or `.env` and omit `--model`.

Set the corresponding provider credentials in your shell or `.env`. `docker.sh` loads the full
`.env` file automatically and also forwards common LiteLLM provider variables from the current shell.

If a model needs custom Aider metadata or non-default request options, see
[Aider advanced model settings](https://aider.chat/docs/config/adv-model-settings.html).

#### GitHub Models

For GitHub Models, use LiteLLM's native `github/...` model prefix.

Set the GitHub API key before running the benchmark in your current Git Bash session:

```bash
export GITHUB_API_KEY=<github_models_token>
```

If you want the value to persist, put `GITHUB_API_KEY=...` in `.env` at the repo root or add the export to your shell profile. `benchmark` loads that `.env` before it initializes Aider/LiteLLM, so GitHub Models credentials are available automatically. Then pass a GitHub model name directly, for example:

LiteLLM GitHub provider docs:

- [LiteLLM GitHub provider](https://docs.litellm.ai/docs/providers/github)

```bash
uv run benchmark github-models-smoke --model github/gpt-4o --languages csharp --num-tests 3 --unsafe
```

GitHub Models smoke sample with multiple models:

```bash
uv run benchmark github-models-smoke --model github/gpt-4.1 --model github/gpt-4o --languages csharp --num-tests 1 --threads 1 --unsafe
```

You can benchmark multiple GitHub Models in one run by repeating `--model`. The harness will run each model into its own result directory, then write aggregate leaderboard files automatically when the batch finishes:

```bash
uv run benchmark github-models-compare --model github/gpt-4o --model github/gpt-4.1 --languages csharp --num-tests 5 --unsafe
```

The exact command used for the sample comparison in this repo session was:

```bash
uv run benchmark github-multi-sample --model github/gpt-4o --model github/gpt-4.1 --languages csharp --threads 1 --num-tests 5 --unsafe
```

You only need `GITHUB_API_KEY` for the standard GitHub Models flow in this repo.

#### GitHub Copilot (LiteLLM `github_copilot/...`)

For GitHub Copilot provider routing through LiteLLM, use model names with the
`github_copilot/` prefix and repeat `--model` for batch runs. Example:

GitHub Copilot provider docs:

- [LiteLLM GitHub Copilot provider](https://docs.litellm.ai/docs/providers/github_copilot)
- [LiteLLM GitHub Copilot provider — device code auth](https://docs.litellm.ai/docs/providers/github_copilot?ref=dsebastien.net)

No API key is required: LiteLLM authenticates to GitHub Copilot through an
OAuth device-code flow. On first use the CLI prints a device code and asks you
to open <https://github.com/login/device>, enter the code, and authorize the
request. After authorization, credentials are cached locally and reused for
future runs.

If you omit the leading run name, `benchmark` derives one from the selected model
names. For example, this uses a base run name like `gpt-4_gpt-4.1_kimi`:

```bash
uv run benchmark --model github_copilot/gpt-4   --model github_copilot/kimi --languages csharp --num-tests 3 --unsafe
```

#### OpenRouter

For OpenRouter, use LiteLLM's `openrouter/...` model prefix.

OpenRouter provider docs:

- [LiteLLM OpenRouter provider](https://docs.litellm.ai/docs/providers/openrouter)

Set the OpenRouter API key before running the benchmark in your current Git Bash session:

```bash
export OPENROUTER_API_KEY=<openrouter_api_key>
```

Optional OpenRouter headers for attribution and analytics:

```bash
export OR_SITE_URL=https://your-site.example
export OR_APP_NAME=aider-polyglot-benchmark
```

You can also place these in `.env` if you want them loaded automatically. Then pass an OpenRouter model name directly, for example:

```bash
uv run benchmark openrouter-smoke --model openrouter/openai/gpt-4o --languages csharp --num-tests 3 --unsafe
```

OpenRouter multi-model sample:

```bash
uv run benchmark openrouter-model-smoke --model openrouter/openai/gpt-3.5-turbo --model openrouter/openai/gpt-4 --languages csharp --num-tests 3 --threads 1 --unsafe
```

The same benchmark flow works for other LiteLLM-supported providers once the required model name
and credentials are present.

### Run in Docker

`docker.sh` is the one-command Docker entrypoint. It will:

- build the `aider-polyglot-benchmark` image automatically if it does not exist yet
- rebuild the image first when you pass `--build`
- keep the container after exit when you pass `--keep`
- save a copy of live container output under `tmp.benchmarks/docker-run-*.log` unless you pass `--no-log-file`
- load secrets from `.env` when present
- forward the supported provider variables from your current shell
- mount the repo at `/aider`
- mount benchmark outputs to `tmp.benchmarks` on the host through `/benchmarks` in the container
- print the host-to-container results mount before startup
- stream container stdout and stderr live until the benchmark exits

For the upstream container guidance behind this workflow, see
[Aider install docs](https://aider.chat/docs/install.html) and
[Aider Docker docs](https://aider.chat/docs/install/docker.html).

Run a benchmark directly from the host shell:

```bash
./docker.sh a-helpful-name-for-this-run --model github/gpt-4o --languages csharp --threads 10 --exercises-dir exercises

# Or use OpenRouter
./docker.sh a-helpful-name-for-this-run --model openrouter/openai/gpt-4o --languages csharp --threads 10 --exercises-dir exercises

# Or omit the run name and let docker.sh generate one
./docker.sh --model github/gpt-4o --languages csharp --num-tests 3

# Keep container after exit for inspection
./docker.sh --keep github-models-smoke --model github/gpt-4o --languages csharp --num-tests 1 --threads 1 --unsafe

# Stream logs live but skip writing the host log file
./docker.sh --no-log-file github-models-smoke --model github/gpt-4o --languages csharp --num-tests 1 --threads 1 --unsafe
```

Inside the container, the same repeated-`--model` pattern works if you want one command to benchmark several models and then emit aggregate reports:

```bash
./docker.sh github-models-compare --model github/gpt-4o --model github/gpt-4.1 --languages csharp --num-tests 5
```

Open an interactive shell in the container when needed:

```bash
./docker.sh --shell

# Force a rebuild before opening the shell
./docker.sh --build --shell
```

`docker.sh` forwards common LiteLLM-related environment variables when they are defined in `.env`
or your current shell. This includes `MODEL`, `LITELLM_*`, and common provider credentials such as
OpenAI, Anthropic, Azure OpenAI, GitHub Models, Gemini, DeepSeek, AWS/Bedrock-style variables,
Vertex AI, and OpenRouter attribution headers.

The above creates results under `tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--a-helpful-name-for-this-run` on the host.
Because `tmp.benchmarks` is bind-mounted into the container as `/benchmarks`, you can inspect files on the host
while the benchmark is still running, and the wrapper now prints that exact mount path before startup.
By default, the same live output is also copied to `tmp.benchmarks/docker-run-*.log` on the host.
Run like this, the harness will execute the selected exercises in a random order inside the container.
If you omit the leading run name, `docker.sh` generates one like `auto-2026-07-04-18-30-12`.

The Docker image is built from the local [Dockerfile](Dockerfile). The image name is only a local tag, not a remote dependency. The Dockerfile is intentionally scoped to the Python-based harness and bundled `csharp` track, so it installs Python 3.11 and the .NET 10 SDK required by the current Exercism C# projects.

### Run locally

Local runs execute untrusted model-generated code. Use Docker when possible.
If you accept the risk for local testing, pass `--unsafe` explicitly.

```bash
uv run benchmark my-run --model github/gpt-4o --languages csharp --threads 1 --num-tests 3 --unsafe

# Or use OpenRouter
uv run benchmark my-run --model openrouter/openai/gpt-4o --languages csharp --threads 1 --num-tests 3 --unsafe

# Or benchmark multiple models and auto-generate aggregate reports
uv run benchmark my-compare --model github/gpt-4o --model github/gpt-4.1 --languages csharp --threads 1 --num-tests 5 --unsafe
```

If you prefer not to depend on a manually activated `.venv`, prefix with `uv run` (e.g. `uv run benchmark ...`).

For upstream Aider CLI behavior that this harness builds on, see:

- [Aider usage guide](https://aider.chat/docs/usage.html)
- [Aider options reference](https://aider.chat/docs/config/options.html)
- [Aider model settings](https://aider.chat/docs/config/adv-model-settings.html)

Useful flags:

- `--languages csharp` is required for benchmark runs and limits the run to that cloned track.
- `--keywords two-fer` runs only matching exercise names.
- `--num-tests 5` is good for smoke testing setup and now stages only those selected exercises into `tmp.benchmarks`.
- `--read-model-settings path/to/settings.yml` loads custom Aider model settings.

During a run, press **Ctrl+C** once to cancel. The harness stops the current
benchmark gracefully, summarizes completed exercises, and still writes the
aggregate leaderboard files (`leaderboard.csv`, `leaderboard.html`,
`polyglot_leaderboard.yml`) so partial results are preserved. When benchmarking
multiple models in parallel, the cancellation applies to the running models and
skips any models that have not started yet.

You can run `benchmark --help` or `uv run benchmark --help`. The help output now includes common workflows such as running a benchmark, comparing runs, collecting stats, and purging generated outputs. The most useful arguments are:

- `--model` is the name of the model, same as you would pass directly to `aider`. Repeat it to benchmark multiple models in one command.
- `--edit-format` is the name of the edit format, same as you would pass directly to `aider`. When working with an experimental LLM, I recommend starting with `whole`
- `--threads` specifies how many exercises to benchmark in parallel. Start with a single thread if you are working out the kinks on your benchmarking setup or working with a new model. Once you are getting reliable results, you can speed up the process by running with more threads.
- `--num-tests` specifies how many of the tests to run before stopping. This is another way to start gently as you debug your benchmarking setup.
- `--keywords` filters the tests to run to only the ones whose name match the supplied argument (similar to `pytest -k xxxx`).
- `--read-model-settings=<filename.yml>` specify model settings, see [Aider model settings](https://aider.chat/docs/config/adv-model-settings.html#model-settings).

### Benchmark report

Every benchmark run writes a per-run `benchmark-report.yml` inside that run directory. When you invoke `benchmark` with one or more models, it also writes aggregate outputs at the end of the batch under `tmp.benchmarks/`:

- `leaderboard.md`
- `leaderboard.html`
- `polyglot_leaderboard.yml`

Recommended default for sharing results with other people is `leaderboard.md`. It is the easiest format to paste into chat, issues, PRs, or notes. Use `leaderboard.html` when you want the interactive table view, and `polyglot_leaderboard.yml` when you want machine-friendly structured data.

By default, parallel runs now avoid live report rewrites while work is still in flight. In `--report-mode auto`, any run using parallel exercise threads, parallel model execution, or multiple `--model` flags behaves like `--report-mode end`:

- exercise workers write only their own `.aider.results.json` files during execution
- each model writes `benchmark-report.yml` once when that model completes
- root `leaderboard.md`, `leaderboard.html`, and `polyglot_leaderboard.yml` are written once when the batch completes

If you want continuously refreshed reports during the run for debugging or live inspection, pass `--report-mode live` explicitly.

For a two-model run like `github-multi-sample`, you will also get one run directory per model, for example:

- `tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--github-multi-sample--github-gpt-4o`
- `tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--github-multi-sample--github-gpt-4.1`

That means the common workflow is now a single `benchmark` command.

You can also run multiple models in one command by repeating `--model`, and the harness will generate the aggregate report for that batch automatically when the run finishes.

For example:

```bash
uv run benchmark kimi-gpt4.1 --model github_copilot/kimi-k2.7-code --model github_copilot/gpt-4.1 --languages csharp --num-tests 1 --threads 1 --unsafe
```

That single command creates one run directory per model plus the combined aggregate outputs under `tmp.benchmarks/`.

But you do not need to benchmark all models in one command. You can also run models separately and generate the final combined report later.

Practical workflow for two or more separate model runs:

1. Run one command per model.
2. Let each command finish completely.
3. Rebuild one combined leaderboard after the runs are done.

Example with two separate commands:

```bash
uv run benchmark gpt41-run --model github/gpt-4.1 --languages csharp --num-tests 5 --unsafe
uv run benchmark sonnet-run --model anthropic/claude-sonnet-4 --languages csharp --num-tests 5 --unsafe
```

Each command creates its own timestamped run directory under `tmp.benchmarks/`, for example:

```text
tmp.benchmarks/2026-07-13-10-00-00--gpt41-run
tmp.benchmarks/2026-07-13-10-30-00--sonnet-run
```

After both runs complete, you have 2 options.

Rebuild one report from every run currently present under `tmp.benchmarks/`:

```bash
uv run benchmark --report
```

Or rebuild one report from only the specific runs you want to include:

```bash
uv run benchmark 2026-07-13-10-00-00--gpt41-run 2026-07-13-10-30-00--sonnet-run --report
```

This same pattern works for more than two runs. Run each model separately first, then pass all desired run directories to one final `--report` command.

For example, these can happen as separate runs at different times:

```bash
uv run benchmark gpt41-run --model github/gpt-4.1 --languages csharp --num-tests 5 --unsafe
uv run benchmark sonnet-run --model anthropic/claude-sonnet-4 --languages csharp --num-tests 5 --unsafe
uv run benchmark gemini-run --model gemini/gemini-2.5-flash --languages csharp --num-tests 5 --unsafe
```

Then rebuild the final aggregate leaderboard from previous runs without re-running any benchmark:

```bash
# Rebuild from all run directories under tmp.benchmarks
uv run benchmark --report

# Rebuild from only selected prior runs
uv run benchmark 2026-07-05-12-00-00--gpt41-run 2026-07-05-13-00-00--sonnet-run --report
```

`leaderboard-report` still works too, but `benchmark --report` is the simpler repo-local entrypoint for rebuilding final reports from existing benchmark runs.

You can generate stats about any benchmark, including ones which are still running.
You don't need to run this inside the docker container, as it is just
collecting stats not executing unsafe python.

`leaderboard-report` still exists for re-generating aggregate reports later from existing benchmark directories without re-running the benchmark itself.

If you want to discard generated benchmark outputs before rebuilding a fresh final report, use the purge mode in `benchmark`. It removes generated aggregate leaderboard files plus the selected benchmark run directories from `tmp.benchmarks`.

```text
# Remove all benchmark result directories and generated leaderboard files
uv run benchmark --purge

# Remove one specific benchmark run and regenerate reports later from the remaining runs
uv run benchmark 2026-07-05-12-00-00--my-run --purge
```

If your shell resolves the direct entry point correctly, `benchmark.exe --purge` also works on Windows.

### Clean benchmark temp files

Use purge mode to clear generated benchmark temp output under `tmp.benchmarks` before a fresh run:

```bash
uv run benchmark --purge
```

You can also purge one specific run directory and keep the rest:

```bash
uv run benchmark 2026-07-05-12-00-00--my-run --purge
```

### Generate stats for a specific benchmarking directory

```bash
uv run benchmark --stats tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--a-helpful-name-for-this-run
```

Or summarize the most recent run automatically:

```bash
uv run benchmark --stats
```

By default, this also writes a YAML summary file into the benchmark directory:

```text
tmp.benchmarks/YYYY-MM-DD-HH-MM-SS--a-helpful-name-for-this-run/benchmark-report.yml
```

### Testing

The repo now has two test layers:

- unit tests in [tests/unit/test_benchmark.py](tests/unit/test_benchmark.py), [tests/unit/test_leaderboard_report.py](tests/unit/test_leaderboard_report.py), and [tests/unit/test_tracks.py](tests/unit/test_tracks.py)
- integration tests in [tests/integration/test_benchmark_cli.py](tests/integration/test_benchmark_cli.py) and [tests/integration/test_clone_exercism_tracks.py](tests/integration/test_clone_exercism_tracks.py)

Run both layers:

```bash
uv run pytest -vv
```

Run only the unit suite:

```bash
uv run pytest tests/unit -vv
```

Run only the integration suite:

```bash
uv run pytest tests/integration -vv
```

GitHub Actions also runs these suites separately on pushes to `main` and on pull requests, using
independent `Unit Tests` and `Integration Tests` jobs for clearer failure reporting.

The tests are now grouped by target surface instead of mixing all assertions into one file. That keeps benchmark CLI, clone CLI, and leaderboard/report behavior easier to extend with module-specific scenarios.

The integration suite runs real subprocess commands for the local CLI surfaces, including:

- `benchmark --help` prints usage and exposes `--languages`
- `clone-exercism-tracks --help` exposes `--update-existing` and `--repo-base-url`
- `cleanup-exercism-tracks --help` exposes `--dest-dir`
- mandatory `--languages` enforcement when running benchmarks
- `clone-exercism-tracks` downloading Exercism practices separately via command
- real local clone and `--update-existing` flows for Exercism tracks
- `cleanup-exercism-tracks` removing requested language directories (and their `exercises/practice` tree)
- `benchmark --num-tests` limiting the number of exercises processed
- `benchmark` automatically downloading a language track during a run when `exercises/practice` is missing
- offline benchmark smoke runs using `--no-aider --no-unit-tests` with GitHub Models such as `github/gpt-4o` and `github/gpt-4.1`
- a single `benchmark` command with multiple `--model` flags generating a separate run directory per model plus aggregate `leaderboard.csv`, `leaderboard.html`, and `polyglot_leaderboard.yml`
- separate per-model benchmark runs followed by `benchmark --report` to build the aggregate leaderboard in a second command
- `benchmark <run-dir> --stats` summarizing a completed run
- `benchmark <run-a> <run-b> --diffs` comparing pass/fail outcomes
- `benchmark --purge` deleting run directories and aggregate leaderboard files

Provider-backed benchmark execution and language-specific unit-test toolchains are still environment-dependent. Those paths are not part of the default offline integration suite.

The same YAML report is also written when a benchmark run finishes normally, so you do not need
to copy the console output by hand.

For a local leaderboard-style report export across one or more benchmark runs:

Use `leaderboard.md` as the primary text report. Keep `leaderboard.html` for interactive browsing.

```text
# Scan all runs under tmp.benchmarks and write tmp.benchmarks/leaderboard.md + leaderboard.html + polyglot_leaderboard.yml
uv run leaderboard-report

# Restrict the leaderboard to C# runs only
uv run leaderboard-report --stats-languages csharp

# Include only fully completed runs
uv run leaderboard-report --complete-only
```

The same aggregation flow is also available directly through `benchmark`:

```bash
# Rebuild reports from every prior run
uv run benchmark --report

# Restrict rebuild to specific run directories
uv run benchmark run-a run-b --report
```

Open `tmp.benchmarks/leaderboard.html` in a browser to see a searchable, sortable table with
percent-correct and cost bars inspired by the Aider leaderboard layout.

If you prefer a text-friendly export that is easy to paste into notes, PRs, or chat, use
`tmp.benchmarks/leaderboard.md`.

The same exporter also writes `tmp.benchmarks/polyglot_leaderboard.yml`, which is closer to the
multi-entry YAML format used by Aider's website data files under `aider/website/_data`.
For this aggregate YAML export, duplicate model entries are deduplicated so only the best run per
model is kept, with the latest run winning ties.

The benchmark report is a yaml record with statistics about the run:

```yaml
- dirname: 2024-07-04-14-32-08--claude-3.5-sonnet-diff-continue
  test_cases: 225
  model: claude-3.5-sonnet
  edit_format: diff
  commit_hash: 35f21b5
  pass_rate_1: 57.1
  pass_rate_2: 77.4
  percent_cases_well_formed: 99.2
  error_outputs: 23
  num_malformed_responses: 4
  num_with_malformed_responses: 1
  user_asks: 2
  lazy_comments: 0
  syntax_errors: 1
  indentation_errors: 0
  exhausted_context_windows: 0
  test_timeouts: 1
  command: aider --sonnet
  date: 2024-07-04
  versions: 0.42.1-dev
  seconds_per_case: 17.6
  total_cost: 3.6346
```

Field glossary:

| Field                          | Meaning                                                                                                                                                  |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dirname`                      | Name of the run folder under `tmp.benchmarks`. It usually includes the timestamp, run name, and for multi-model runs the model slug.                     |
| `test_cases`                   | Number of exercises actually completed so far in that run. For example, `5` means the benchmark processed 5 exercises.                                   |
| `model`                        | Exact model ID used for that run.                                                                                                                        |
| `edit_format`                  | Aider edit mode used with that model, such as `diff` or `whole`.                                                                                         |
| `commit_hash`                  | Git commit of the harness/repo when the run happened. `-dirty` means local uncommitted changes existed.                                                  |
| `pass_rate_1`                  | Percent of completed exercises that passed on the first attempt.                                                                                         |
| `pass_rate_2`                  | Percent of completed exercises that had passed by the second attempt. This is cumulative in the per-run YAML.                                            |
| `pass_num_1`                   | Raw count of exercises that passed on the first attempt.                                                                                                 |
| `pass_num_2`                   | Raw count of exercises that had passed by the second attempt.                                                                                            |
| `percent_cases_well_formed`    | Percent of completed exercises where the model response was structurally usable by the harness, with no malformed reply issues.                          |
| `error_outputs`                | Total count of model outputs flagged as error-like by the harness.                                                                                       |
| `num_malformed_responses`      | Total malformed model responses seen across the run.                                                                                                     |
| `num_with_malformed_responses` | Number of exercise cases that had at least one malformed response. This differs from `num_malformed_responses` when one case has multiple bad responses. |
| `user_asks`                    | Count of times the model tried to ask the user a question instead of just solving the task.                                                              |
| `lazy_comments`                | Count of outputs matching the harness's lazy-comment pattern, such as placeholder comments instead of real implementation.                               |
| `syntax_errors`                | Count of syntax errors seen in failing test or build output across attempts.                                                                             |
| `indentation_errors`           | Count of indentation errors seen in failing output across attempts.                                                                                      |
| `exhausted_context_windows`    | Count of times the run hit model context-window exhaustion conditions.                                                                                   |
| `prompt_tokens`                | Total input tokens sent to the model across all completed exercises in that run.                                                                         |
| `completion_tokens`            | Total output tokens returned by the model across all completed exercises in that run.                                                                    |
| `test_timeouts`                | Number of exercises where unit tests timed out.                                                                                                          |
| `total_tests`                  | Total exercises available in the selected benchmark scope. For example, `144` means the bundled C# scope has 144 exercises total.                        |
| `command`                      | Effective Aider command associated with the run.                                                                                                         |
| `date`                         | Run date derived from the benchmark directory timestamp.                                                                                                 |
| `versions`                     | Aider version metadata, if the harness can recover it from git history. It may be empty if that lookup fails in the current repo layout.                 |
| `seconds_per_case`             | Average wall-clock seconds per completed exercise in that run.                                                                                           |
| `total_cost`                   | Total model API cost for the completed exercises in that run.                                                                                            |

The key statistics are the `pass_rate_#` entries, which report the
percent of the tasks which had all tests passing.
There will be multiple of these pass rate stats,
depending on the value of the `--tries` parameter.

The yaml also includes all the settings which were in effect for the benchmark run.
It also reports the git hash of the repo at the time that the benchmark was
run, with `(dirty)` if there were uncommitted changes.
It's good practice to commit the repo before starting a benchmark run.
This way the `model`, `edit_format` and `commit_hash`
should be enough to reliably reproduce any benchmark run.

## Credits

This repo builds on work from these upstream projects:

- [Exercism C# track](https://github.com/exercism/csharp)
- [Aider](https://github.com/Aider-AI/aider)
- [Aider benchmark harness](https://github.com/Aider-AI/aider/tree/main/benchmark)
