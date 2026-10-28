# Check files

One JSON file per task, named by the task id, listing everything the task's
request asks of the final database state. The benchmark's own checks test
only what their authors thought to test; these files add the rest, keep the
originals inside, and record what became of each original, so the
benchmark's verdict is always recoverable from the same file. `checks.py`
loads and validates them. The runner scores the benchmark's checks until the
files are complete.

```json
{
  "task_id": "task_20251210_205620_593_391f1eff_ad65c160",
  "policy_sha1": "c56705c3",
  "requirements": [
    {
      "text": "log an incident for Benjamin's Wi-Fi hardware",
      "kind": "create",
      "table": "incident",
      "anchor": {"incident_id": "INC_024"},
      "cells": {"caller_id": "USER_005", "channel": "chat", "priority": "moderate"},
      "sql": "SELECT COUNT(*) FROM incident WHERE incident_id = 'INC_024' AND caller_id = 'USER_005' AND channel = 'chat' AND priority = 'moderate'",
      "expected": 1,
      "comparison": "equals",
      "source": "Report incident",
      "seed_count": 0,
      "assumptions": ["channel not stated; the policy's default is chat"]
    }
  ],
  "original_checks": {
    "Report incident": {"disposition": "kept"},
    "Report incident (2)": {"disposition": "excluded", "evidence": "a copy of 'Report incident' differing only in channel = phone; the request names no channel and the policy defaults it to chat, so the two cannot both hold"},
    "Add CI": {"disposition": "kept"}
  }
}
```

Fields of a requirement:

- `text`: the sentence of the request it comes from, in the request's words.
- `kind`: `create` (a row that must exist), `change` (cells of an existing
  row that must have changed), `remove` (rows that must be gone: a
  membership, a skill, a link; `expected` is 0 and `comparison` `equals`)
  or `stay` (rows or cells that must not have changed; a stay holds on the
  seed, so its `expected` equals its `seed_count`).
- `table`: the table the requirement reads.
- `anchor` or `selector`: how the rows are found. An anchor is the row's key
  (`{"incident_id": "INC_024"}`; a created row's id is the next in sequence).
  A selector is the WHERE clause that picks out existing rows by description
  (`"assignment_group = 'GROUP_001' AND priority = 'moderate'"`).
- `cells`: column to the value or predicate each cell must satisfy. A plain
  value means equality; a predicate is written as SQL on the column
  (`"LIKE '%reassignment%'"`, `"NOT IN ('resolved', 'closed')"`); two
  conditions on one column are a list of predicates
  (`["LIKE '%call%'", "LIKE '%audio%'"]`), never one compound string. The
  anchor or selector names the rows and `cells` carries every further
  predicate of `sql`; a column the anchor or selector fixes is not repeated
  in `cells`.
- `sql`, `expected`, `comparison`: the count query the cell audit runs, as the
  benchmark's checks are written, so one scorer handles both.
- `source`: the benchmark check this requirement came from, by its exact
  name in `original_checks` (whitespace included); a list of exact names
  when the requirement carries several checks; or `new` when no benchmark
  check tests it. Names are never joined with commas, since check names
  contain commas.
- `seed_count`: what `sql` returns on the seed database, before the agent
  acts; a `create` or `change` requirement already satisfied there is weak.
- `assumptions`: one line per judgement the request left open.

`original_checks` maps each benchmark check, by its unique name, to its
disposition: `kept`, `repaired` (with the evidence that decided it),
`excluded` (undecidable; with the reason) or `weak` (true before the agent
acts). Every kept or repaired check is named in the `source` of at least one
requirement, and no requirement names an excluded one; `checks.validate`
enforces both. `policy_sha1` is the first eight hex digits of the SHA-1 of the task's
policy text, since policies vary within a domain. `notes` (optional) holds
the judgements that concern the file as a whole, including every point on
which the two independent passes that produced it disagreed and how each
was resolved; `minutes` (optional) is the time spent writing it.
