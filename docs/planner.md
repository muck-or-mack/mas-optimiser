# Experiment planner

<p><a href="_static/planner.html"><strong>Open the experiment planner</strong></a></p>

The planner is one page with sliders that shows, before anything is run,
what an experiment of the improvement loop can show and what it costs. It
runs nothing and needs nothing installed: every figure is worked out in the
browser from the formulas of [Sizing an experiment](methodology/sizing.md).

## What it answers

It works both ways round.

| what is chosen                                                          | what the planner shows                                                             |
|-------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| the split of the tasks and the attempts at each                         | the smallest gain the gate can find, and the confidence interval of the final gain |
| the smallest gain to find and the confidence interval of the final gain | the split that reaches them, and whether there are tasks enough                    |

For either, it shows the runs of the agent, the tokens, the dollars and the
hours of the design, with the cost part by part.

## Its controls

Thirteen sliders set the design: the number of tasks, the shares of the
training and validation sets (or the two targets), the attempts at each
task, the rounds, the candidates in each round, the changes expected to be
kept, the share of the validation tasks in the filter, the share of the
training traces that can be read in a round, the chance of keeping a useless
change, and the chance of finding a gain of the chosen size.

Six of them are settings of an experiment file: `attempts`, `rounds`,
`candidates`, `alpha`, `filter_share` and `pool_share`. The card "This
design as an experiment file" prints them as lines to paste into a copy of
`configs/experiments/template.yaml`, with the command that draws a split of
the chosen shares. The other 23 settings name things, switch things on or
off, or set limits, and have no number to plot; they are listed at the foot
of the planner, and each is explained on
[Experiment settings](settings.md).

## The model's figures

A design's cost depends on the model: the tokens and the dollars of one run
of the agent, the runs that finish in an hour, and how much a task's score
differs between two runs. The planner has these for the models measured
here, each marked as measured, with the number of runs behind it, or as
assumed. Any of them can be typed over, which is how a model not listed is
planned for: run its baseline twice, as "Doing this for another model" on
[Sizing an experiment](methodology/sizing.md) describes, and enter what it
gave.

## What it supposes

The planner supposes that more attempts shrink the spread of a task's
difference by the square root of their number, and it works the smallest
gain out from quantiles of Student's t, which approximates the package's own
calculation. What the measured spreads themselves suppose is under "What the
figures suppose" on [Sizing an experiment](methodology/sizing.md). Its
figures are a plan, not a promise: the cost of an experiment also depends on
how many candidates the filter drops and how many changes are kept, which
only the experiment shows.
