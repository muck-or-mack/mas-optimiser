# mas-optimiser

An improvement loop for the harness of an LLM agent, with a statistical gate
on every change. A model reads how the agent did on training tasks and
proposes a change to the agent's harness, its instructions or its code. The
change is kept only when a paired test supports it on validation tasks,
which the proposing model never sees, at an error rate fixed for the whole
experiment, and a test set is opened once, at the end. The agent is
evaluated on
[EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym), a
benchmark of enterprise IT-operations workflows graded by SQL checks on the
final database state.

Status: the runner, the measure (every failed check explained down to the
database cell and the turn that set it), the statistics and the loop are
built and tested. The first full experiments of the loop are being run.

Documentation: <https://mas-optimiser.readthedocs.io/>, rebuilt from `main`
on every push. To read a change before it is pushed, `./scripts/docs.sh`
builds the documentation and opens it in the browser.

How it all works, file by file, with one attempt followed from the command
to the wrong cell and the turn that set it: `docs/how-it-works.md`, with
its figures in `docs/figures/`.

## Install

One command, from the repository root:

```bash
./setup.sh
```

This installs [uv](https://docs.astral.sh/uv/), which builds the Python
environment, into `~/.local/bin` if the machine has none (the version the
tests run on; it needs `curl` or `wget` and no administrator rights). It
then fetches the two submodules (the benchmark, and the loop code that
`mas-optimiser evolve` is built on), installs exactly the versions in
`uv.lock` into `.venv/`, with a Python of 3.11 or newer that uv fetches if
the system has none, unpacks the benchmark's seed databases and runs the
unit tests. Then activate the environment in each new terminal:

```bash
source .venv/bin/activate
```

The commands below assume it is active. Without activating, `uv run <command>`
runs any of them; the scripts in `scripts/` work either way.

## Models and keys

A model is chosen by the name of its file in `configs/models/`: `--model
<name>` on a command, or `model: <name>` in an experiment file. Five come
with the package:

| model file        | the model                                              | what it needs                                                                              |
|-------------------|--------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `haiku-5-5`       | Claude Haiku 5.5, through the Claude API               | one line of `.env`: `ANTHROPIC_API_KEY`                                                    |
| `gpt-5.1-azure`   | GPT-5.1, through an Azure OpenAI resource of one's own | four lines of `.env`, all beginning `AZURE_OPENAI_`                                        |
| `qwen3.6-27b-fp8` | Qwen3.6-27B in 8 bits, on one GPU of the machine       | no key; its server started ([Serving an open-weight model](#serving-an-open-weight-model)) |
| `qwen3.6-27b`     | Qwen3.6-27B as released, on two GPUs of the machine    | no key; its server started                                                                 |
| `gpt-oss-20b`     | gpt-oss-20b, on one GPU of 16 GB or more               | no key; its server started                                                                 |

### Where the keys go

Keys and anything else that belongs to an account of one's own go in one
file, `.env`, at the top of the repository. It is never committed. Make it
from the example and fill in the lines of the models to be used:

```bash
cp .env.example .env
```

One line holds one value, as `NAME=value`, with no spaces around the equals
sign. Quotation marks are not needed. For Azure OpenAI the four lines look
like this, with made-up values:

```
AZURE_OPENAI_API_KEY=0123456789abcdef0123456789abcdef
AZURE_OPENAI_ENDPOINT=https://my-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=my-gpt-5-1-deployment
AZURE_OPENAI_API_VERSION=2025-04-01-preview
```

Several keys sit in `.env` side by side. Each model file names the
variables it reads, so choosing a model chooses its keys and nothing has to
be swapped. A run on a model whose lines are blank stops at once and names
the variable that is missing.

To check a model and its key, run one task on it:

```bash
mas-optimiser run smoke --model gpt-5.1-azure --limit 1
```

### Adding a model

Copy the nearest file in `configs/models/` under a new name and change its
keys; every shipped file has a comment on each.

| key of a model file                            | what it gives                                                                                                                                                      |
|------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `llm_provider`                                 | the service: `anthropic`, `openai`, `azureopenai`, `google`, `googlevertexai`, `aws_bedrock`, `openrouter`, `deepseek`, `qwq`, or `vllm` for a server of one's own |
| `llm_model`                                    | the model's name at that service; for Azure OpenAI, the name of the deployment                                                                                     |
| `llm_api_endpoint`, `llm_api_version`          | the address and the version of the API, for a service that needs them                                                                                              |
| `api_key_env`                                  | the name of the variable of `.env` that holds the key; left out for a server that asks for none                                                                    |
| a key ending in `_env`                         | the name of the variable of `.env` that holds the value of the field it is named after: `llm_api_endpoint_env` for `llm_api_endpoint`                              |
| `temperature`, `top_p`, `max_tokens`, `effort` | how the model samples, the most tokens of an answer, and how much it reasons                                                                                       |
| `max_retries`                                  | how many more times a call is sent when the service refuses it or gives no answer; left out, the provider's library sends it twice more                            |
| `pricing`                                      | US dollars per million tokens, six figures, from the provider's price list; all zero for a server of one's own                                                     |
| `server`                                       | for a model served on the machine: how its server is started                                                                                                       |

A second account at the same service is a second file that names other
variables, `AZURE_OPENAI_API_KEY_2` for instance, with those lines added to
`.env`.

## Benchmark servers

Each [EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym)
domain runs as its own server in Docker. Start the ITSM, CSM and HR servers,
then check that they answer:

```bash
docker compose -f docker/compose.yaml up -d
pytest -m integration
```

Stop them with `docker compose -f docker/compose.yaml down`.

They need Docker, about 1.2 GB on Docker's own disk for the three images
(which is not always the disk of the home folder: `docker system df` shows
what Docker holds), and the ports 8001, 8006 and 8008 of the machine, where
they are reachable from that machine only. A run checks that its domain's
server answers before it starts, and names the start command if not.

## Serving an open-weight model

An open-weight model runs on the machine's own GPUs, served by
[vLLM](https://docs.vllm.ai/). One command installs vLLM, starts the server
and reports on it, and the settings are in the model's file.

The example throughout is Qwen3.6-27B in its 8-bit version,
`configs/models/qwen3.6-27b-fp8.yaml`: the model with each weight stored in
8 bits and not 16, published by its authors, at half the size, so that it
fits one card of 48 GB. `configs/models/qwen3.6-27b.yaml` is the model as
released, which needs two such cards. The two are different models, and
results say which was used. `configs/models/gpt-oss-20b.yaml` is a smaller
model of another family, which takes about 16 GB of a card and so leaves
room for more attempts at once; its file sets a temperature of 0.

### What the machine needs

| the machine needs                      | for the example as its file is written                                                                                           | how to check                                                                  |
|----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| an NVIDIA GPU with free memory         | one card of 48 GB with nothing else running on it; the model takes about 41,000 MiB of it                                        | `nvidia-smi`: the memory in use on each card and the programs listed under it |
| a card that computes in 8 bits         | NVIDIA's Ada Lovelace generation or newer, such as an L40; on an older card vLLM runs the model with the weights alone in 8 bits | the card's name in `nvidia-smi`                                               |
| an NVIDIA driver that supports CUDA 13 | asked for by vLLM 0.34.0, the release the file names                                                                             | the first line of `nvidia-smi` gives the CUDA version                         |
| disk space in the home folder          | about 50 GB: 19 GB for vLLM and 31 GB for the model's weights                                                                    | `df -h ~`                                                                     |
| a free port                            | 8028                                                                                                                             | the command says so if another program has it                                 |

Docker is not needed, and neither are administrator rights.
`docs/gpu-memory.md` shows where the GPU memory goes for both versions of
the model, how the need grows with the benchmark's domain and with the
attempts run at once, and what to change for other cards.

### From a fresh clone to a first task

```bash
./setup.sh
source .venv/bin/activate
mas-optimiser server qwen3.6-27b-fp8      # again, until it says ready
docker compose -f docker/compose.yaml up -d
mas-optimiser run smoke --model qwen3.6-27b-fp8 --limit 1
```

The fourth line starts the benchmark's servers and the fifth runs one task
on the model.

### Start, check, stop

```bash
mas-optimiser server qwen3.6-27b-fp8
```

does four things in turn:

1. It checks that the server's port is free, and warns of any card that
   already has memory in use.
2. The first time only, it installs the release of vLLM that the file names
   into an environment of its own, apart from the package's
   (`~/.cache/mas-optimiser/vllm-<release>/`, about 19 GB, a few minutes on
   a fast connection).
3. It starts the server in the background, where it outlives the terminal,
   and prints each command it runs.
4. It says whether the server is `loading` or `ready`.

The first start also downloads the model's weights (31 GB) into the user's
Hugging Face cache, `~/.cache/huggingface`, and then loads the model. Run
the same command again at any time: it starts nothing new and says whether
the server is still `loading` or `ready`. The server's own output is in
`~/.cache/mas-optimiser/<name>.log`, and the server is reachable from this
machine only.

```bash
mas-optimiser server qwen3.6-27b-fp8 --stop
```

ends the server. The environment and the weights stay, so the next start
installs and downloads nothing.

### What the command says

| the command says                                                     | what it means                                                                                                                                                                               | what to do                                                                                                         |
|----------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `loading`                                                            | the server is running and does not list the model yet                                                                                                                                       | wait; `tail -f` on the log shows the download and the loading                                                      |
| `ready at <address>`                                                 | the server answers and lists the model                                                                                                                                                      | run an experiment with `--model <name>`                                                                            |
| `warning: card ... already has ... MiB in use`                       | another program holds memory on a card the server is about to use                                                                                                                           | look at `nvidia-smi`; if the server then stops for lack of memory, free the card or name other cards with `--gpus` |
| `port ... is already in use`                                         | another program listens on the server's port, which is common on a shared machine                                                                                                           | give the server another port in `llm_api_endpoint` of the model file, then start again                             |
| the server `stopped by itself`, then the errors its output ends with | it ended without being asked. `out of memory` means the model did not fit in the cards' free memory; `estimated maximum model length` means the limit on prompt length is too long for them | put the cause right (`docs/gpu-memory.md`), clear the server with `--stop`, and start again                        |
| `precompiled kernels were not installed`                             | the last step of vLLM's install, which is optional, could not fetch them                                                                                                                    | nothing: the server starts without them, and the message gives the command that tries again                        |

### Fitting it to another machine

The settings that depend on the machine are the choice of model file,
three lines within a file and two options of the command. To change the file's, copy it under a new
name in `configs/models/` and use that name in the commands.

| the machine has                | what to set                                                                             |
|--------------------------------|-----------------------------------------------------------------------------------------|
| two cards of 48 GB to itself   | the model as released: `qwen3.6-27b` in place of `qwen3.6-27b-fp8` in the commands      |
| a different number of cards    | `--tensor-parallel-size` in the file's `server` block                                   |
| less memory on its cards       | `--max-model-len` in the `server` block, the longest prompt and answer the server takes |
| the port taken                 | the port in `llm_api_endpoint`                                                          |
| some cards in use, others free | `--gpus 2,3` on the command, the cards by their numbers in `nvidia-smi`                 |
| little room in the home folder | `--cache <folder>` on the command, where the weights are kept                           |

An evaluation records the file's `server` block in its `evaluation.json`
beside the model's other settings, so that results say how their model was
served.

### In Docker instead

With `--docker` the same release runs as a container of vLLM's image,
named after the model file, with the weights in the Docker volume
`mas-optimiser-models` (or the folder `--cache` names); `--docker --stop`
removes the container and `docker logs -f <name>` shows its output. This
needs Docker, room for the image (about 10 GB to download) on Docker's
disk, and NVIDIA's Container Toolkit
([its install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)),
which takes administrator rights to install.

### Another model, and what has been run

Another model is another file in `configs/models/` with its own `server`
block; a file without one, such as a hosted model's, is refused.

The command has been run end to end on one GPU with a small model of the
same family, Qwen3-0.6B: vLLM installed, the server started, a task run
against it, the reason for a stop printed, the server stopped. On a machine
with two cards of 48 GB, the model as released was installed and downloaded
and began to load, and stopped for lack of memory because another program
held the cards. On one card of that machine the 8-bit version has since been
served, holding 42 of the card's 45 GiB, with no task yet run against it,
and gpt-oss-20b has been served and has run a task in about 25 seconds.
`--docker` has been run with a stand-in for vLLM's image.

## Running an experiment

A run needs its model's key in `.env` ([Models and keys](#models-and-keys)).
An experiment is a short
YAML file in `configs/experiments/` that sets the model, the domain or
domains, mode, task limit and tracing options, with a comment on each;
`smoke.yaml` is the annotated example, `demo.yaml` the two-task
demonstration and `baseline-haiku.yaml` the full baseline (every task of the
three domains, three attempts each). Run one by name:

```bash
mas-optimiser run smoke
```

Any option on the command line overrides the file, so `mas-optimiser run
smoke --limit 1 --domain csm` is a quick variant without editing anything,
`--task <task_id>` runs one chosen task (repeat it for several), and
`--split haiku-20-40-40:training` runs only that set of a split (see
below). Without `--split` a run takes every task. The summary of a run of
a split's validation or test set gives the headline and leaves out what
went wrong ("Reading the results" in the documentation, section 8).
Experiments give the agent every tool its domain's server offers
(`mode: all_tools`); `--mode oracle` gives it only the tools the benchmark
lists with each task, the setting that published results use (see "What is
fixed and what is optimised" in the documentation).
To define a new experiment, copy `smoke.yaml` under a new name. Models are
named the same way: `model: haiku-5-5` means `configs/models/haiku-5-5.yaml`,
and a path works in place of any name. The command checks that the domain's
server answers before it starts, and names the servers' start command if not.

Prompt caching is on by default (`prompt_caching: true`; `--no-prompt-caching`
turns it off for one command). Every call of an attempt repeats most of the
call before it: the tool descriptions, the policy and the conversation so
far. With caching the provider stores that input and bills it at about a
tenth of the input rate when it is sent again within five minutes, and at a
quarter more than the input rate when it is first stored. The model receives
the same input either way. In a check on training tasks the same calls cost
about half as much with each task's own tools and about a fifth as much with
every tool. It is built for Anthropic models only, since it uses that
provider's request field and tool format: with any other provider the
setting does nothing and the run says so. `evaluation.json` records whether
it was used, and costs with and without it are not comparable.

A call to the model has a time limit of 300 seconds (`model_timeout: 300`;
`--model-timeout <seconds>` changes it for one command). A try that gets no
answer in that time is given up and repeated, up to nine tries in all unless
the model file sets `max_retries`; what follows if none succeeds is
described below, under the attempt's limits. Without a limit, a connection
that goes silent would hold the run for ever; real calls answer in seconds.
The limit is set on the provider's own client, for Anthropic models and for
models reached through an OpenAI-style client (the providers `openai`,
`azureopenai`, `vllm` and `openrouter`); with any other provider none is set
and the run says so.

A service may also refuse a call outright, most often because its rate
limit is used up: a deployment allows so many tokens a minute, and one
shared with other users is often at that ceiling. The service says how long
to wait, a few seconds as a rule, and the provider's library waits that
long and sends the call again, twice at most unless the model file gives
another number as `max_retries` (`configs/models/gpt-5.1-azure.yaml` gives
1,000). The benchmark's client makes three such rounds, and a call still
refused has failed for good, which is likewise described below. The waiting
counts towards the
attempt's limit of seconds, described next, which is the cap on it. A
call of one of the improvement loop's own roles has the same limit of
seconds as an attempt. A run's `evaluation.log`, in its folder, has a line
for every answer and every refusal, with the wait after each.

An attempt as a whole has two limits of its own: 3,600 seconds
(`attempt_timeout`) and 100 calls to the model (`attempt_calls`), with
`--attempt-timeout` and `--attempt-calls` to change them for one command. An
attempt that passes either is ended and recorded as an error, counted as a
failure, with no tokens against it: what it spent before it was ended is
not in the run's cost. The runner keeps the limits and not the agent, so a
changed harness cannot lift them; an attempt of the starting harness uses a
small part of each.

An attempt can also end because a service it needs failed, and not the
harness: the model service was down or refused the key, or the domain's
server was down. Counting that as the harness's failure would lower a score
for nothing, so the runner asks the service before it counts anything. After
a call to the model that failed for good it asks the model service a
one-word question, and after an attempt that ended in an error it asks the
domain's server for its health.

- **The service answers.** After a failed call the attempt is run again, up
  to `attempt_retries` times (2), and a call that still fails is the
  harness's own doing. An error with the server answering is the harness's
  own doing at once. Either way the attempt counts as a failure, with none
  of its checks passed. An agent that asks for a tool the server does not
  have ends like this, since the benchmark ends such an attempt.
- **The server was down and comes back** within `service_wait` seconds
  (1,800): the attempt is run again, as after a failed call.
- **The service does not answer** within that time: the attempt is marked
  `service_error` in `tasks.csv`, left out of every score, and the command
  ends with an error. The improvement loop stops at such a job and carries
  on from it when its command is given again.

A self-hosted open-weight model is run the same way, through its own model
file. `configs/models/qwen3.6-27b-fp8.yaml` is one, for the 8-bit version of
Qwen3.6-27B served by vLLM, which answers in the OpenAI API's format: the
file gives the provider `vllm`, the server's address and no API key, and its
`server` block says how the server is started
([Serving an open-weight model](#serving-an-open-weight-model)). With the
server ready,

```
mas-optimiser run smoke --model qwen3.6-27b-fp8
```

runs the smoke experiment on that model. Nothing is billed per token, so
the cost in dollars is zero and the tokens and seconds, recorded as for any
model, are the measures of cost. Prompt caching does not apply. This path
has been run against a live vLLM server with a small model of the same
family (Qwen3-0.6B, with the task's own tools) and with gpt-oss-20b on one
L40, and not yet with Qwen3.6-27B.

The agent that runs the tasks comes from a harness folder. `harnesses/default/`
is the starting one: its `agent.py` is the benchmark's own agent loop, 150
lines, and its `prompt.md`, empty to start with, is text the agent adds to
the end of every task's system message. `harness: default` in the
experiment file names it, and `--harness <folder>` takes any copy of the
folder by its path, so a changed copy is run and compared like any other
harness. Everything outside the folder (the tasks, the servers, the checks,
the scoring) is the same for every harness; see "What is fixed and what is
optimised" in the documentation. `evaluation.json` records the harness an
evaluation used.

`./scripts/demo.sh` starts the servers if needed and runs the `demo`
experiment. `mas-optimiser --help` lists the commands, `mas-optimiser run
--help` the options, and every script in `scripts/` and `setup.sh` answers
`-h`.

## A demonstration in ten minutes

One attempt at one ITSM task, watched live, then read. It costs a few cents.

1. Start Docker Desktop, then the tracing UI (optional, once):
   `scripts/langfuse.sh up`, and open http://localhost:3000.
2. Run one task of the `demo` experiment (the servers start if needed):
   ```
   scripts/demo.sh --limit 1
   ```
3. While it runs, open the newest session in Langfuse: the trace grows one
   turn at a time, each with the model's reasoning summary, its tool call
   and the server's answer.
4. The command ends by printing the evaluation folder. Open its `summary.md`
   for the verdict, then `tasks/<task_id>/attempt-1.md` for the attempt as
   a page: the turns, every row the agent wrote, and each check as a table
   of what it wanted against what it found.
5. To query the database the attempt ended with:
   `mas-optimiser rebuild <folder> <task_id> 1`
   writes `attempt-1.sqlite` beside the page.
6. `docs/how-it-works.md` follows a made-up attempt with the same parts
   through the code, file by file, and `docs/data-model/itsm.md` names
   every table the checks query.

## Reading the results

Results live under `evaluations/` (gitignored), grouped by experiment, then
by run, then by domain. One domain's folder is an evaluation: one model on
one domain's tasks.

```
evaluations/<experiment>/
  index.csv                        one row per evaluation: settings and totals
  <run>/                           one run, named by its start: 2026-11-17-0817
    summary.md                     the run as a whole, then a row per domain
    run.json                       the run's settings and totals
    <domain>/                      one evaluation: itsm, csm or hr
      summary.md                   how the evaluation went and what went wrong, one page
      evaluation.json              every setting, the task list and the totals
      tasks.csv                    one row per attempt: verdict, checks, tables by result, tool errors, tokens, cost
      checks.csv                   one row per attempt and check: verdict, diagnosis, kind, failure, patch
      cells.csv                    one row per failed check, touched row and condition: wanted against found
      tables.csv                   one row per attempt and table: the checks on it, the rows changed, the result
      failures.csv                 one row per table, column and kind of failure, most frequent first
      tools.json                   every tool the domain's server offers
      evaluation.log               the benchmark's log of every request, with each refusal and wait
      tasks/<task_id>/
        attempt-<n>.md             the attempt as a page: request, tables, tools, checks, turns, rows
        attempt-<n>.json           the executor's complete record of the attempt
        attempt-<n>.changes.json   every row the attempt inserted, updated or deleted
```

`docs/results.md` ("Reading the results" in the documentation) follows one
small run through these files: every column of every file, the words the
results use, and how to read one attempt's page.

To read an evaluation, open `summary.md` first: the headline table (a row
per measure, with what it counts), then the tables that were wrong or
missed and the most frequent failures, then the tasks that passed least
with their most frequent failure. For a task on that list, open its `attempt-<n>.md`:
under each failed check, the row the agent wrote, which turn wrote it, and
each condition marked `ok` or `WRONG` with the value found and the turn that
set it. `cells.csv` holds the same conditions as data, for filtering across
an evaluation (every wrong `status`, say). `tables.csv` says for each
attempt which tables the checks look at and how the attempt did on each:
`correct`, `wrong` (changed, and a check on it failed) or `missed` (a check
on it failed and the attempt changed nothing in it), with the tables it
changed that were `not asked for`. `failures.csv` ranks what went wrong
across the evaluation: tables not written, and wrong cells by column with
whether the value was passed wrong or left out of the call.

`attempts` in the experiment file (or `--attempts`) runs every task that many
times, each attempt from a freshly seeded database, so that a task the agent
always fails can be told from one it fails half the time; `tasks.csv` has a
row per attempt and `evaluation.json` counts the tasks that passed every attempt.
`concurrency` (default 4) is how many attempts are in flight at once. Each row
also carries the attempt's cost in US dollars, priced with the table in the model
file (`configs/models/<model>.yaml`, which records when the prices were
checked), and `evaluation.json` and `index.csv` carry the evaluation's total.

A task passes when every one of its checks (the benchmark's "verifiers") passes.
Three things differ from
the benchmark's own count, and the `benchmark_` columns of `tasks.csv` and
`checks.csv` give that count alongside. All three are applied to a task's
check list before each attempt (`checks.prepare`), so the server's verdicts
come back for every check under a unique name. The benchmark keys verdicts by
name, so where a task gives two checks the same name only the
last one counts; in 59 of the 308 oracle tasks names clash, and in 27 of
those the shared name hides different checks. Here an exact copy of a check
(32 tasks simply repeat their list) counts once, and every distinct
check counts. And 33 count checks in seven tasks give their expected value as
the string `'1'`, which the benchmark compares with `==` against the integer
the database returns, so they can never pass; here count checks are compared
as numbers. And a short patch list (`data/check_patches.json`) excludes the
few checks that no correct attempt can satisfy and repairs the few whose query
names the wrong record, each entry with its evidence; the benchmark's own
verdict on every patched check is still recorded beside ours (the
`benchmark_passed` and `patch` columns of `checks.csv`). A task whose every
check is excluded cannot be judged and is not run (one ITSM task at the
current patch list).

How a failed check is diagnosed cell by cell and traced to the turn that
set each wrong value, and what the `kind` and `failure` columns of `checks.csv`
mean, is in `docs/how-it-works.md` (sections 2.6 and 2.7), which follows
one real attempt through the whole pipeline.

A run is one invocation of an experiment, and its folder is named by its
start minute and any `--label` word: `evaluations/smoke/2026-10-27-0930/`,
or `2026-10-27-0930-retry` with a label; the seconds are added only when a
second run starts within the minute. Inside it, one evaluation folder per
domain (`itsm/`, `csm/`, `hr/`), `run.json` with the settings and the
pooled totals, and a `summary.md` with the headline for the run as a whole
and a row per domain. Every other setting (model, mode, split, task limit,
command-line overrides) is a column of `index.csv` and a field of
`evaluation.json`, not part of a name. Runs made before 13 November 2026
were regrouped into this layout; their index rows carry the same columns. The experiment, not
the model, domain or date, is the folder level, because it is the question
being asked; the others are columns of `index.csv`, where any cut of the
results is one table away. An attempt that raises an error counts as failed
and the error count is reported separately; the benchmark keeps no token
counts for such an attempt. An attempt that a service it needs never
answered, the model service or the domain's server, is the exception: it is
marked `service_error` and counted nowhere.

Every evaluation folder is written to be read without a tracing server:

- `summary.md`: a page on how the evaluation went and what went wrong, by table and
  column, with the tasks that passed least and what each file holds;
- `tasks.csv` (one row per attempt), `checks.csv` (one per attempt and
  check, with the failure and its kind), `cells.csv` (one per failed
  check, touched row and condition, wanted against found, with the turn the
  value came from), `tables.csv` (one per attempt and table, with its
  result) and `failures.csv` (one per table, column and kind of failure);
- `tasks/<task_id>/attempt-<n>.md`: the attempt as a page: the request; one
  row per table the checks look at, with the result; the tools the task
  was given and the arguments each takes; every check with, under a
  failure, a table of each column wanted against found, marked ok or
  WRONG, with where the value came from; what the agent did turn by turn,
  each call with its arguments and what came back; and every row it
  inserted, updated or deleted. Beside it, the full record (`.json`) and
  the changes (`.changes.json`).

## Where to find what

| To find | Open | Written by |
|---|---|---|
| what every arm of an experiment shares, and what may differ | `docs/methodology/fixed-and-optimised.md` | by hand |
| whether it passed, and how many | `summary.md` (headline); `index.csv`, one row per evaluation of the experiment | `report.summary`, `runner.write_index_row` |
| the run as a whole: the headline pooled over its domains, then a row per domain | the run's `summary.md` and `run.json` | `runner.write_run_summary` |
| what went wrong overall: by table, then by failure and column; the tasks that passed least | `summary.md`, "What went wrong" | `report.summary` |
| what went wrong most often, as data | `failures.csv` | `runner.write_tables` |
| each attempt's verdict, tables by result, errors, tokens and cost | `tasks.csv` | `runner.task_outcome` |
| each check's verdict, kind, failure and patch | `checks.csv` | `checks.check_records` |
| every cell a failed check tests, wanted against found, and the turn its value came from | `cells.csv` | `report.cell_rows` |
| which tables each attempt got right, got wrong, missed or changed unasked | `tables.csv` | `report.table_rows` |
| every row each attempt changed in the database | `attempt-<n>.changes.json` | `states.diff` |
| one attempt, readable: tables, tools, checks, turns, rows | `tasks/<task_id>/attempt-<n>.md` | `report.attempt_text` |
| one attempt, complete and raw | `attempt-<n>.json` | the benchmark's executor |
| every tool the domain server offered, with its description and argument schema | `tools.json` | `runner.save_tools` |
| the database an attempt ended with | `mas-optimiser rebuild`, then `attempt-<n>.sqlite` | `rebuild` |
| what the model was sent and answered, call by call; a run in progress | Langfuse: a session per evaluation, a trace per attempt | `tracing` |
| the evaluation's settings and totals | `evaluation.json` | `runner.evaluate` |
| the benchmark's own log of the run | `evaluation.log` | the benchmark |
| the tasks' policy, request, checks and tools | the dataset and `vendor/enterpriseops-gym/data/revised/`; the corrections in `data/check_patches.json` | |
| the tables a domain has, with keys and references | `docs/data-model/<domain>.md`; the schema in `data/schemas/<domain>.sql` | `mas-optimiser schema` |
| which tasks are training, validation and test | `configs/splits/<name>.json` | `mas-optimiser split` |
| every run of an experiment, at a glance, with its domains | `mas-optimiser list <experiment>`, from `index.csv` | `listing` |
| whether a candidate beat the current harness, by how much, and the smallest gain the data could show | `comparison.md`, its data files and `figures/` in the folder `mas-optimiser compare` writes | `stats.compare` |
| whether that test can be trusted on this benchmark and model | `calibration.md` and its data files in the folder `mas-optimiser calibrate` writes | `stats.calibrate` |
| what an experiment of the loop found and what each part of it cost | `report/report.md` and its data files in the experiment's folder, written by `mas-optimiser report` | `evolve.report` |

## Splitting the tasks

```
mas-optimiser split --name by-domain
```

Draws the training, validation and test sets with a seeded shuffle and
writes `configs/splits/<name>.json`. The proposer reads training traces
only, the acceptance gate decides on validation and returns its verdict
alone, and the test set is opened once for the reported number.

| option          | what it sets                                            | default          |
|-----------------|---------------------------------------------------------|------------------|
| `--proportions` | shares of training, validation and test, summing to one | `0.17 0.52 0.31` |
| `--seed`        | seed of the shuffle; the same seed gives the same sets  | `0`              |
| `--balance`     | what every set holds the same mix of                    | `domain`         |
| `--experiment`  | the baseline run read for each task's difficulty        | none             |
| `--seen`        | a file of tasks already read by hand, kept in training  | none             |

```
mas-optimiser split --name trial --proportions 0.6 0.2 0.2 --seed 7
mas-optimiser split --name plain --balance
mas-optimiser split --name by-difficulty --experiment baseline-haiku
mas-optimiser split --name with-seen --seen seen-tasks.yaml
```

`--balance` takes `domain`, `difficulty`, both, or nothing after it. The
tasks are grouped by what is named and each group is shared out in the
proportions, so every set holds the same mix; with nothing named the tasks
are shuffled as one group. The more that is balanced, the smaller the groups,
and the set sizes can differ from the exact shares by a few tasks.

A task's difficulty is whether all, some or none of its attempts passed in a
baseline run, named by `--experiment`. Naming one is enough: with no
`--balance` given, the sets are then balanced on domain and on difficulty.
With no run yet there is no difficulty to balance on, and the sets are
balanced on domain alone. For each domain the command reads the experiment's latest full
evaluation that has no label and no command-line option that changes what is
measured (`--domain`, `--concurrency`, `--out` and the tracing switch change
nothing). It stops if a domain with a full evaluation has none that
qualifies, or if the evaluations chosen differ in model, mode, harness or
attempts, and the split covers the tasks that run scored. Without a balance
on difficulty the task list comes from the dataset and no run is needed.

`--seen` names a YAML file that gives each task id a reason. A task someone
has read cannot serve as an unseen one, so these go into training before the
shuffle. Validation and test tasks are never read; only their scores are
looked at.

A split is drawn once. The command refuses a name that already has a file,
since a redraw could move tasks someone has read into a sealed set. The file
holds the seed, the proportions, the balance, what was read, the seen tasks
and the three lists of task ids, and no result of any single task. The split
`haiku-20-40-40`, used in the examples of this page, was drawn on 27 October
with both balances and 20 seen tasks. The split `seed-1`, which the
experiment files `evolve-haiku`, `evolve-gpt-5.1` and `evolve-gpt-oss-20b`
use, has the default shares, a balance on domain and no seen task. Paths
here are inside the repository, whatever the working directory.

## Local copies of the seed databases

```
mas-optimiser schema
```

Seeds a database on each domain's server from every seed file its tasks
use, reads the CREATE statements of its tables and indexes back, checks
that all of a domain's seed files share one schema, and writes it to
`data/schemas/<domain>.sql`, with a readable version at
`docs/data-model/<domain>.md`: every table, its key, its columns and the
tables it references. With the schema file, `seeds.build_seed_database`
builds a local SQLite copy of any task's starting database, so new checks
can be tested without the servers. The copies match the servers' databases
row for row (`uv run pytest -m integration tests/integration/test_seeds.py`),
with one exception: in many rows the HR server replaces `created_at` and
`updated_at` with the time it loads the seed (which rows depends on the seed
file), so those two HR columns are left out of the comparison. The CSM
server returns at most 100 rows to a query that sets no `LIMIT` of its own. Both folders are found from the repository,
whatever the working directory.

## Recomputing an evaluation

```
mas-optimiser rescore evaluations/<experiment>/<run>/<domain> [...]
```

Recomputes every attempt of an evaluation from its saved record and its
changes against the seed, without running the agent: the final database is
rebuilt locally, every check is run again as the benchmark's engine would run
it, each failure is diagnosed and traced, and the tables, pages and summary
are rewritten, with the totals in `evaluation.json`, the evaluation's row
in the experiment's `index.csv` and, for one domain of a run, the run's
`run.json` and `summary.md`. Use it after a change to the diagnosis or to the
checks. An attempt with no saved changes (one that raised before it
finished) keeps its record and counts as failed, as the runner counted it;
a task with no check left to judge by has its pages rewritten and stays out
of the tables. The record keeps the verdicts the attempt first produced as
`verification_results_as_run` beside the recomputed ones. A condition on the
clock (`datetime('now', '-1 day')`) is judged at the time the attempt's
checks ran, so the result does not depend on the day of the rescore.

To look at the database one attempt ended with:

```
mas-optimiser rebuild evaluations/<experiment>/<run>/<domain> <task_id> <attempt>
```

writes it, rebuilt from the saved changes, as `attempt-<n>.sqlite` beside
the record. Open it with `sqlite3` or an editor's SQLite viewer to run a
check's query by hand or browse every table; it is the database `rescore`
judges on.

## Listing an experiment's evaluations

```
mas-optimiser list <experiment> [--domain itsm]
```

Prints the experiment's `index.csv` as a table, one block per run: a row
for the run as a whole, its domains' counts summed, then a row per domain,
with mode, model, label, attempts and checks passed, errors and cost. It
is the quickest way to find a run without reading folder names.

## Comparing two evaluations

```
mas-optimiser compare --experiment <experiment> --a <name> --b <name> [--split <name>] [--out <folder>]
```

Compares a candidate (`--b`) with the current harness (`--a`) task by task,
each side one evaluation per domain, on the tasks of a split's set (the
validation set by default) or on every task both sides scored. A side is
named by its run (`2026-11-03-0803`), by a day when the experiment ran
once that day, or by a run folder or evaluation folders given one `--a` or
`--b` at a time; names are resolved through the experiment's `index.csv`.
The two baseline runs of the same harness, which give the standard error
and the minimum detectable effect:

```
mas-optimiser compare --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803 --split haiku-20-40-40
```

It writes its results as data: `tests.csv`, a row per outcome and set of
tasks with every number of the test; `rules.csv`, a row per decision rule;
`sides.csv`, the two sides with their settings, cost and time; and
`differences.csv`, a row per task. `comparison.md` is a report of those
files for reading, with the figures of `figures/` embedded: what is
compared, the result for both outcomes (checks passed and attempts fully
passed, in percent, their difference in percentage points), cost and time,
the result by domain, the test in full with what each quantity is, and the
decision rules. The method is in `docs/methodology/acceptance-gate.md`, and
how to read the report, part by part, in `docs/results.md` ("Reading a
comparison").
Running it on the 27 October and 3 November baselines, two runs of one
harness, gives the minimum detectable effect every later gain is measured
against.

## Checking the test on two runs of one harness

```
mas-optimiser calibrate --experiment <experiment> --a <run> --b <run> [--split <name>] [--out <folder>]
```

When two runs are of the same harness, any difference between them is
chance. The command pools each task's attempts from both runs, splits them
at random into two halves 200,000 times, and computes the comparison's
statistic for each split, so every split compares the harness with itself:

```
mas-optimiser calibrate --experiment baseline-haiku --a 2026-10-27-0528 --b 2026-11-03-0803 --split haiku-20-40-40
```

It writes `calibration.csv` (the mean and width of the statistic and the
share of splits each decision rule accepts, each measured against the
value expected), `pulls.csv` (the statistic of every split), `scores.csv`
(each task's score in each run) and `calibration.md`, a report of them
with three figures. It refuses two runs that differ in mode, model or
harness. Run it on a new model or benchmark before relying on a
comparison's verdicts there; the method is in
`docs/methodology/acceptance-gate.md`.

## Improving a harness

```
mas-optimiser evolve <experiment>
```

Runs the improvement loop on one split of the tasks: the baseline of the
starting harness, then rounds in which a model reads how the current harness
did on the training tasks and proposes a change to a copy of the harness
folder, the copy is scored on the validation tasks, and the acceptance gate
decides whether it replaces the current harness. At the end every harness
that was kept is run once on the test tasks. The method, who may read what,
and the files it writes are on "The improvement loop" in the documentation.

The experiment file holds the settings of `run` and the loop's own:

| setting            | what it sets                                                                                                                                      | default           |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|-------------------|
| `split`            | the split whose three sets are used, by its name alone                                                                                            | none, must be set |
| `harness`          | the folder the experiment starts from                                                                                                             | `default`         |
| `proposer_model`   | the model file of the roles that read and propose                                                                                                 | the agent's model |
| `rounds`           | rounds of the loop                                                                                                                                | 10                |
| `candidates`       | candidates proposed in each round                                                                                                                 | 1                 |
| `alpha`            | the chance, over the experiment, of keeping a useless change                                                                                      | 0.05              |
| `filter_share`     | the share of the validation tasks a candidate is run on first, and dropped if it does no better there; 0 for no filter                            | 0.5               |
| `filter_threshold` | the gain, in percentage points, a candidate must be above on the filter's tasks                                                                   | 0                 |
| `pool_share`       | the share of the training tasks whose traces the roles that read and propose may open in a round; 1 for every task                                | 0.5               |
| `pool_pick`        | how that share is picked from the tasks ranked by score: `spread`, evenly and one place further each round, or `ends`, the lowest and the highest | `spread`          |
| `editable`         | the types of file a proposal may edit: `.md` for the agent's text, `.py` for its code                                                             | `.md` and `.py`   |
| `approval`         | `each`: every change is shown at the terminal and run only after a yes; `none`: no one is asked                                                   | `none`            |

```
mas-optimiser evolve evolve-smoke
```

is a trial of the loop itself: three tasks of each set, one attempt, one
round, text edits only, a few minutes and a few cents. Results go to
`evolutions/<experiment>/<split>/`, and giving the command again carries an
interrupted experiment on.

A full experiment takes hours, and a command started over SSH ends when the
connection closes. On a remote machine, start it inside `tmux`, which keeps
a terminal open on the machine whether or not anyone is connected to it:

```bash
tmux new -s evolve                  # a terminal that stays on the machine
source .venv/bin/activate
mas-optimiser evolve <experiment>
```

Ctrl+b and then d leaves that terminal with the experiment still going,
and `tmux attach -t evolve`, from any later connection, comes back to it.

```
mas-optimiser report evolutions/<experiment>/<split>
```

writes `report/report.md` in that folder, for an experiment that has
finished or one still going: each harness kept, with its gain on the test
tasks and a 95% confidence interval; each round's candidate and where it
ended; what every part of the experiment cost; and a figure of the score
after each round. The tables are beside it as CSV files.

A candidate is run on the filter's share of the validation tasks first,
and on the rest only if its score there is above the current harness's by
more than the threshold. That saves most of the cost of a change that does
harm and half the rest of the cost of one that does nothing, and it cannot
make the gate less strict, since a change is kept only if it passes the
gate's test on every validation task as before. "The improvement loop" in
the documentation gives the figures.

Each round the roles that read and propose are given the score of every
training task, and may open the traces of a share of those tasks, the pool.
Reading a trace costs calls to the model, which is why the pool is a share.
With `pool_pick: spread` it is taken evenly over the tasks ranked by score
and moves on one place each round, so that every task is read in turn.

`editable` and `approval` together say how much is left to the model. With
`.md` alone a proposal changes only `prompt.md`, the text added to the
agent's instructions. With `.py` as well it may change `agent.py`, the
agent's own code, and that code then runs on the machine that runs the
tasks, as the user who started the experiment, able to reach what that user
can: files, and the keys in `.env`.

Every change is screened for code that reaches past the agent's own work
and is read by a reviewing model before it first runs, and `approval: each`
adds a person, who sees the change and answers yes or no. With code edits
and `approval: none` nobody reads the code first, so the command prints what
that means and starts only when the risk is accepted:

```
mas-optimiser evolve <experiment> --accept-unreviewed-code
```

The screen and the reviewer catch accidents and plain misuse, and the
package is not a sandbox. "The improvement loop" in the documentation says
what each check does.

## Watching an attempt in Langfuse

[Langfuse](https://langfuse.com) shows each attempt as a tree: the task, every
model call with the messages sent and the reply, its tool calls, token usage
and cost, and the check verdict as two scores (`task_success` and
`check_fraction`). Attempts from one evaluation share a session named after the
evaluation folder, and traces are tagged with the domain, mode, arm and model.

Every run goes to the one Langfuse project whose keys are in `.env`, and
runs are told apart inside it:

| Level in Langfuse | What it is here                     | Named or valued                                                 |
|-------------------|-------------------------------------|-----------------------------------------------------------------|
| session           | one evaluation: a run on one domain | the run and the domain, such as `2026-11-16-0808/itsm`          |
| trace             | one attempt at one task             | the task ID                                                     |
| tags              | labels to filter traces by          | the domain, the mode, `arm:default`, the model, `attempt:<n>`   |
| scores            | the verdict, on each trace          | `task_success` (true or false), `check_fraction` (from 0 to 1)  |

To find a run, open the list of sessions and pick the one named after the
run's folder; its traces are that evaluation's attempts. To look across
runs, filter the list of traces by tag (a domain, a mode, a model) or by
score (every attempt whose `task_success` is false, say).

A trace reads from the top down:

- its input is the attempt's settings (the task ID, the attempt number and
  the dataset revision);
- under it comes one entry per model call, in order, with the messages sent
  (the policy and the request first, then the conversation so far), the
  model's reply, the tool calls it asked for, and the tokens and cost of
  that call;
- its output, written when the attempt ends, is the verdict: whether the
  task passed, the benchmark's own verdict, and for every check what it
  expected, what it found and why it failed, followed by the rows the
  attempt changed.

The same verdict, in full, is the attempt's page in the evaluation folder
(`tasks/<task_id>/attempt-<n>.md`); the trace adds what the model was sent
and what it answered at each call.

One command installs and starts a self-hosted copy with Docker, outside this
repository (the stack wants about 16 GiB of memory):

```bash
scripts/langfuse.sh up
```

Used for the first time, the script clones Langfuse into `~/dev/tools/langfuse`, creates an
account for you (your git name and email, a generated password) and the
project, starts the containers, waits for them, and writes the project's
keys into this repository's `.env`. It ends by printing the sign-in details;
`scripts/langfuse.sh login` prints them again and `scripts/langfuse.sh down`
stops the stack. The UI is at http://localhost:3000. The runner traces
whenever the keys are set and the experiment's `trace` is true (the default);
`--no-trace` turns tracing off for one command.

`thinking_summary: true` in an experiment, or `--thinking-summary` on the
command line, asks the model for a summary of its reasoning on each call so
the trace shows it. This changes the agent's behaviour and cost, so use it for
demonstrations only, never for an evaluation whose numbers are reported; `evaluation.json`
records whether it was on.

## Licence

MIT; see `LICENSE`. Third-party material is listed in `NOTICE.md`.
