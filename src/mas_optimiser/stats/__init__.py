"""Statistics for comparing two harnesses on the same tasks.

``outcomes`` scores each task in two evaluations and pairs the scores task by
task; ``gate`` tests whether the mean paired difference is a gain and reports
the smallest gain the data can detect; ``budget`` sets the error level of
each decision in a run so that the run's overall error stays within a fixed
budget; ``figures`` draws the comparison with the plotted numbers saved beside
each figure; ``compare`` is the command that runs all of it on two evaluations.

The error budget and the bounded-score confidence bound follow Sun et al.,
"Which Self-Improvements Should We Trust?" (arXiv 2609.33180); the rest is the
standard normal theory of a paired comparison.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``stats``.
"""
