# Comparison: candidate 2026-11-03-0803/itsm, 2026-11-03-0803/csm, 2026-11-03-0803/hr against current 2026-10-27-0528/itsm, 2026-10-27-0528/csm, 2026-10-27-0528/hr

Candidate (second) against current (first) on the validation tasks of split haiku-20-40-40: 125 tasks. Scores are per-task means over attempts; a point is a hundredth. Decisions at level 0.05, one-sided.

## Fraction of checks passed (the gate's outcome)

| set    | tasks | current | candidate | gain (points) | SE (points) | T     | X0    | p     | verdict | detectable (points) | sign test (up/down, p) | permutation p | bootstrap SE (points) | lower bound (points) | Bernstein bound (points) |
|--------|-------|---------|-----------|---------------|-------------|-------|-------|-------|---------|---------------------|------------------------|---------------|-----------------------|----------------------|--------------------------|
| pooled | 125   | 0.522   | 0.529     | +0.71         | 1.36        | +0.52 | 1.645 | 0.300 | reject  | 3.4                 | 32/27, 0.301           | 0.304         | 1.37                  | -1.52                | -8.37                    |
| csm    | 42    | 0.527   | 0.557     | +2.95         | 2.60        | +1.14 | 1.645 | 0.128 | reject  | 6.5                 | 13/8, 0.192            | 0.130         | 2.57                  | -1.32                | -20.44                   |
| hr     | 41    | 0.556   | 0.550     | -0.62         | 2.15        | -0.29 | 1.645 | 0.613 | reject  | 5.4                 | 10/10, 0.588           | 0.602         | 2.12                  | -4.16                | -23.48                   |
| itsm   | 42    | 0.484   | 0.482     | -0.22         | 2.31        | -0.09 | 1.645 | 0.538 | reject  | 5.7                 | 9/9, 0.593             | 0.527         | 2.28                  | -4.02                | -22.95                   |

## Task success

| set    | tasks | current | candidate | gain (points) | SE (points) | T     | X0    | p     | verdict | detectable (points) | sign test (up/down, p) | permutation p | bootstrap SE (points) | lower bound (points) | Bernstein bound (points) |
|--------|-------|---------|-----------|---------------|-------------|-------|-------|-------|---------|---------------------|------------------------|---------------|-----------------------|----------------------|--------------------------|
| pooled | 125   | 0.205   | 0.227     | +2.13         | 1.96        | +1.09 | 1.645 | 0.138 | reject  | 4.9                 | 17/13, 0.292           | 0.137         | 1.97                  | -1.09                | -8.41                    |
| csm    | 42    | 0.214   | 0.270     | +5.56         | 3.75        | +1.48 | 1.645 | 0.069 | reject  | 9.3                 | 10/4, 0.090            | 0.087         | 3.68                  | -0.62                | -20.42                   |
| hr     | 41    | 0.187   | 0.187     | +0.00         | 3.08        | +0.00 | 1.645 | 0.500 | reject  | 7.7                 | 3/5, 0.855             | 0.514         | 3.01                  | -5.07                | -24.74                   |
| itsm   | 42    | 0.214   | 0.222     | +0.79         | 3.31        | +0.24 | 1.645 | 0.405 | reject  | 8.2                 | 4/4, 0.637             | 0.412         | 3.26                  | -4.65                | -24.02                   |

## Decision rules on the pooled fraction of checks passed

| rule                                                  | level  | X0   | verdict | detectable (points) |
|-------------------------------------------------------|--------|------|---------|---------------------|
| level as given (0.05)                                 | 0.05   | 1.64 | reject  | 3.4                 |
| fixed 3 sigma                                         | 0.0013 | 3.00 | reject  | 5.2                 |
| equal split over 20 decisions                         | 0.0025 | 2.81 | reject  | 5.0                 |
| REUSE allocation, round 1, 0 promotions, 1 candidates | 0.013  | 2.24 | reject  | 4.2                 |

## Figures

- `figures/threshold.pdf`
- `figures/power.pdf`
- `figures/differences.pdf`
- `figures/domains.pdf`
