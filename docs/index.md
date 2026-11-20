# mas-optimiser

An LLM agent run on the EnterpriseOps-Gym benchmark, measured properly, with
every failure explained down to the database cell and the turn that set it;
on top of that, a ladder of harnesses and an optimiser that improves the
best of them by editing its planning layer. The pages run in the order a
new user needs them: installing and using the package (the repository's
README), how the code works, the methods behind the measurements and
decisions, the data model of each domain and the API.

```{toctree}
:maxdepth: 2
:caption: Using the package

Install, run, read the results <usage>
results
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
