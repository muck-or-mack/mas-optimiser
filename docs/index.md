# mas-optimiser

An LLM agent run on the EnterpriseOps-Gym benchmark, measured properly, with
every failure explained down to the database cell and the turn that set it;
on top of that, a loop that improves the agent's harness, in which a model
proposes changes from the training tasks and each is kept only when a
statistical test supports it on tasks the model never saw. The pages run in the order a
new user needs them: installing and using the package (the repository's
README), how the code works, the methods behind the measurements and
decisions, the data model of each domain and the API.

```{toctree}
:maxdepth: 2
:caption: Using the package

Install, run, read the results <usage>
settings
planner
results
reading-a-decision
watching-an-experiment
gpu-memory
```

```{toctree}
:maxdepth: 2
:caption: How it works

how-it-works
```

```{toctree}
:maxdepth: 2
:caption: Methodology

methodology/fixed-and-optimised
methodology/acceptance-gate
methodology/improvement-loop
methodology/sizing
```

```{toctree}
:maxdepth: 1
:caption: Data model

data-model/itsm
data-model/csm
data-model/hr
```

```{toctree}
:maxdepth: 2
:caption: Reference

api
```
