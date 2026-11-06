# ITSM data model

1 tables, from `data/schemas/itsm.sql`, the schema the server builds
each attempt's database from. Written by `mas-optimiser schema`; do not edit.

| table | columns | references |
|---|---|---|
| [`incident`](#incident) | 3 |  |

## incident

Key: `incident_id`.

| column | type |
|---|---|
| `incident_id` | VARCHAR(32) |
| `priority` | VARCHAR(8) |
| `cost` | INTEGER |
