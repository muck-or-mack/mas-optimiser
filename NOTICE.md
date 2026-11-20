# Third-party material

## EnterpriseOps-Gym

`vendor/enterpriseops-gym` is a git submodule of
[ServiceNow/EnterpriseOps-Gym](https://github.com/ServiceNow/EnterpriseOps-Gym),
pinned at commit `271f2c3` and used unmodified. It is distributed under the
Apache License 2.0; see `vendor/enterpriseops-gym/LICENSE`.

The domain servers in `docker/compose.yaml` are the Docker Hub images the
benchmark's authors publish (`shivakrishnareddyma225/enterpriseops-gym-mcp-*`);
they are pulled at run time, not redistributed here.

> Malay et al., *EnterpriseOps-Gym: Environments and Evaluations for Stateful
> Agentic Planning and Tool Use in Enterprise Settings*, arXiv:2603.13594, 2026.

Two functions of this package reproduce code from that repository rather
than import it, under the same licence:

- `checks.compare` follows `benchmark/verifier.py`, `_compare_values`,
  branch for branch, so that a verdict computed here matches the executor's.
- `seeds.server_database` follows `benchmark/mcp_client.py`,
  `create_database_from_file`: the same endpoint, payload and id format,
  rewritten as a context manager that deletes the database afterwards.

## RRSI

`vendor/rrsi` is the code released with

> Xia et al., *Regularized Recursive Self-Improvement of Agent Harnesses*,
> arXiv:2609.24972, 2026.

([google-research/rrsi](https://github.com/google-research/rrsi)), at commit
`be50316` and used unmodified. It is distributed under the Apache License
2.0; see `vendor/rrsi/LICENSE`. The improvement loop (`mas_optimiser.evolve`)
calls its analysis, proposer, review, record and git modules, and puts its
own round, acceptance rule and model call in place of RRSI's.

## EnterpriseOps-Gym, copied

One file outside the package is a modified copy from that repository, under
the same licence:

- `harnesses/default/agent.py` is `orchestrators/react.py` with the changes
  its opening lines state: the class renamed, its base class imported by
  its full name, and the text of `prompt.md` added to the system message.
