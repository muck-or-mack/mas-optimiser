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
