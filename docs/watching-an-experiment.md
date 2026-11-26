# Watching an experiment

An experiment of the loop takes hours or days, and most of that time is
spent waiting for the model's service. `status` answers, from another
terminal, the questions that come up while it goes: where it is, whether it
is moving, and whether the service is holding it back.

```
mas-optimiser status evolutions/<experiment>/<split>
```

It reads the files the experiment has written and asks each of the
experiment's models one word. Nothing is run and nothing of the experiment
is changed. It prints counts and totals only: no task is named, so it can
be read while the validation and test tasks are in use.

## What it prints

The example is the made-up experiment of the unit tests
(`tests/unit/test_status.py`), not a result.

```
| experiment             | an-experiment on a-split (first)                                               |
|------------------------|--------------------------------------------------------------------------------|
| model                  | a-model                                                                        |
| code version           | v0                                                                             |
| where it is            | the starting harness, validation tasks                                         |
| rounds                 | 0 of 5 decided; changes kept: 0                                                |
| this job               | 4 of 6 attempts                                                                |
| attempts finished      | 3 in the last 10 minutes, 3 in the last hour                                   |
| time left for this job | 0 h 40 min at that pace                                                        |
| the rest, at the least | 11 h 40 min (35 attempts): every candidate still to come dropped at the filter |
| the rest, at the most  | 28 h 20 min (85 attempts): every candidate still to come kept                  |
| last file written      | less than a minute ago                                                         |
| in the last hour       | ended in an error: 1; unanswered by a service: 0; calls by the search roles: 1 |
| its traffic            | 167 tokens a minute over the last hour, 17% of the 1,000 its service allows    |

| model   | answers   | seconds   | tokens a minute   | left   | requests a minute   | left   | what was raised   |
|---------|-----------|-----------|-------------------|--------|---------------------|--------|-------------------|
| a-model | yes       | 1.4       | 1,000             | 640    |                     |        |                   |
```

| line                   | what it says                                                                                                                                                   | read from                                                                             |
|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| where it is            | the job its newest finished attempt belongs to, in the report's words; or that the test has run                                                                | the job folders                                                                       |
| rounds                 | the rounds decided, of those the experiment is to have, and the changes kept so far                                                                            | `frontier.json`                                                                       |
| this job               | that job's finished attempts, of the number it is to have                                                                                                      | the attempts' records; the total from the job's `job.json`                            |
| attempts finished      | over every job of the experiment, in the last 10 minutes and the last hour; for an experiment whose command was given less than an hour ago, since then        | the times of the attempts' records                                                    |
| time left for this job | the attempts it still lacks, at the pace of the line above                                                                                                     | the two lines above                                                                   |
| the rest, at the least | the time for every attempt still to run, at that pace, if every candidate still to come is dropped at the filter (or, with no filter, turned down at the gate) | the experiment's settings and its sets' sizes in `experiment.json`; the attempts done |
| the rest, at the most  | the same if every candidate still to come is kept: each is then run on every validation task and adds a job on the test tasks                                  | the same                                                                              |
| last file written      | how long ago the experiment wrote anything, the harness's own git folders aside                                                                                | the experiment's folder                                                               |
| in the last hour       | of the attempts in that time, those that ended in an error and those a service left unanswered; and the calls of the search roles                              | the attempts' records; `search_usage.jsonl`                                           |
| its traffic            | the tokens of the calls answered in that time, a minute, and their share of what the service says it allows                                                    | the same records; the limit from the model's answer just below                        |
| the second table       | each of the experiment's models asked one word: whether it answered, and the allowance its service states                                                      | the reply's headers, as for `mas-optimiser status <model>`                            |

A job started by a version before 0.18 left no `job.json`: its attempts are
shown without a total, and its time left is not known. The two lines for the
rest of the experiment count attempts only: the search roles' own time, some
minutes a round, is not in them, and an experiment started before its sets'
sizes were recorded shows them as not known.

The further attempts of a second look (the setting `second_look`) are in
neither figure for the rest: whether a candidate will miss narrowly cannot
be known before it is tested.

## Three questions to read it for

| question                        | read                                                           | what it means                                                                                                                                                                                                          |
|---------------------------------|----------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Is it moving?                   | last file written; attempts finished                           | A last file some minutes old with calls by the search roles is a round being thought out, not a stop. A last file an hour old with nothing finished is an experiment that has stopped, or one waiting for its service. |
| Is the service holding it back? | the second table's first `left`; its traffic                   | `left` near or below zero with the experiment's traffic well under the limit: the allowance is being used up by something else. `left` large: the service has room, and the pace is set by `concurrency`.              |
| How long will it take?          | time left for this job; the rest, at the least and at the most | The first is for the job it is at. The other two bound the whole of what is left: how many candidates pass the filter and are kept is not known in advance, and a kept candidate costs several times a dropped one.    |

## What it cannot tell

- **Who else is using the allowance.** A service says what is left, not
  who took it.
- **The service's own count.** A service counts tokens against its limit by
  its own rule. The traffic shown is what answered calls used in attempts
  that have finished and in the search roles' calls; an attempt still going
  is not in it yet. The share shown is therefore a floor.
- **Whether the experiment's process is alive.** `status` reads files. It
  says how long ago the last one was written and leaves the judgement to
  the reader.
- **Calls a service refused and the waits that followed.** The model
  client sends a refused call again by itself, and neither the refusal nor
  the wait is recorded yet.

## A model by itself

```
mas-optimiser status gpt-5.1-azure
```

asks the model one word and prints the second table alone: whether it
answered and the allowance its service states. One row is one instant, so
ask a few times before reading anything into `left`. Given a model, the
command ends with an error when the model does not answer; given an
experiment, a silent model is reported in the table and no more.

The numbers are worked out in [`status.progress`][status.progress], and the
question is asked in [`status.ask`][status.ask].

[status.progress]: #mas_optimiser.status.progress
[status.ask]: #mas_optimiser.status.ask
