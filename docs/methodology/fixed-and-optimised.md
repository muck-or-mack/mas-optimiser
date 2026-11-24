# What is fixed and what is optimised

An experiment here compares harnesses. A comparison means something only
if everything else is the same on both sides. This page lists what every
arm of an experiment shares and where each item is set, names the one thing
that differs, and says who may change which. An *arm* is one harness under
test; the baseline is an arm.

## Fixed for every arm

| what                         | value                                                                                                                              | where it is set                           |
|------------------------------|------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------|
| The tasks                    | the benchmark's ITSM, CSM and HR tasks at a pinned revision of the dataset, with the benchmark's corrected copies where they exist | `runner.HF_REVISION`, `runner.load_tasks` |
| The domain servers           | the benchmark's server images, pinned by digest, with a fresh database for every attempt                                           | `docker/compose.yaml`                     |
| The tools the agent is given | every tool the domain's server offers: 93 for ITSM, 89 for CSM, 89 for HR                                                          | `mode` in the experiment file             |
| The checks                   | the benchmark's SQL checks with three corrections, described in "How it works", section 2.5                                        | `checks.py`, `data/check_patches.json`    |
| The model                    | one model and its settings for every arm                                                                                           | the model file in `configs/models/`       |
| Prompt caching               | on, for a provider it is built for; it changes the cost of repeated input and nothing the model receives                           | `prompt_caching` in the experiment file   |
| Time limit on a model call   | 300 seconds for each try, after which the try is repeated; it changes nothing the model receives                                   | `model_timeout` in the experiment file    |
| Tries of a failed model call | two more by the provider's library, or the model file's number; each waits as long as the service asks                             | `max_retries` in the model file           |
| Limits on an attempt         | 3,600 seconds and 100 calls to the model, kept outside the harness; an attempt past either is an error                             | `attempt_timeout`, `attempt_calls`        |
| Attempts per task            | the same number for every arm                                                                                                      | `attempts` in the experiment file         |
| The task sets                | one split into training, validation and test, drawn once                                                                           | the split file in `configs/splits/`       |
| The decision rule            | the test and its level                                                                                                             | [the acceptance gate](acceptance-gate.md) |

`evaluation.json` records the mode, the model and its settings, prompt
caching, the time limit, the attempts, the split and the dataset's revision
for the evaluation it belongs to, so two evaluations can be checked against
each other before they are compared.

## The one thing that differs

The harness: the code around the model that turns a task into tool calls.
Here that is the agent loop, the text it adds to the task's policy and
request, the way it chooses and presents tools, and what it does with their
answers. In the repository a harness is a folder, `harnesses/<name>/`:
`agent.py` holds the loop and `prompt.md` the text it adds to a task's
system message. The baseline's harness is `harnesses/default/`, a copy of
the benchmark's own loop with nothing added. A candidate is a changed copy
of that folder, and may change nothing in the table above.

## Two kinds of change

A defect found while running experiments leads to one of two kinds of
change, and the kind decides who may make it.

|                  | a fix for every arm                                                                                                 | a harness change                                                             |
|------------------|---------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| who gets it      | every arm, the baseline included                                                                                    | the candidate only                                                           |
| what it corrects | the measure or the environment                                                                                      | how the agent works                                                          |
| who makes it     | a person, by hand                                                                                                   | the optimiser                                                                |
| what it rests on | evidence that is no harness's result: the benchmark's own files, a server's behaviour, the provider's documentation | the candidate's score on the validation tasks, through the acceptance gate   |
| examples         | the patch list; prompt caching; a time limit on calls to the model; giving every arm all the tools                  | choosing which tools to show the model; limiting the size of a tool's answer |

A harness change is not made by hand, however plain it looks. An attempt
that calls a lookup with no filter can be answered with a whole table, about
a hundred thousand tokens, which every later call of the attempt then
carries. A limit on the size of an answer would save that cost. It would
also change what the agent reads, so it is for the optimiser to propose and
for the gate to judge.

A fix for every arm must not come from reading the tasks a harness is
judged on, since whoever reads them learns what a better harness would do
with them. A fix that needs such reading is made by code that applies one
rule to every task, or by someone who takes no part in designing harnesses.

## Why every arm is given all the tools

The benchmark lists with each task the tools it needs, between 2 and 19 of
them, and reports its results with that list: "we assume a perfect retriever
supplies the agent with the right set of tools" (EnterpriseOps-Gym,
arXiv:2603.13594, p. 6). StarHarness is evaluated the same way, on the 103
ITSM tasks with their own tools (arXiv:2608.24804, p. 5). Here every arm is
given every tool of its domain's server, for two reasons.

- The list is information that only someone who has solved the task could
  give. The benchmark's paper itself treats a tool's presence in the list
  as a signal for planning (p. 30). A harness can learn to lean on that, and
  a gain made that way would not hold where no such list exists.
- With all tools given, choosing among them is part of the harness, so it
  is something the optimiser can improve, for score and for cost.

The price is cost and comparison. All tools sends about three and a half
times the input in each call. Prompt caching takes that back: on training
tasks the same calls cost 82% less with it, which puts an attempt with all
tools below the cost of one with the task's own tools and no caching. The
main numbers also no longer sit beside the published ones, so the final
harness and the baseline are each run once more with the tasks' own tools
(`--mode oracle`), and that pair is the one to set against the literature.
