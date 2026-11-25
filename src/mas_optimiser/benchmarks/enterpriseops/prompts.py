"""What the loop's search roles are told about this benchmark and its harness.

RRSI's analyst, digester, proposer and critic each take a paragraph about
the benchmark, and the proposer also takes a list of rules and a list of the
kinds of change a harness can express (``domain.EnterpriseOps`` hands them
over). The texts say how the benchmark and the harness folder work and what
a proposal may not contain. They say nothing about what goes wrong on the
tasks or how to put it right: that is for the proposer to find in the
training traces, and anything a person has learnt by reading tasks stays out
of this file.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``evolve``.
"""

ANALYST = """The agent works one request to an enterprise service desk (IT
service management, customer service or HR). The agent's model is frozen. It
is given the task's policy document as its system message, the request as
the user message, and the tools of the system of record as function calls,
and it calls tools until it answers without one. A task has checks, hidden
from the agent: queries against the final database. An attempt passes when
every check holds. The trace is the attempt's page: the request, then each
turn with the agent's words, each tool call with its arguments and what came
back, then the rows the attempt changed, then every check with its verdict
and, for a failed check, a diagnosis of what was missing or wrong and the
turn it came from."""

DIGESTER = """The trajectory is the page of one attempt by a service-desk
agent: a frozen model working one request with function-call tools. Each
<task_id>.txt holds the request, every turn ([step N] prefix: the agent's
words, each tool call with its arguments and its result), the rows the
attempt changed, and at the end the VERIFIER section: every check with its
verdict and a diagnosis of each failure. Read the VERIFIER section first,
then find in the turns where the attempt went wrong."""

PROPOSER = """The benchmark is EnterpriseOps-Gym: requests to an enterprise
service desk, worked by a frozen model through a loop of tool calls. The
harness you edit is a folder of two files. agent.py is the whole agent: one
class whose execute method sends the policy document and the request to the
model, runs the tool calls the model asks for, feeds the answers back, and
repeats until the model answers without a tool call. prompt.md is text that
agent.py adds to the end of the system message; while it is empty nothing is
added. The file exists from the start, so write_file refuses it: to put text
into it while it is empty, use edit_file with an empty old string. You may
edit files of these types only: {editable}. The tools, the tasks, the checks
and the scoring are outside the folder and cannot be changed. A change must
be general: it has to help on requests and records its author has never
seen. The agent's model is not you: read the traces for how it actually
behaves."""

# Added to the proposer's paragraph when it is told how its earlier proposals
# did on the training tasks (``feedback: training``).
FEEDBACK = """Each earlier proposal in the edit history carries its outcome,
which was decided on tasks you cannot see, and three numbers from the
training tasks, which you can: delta_S_training is its score on them less
the current harness's at the time, as a fraction of checks passed;
standard_error is the chance in that difference, so a difference within
about two standard errors of zero is no evidence either way;
delta_C_training is the relative change in tokens an attempt used. The
attribution scoreboard says, for the tasks an edit predicted it would move,
which of them did move on the training tasks."""

CRITIC = """The harness is a folder of two files: agent.py, the agent's loop
of model calls and tool calls, and prompt.md, text added to the end of the
system message. A general way of working is acceptable. Anything taken from
a particular task is not: a record number, an id, the name of a person, a
group or a service, a value copied from a trace. Any reference to the
checks, the verifier, the scoring or the tables they query is gaming the
grader. Code that reads files outside the folder, reads the environment, or
reaches the network other than through the model and tool clients it is
given is to be rejected. Text in prompt.md is read on every model call, so a
long text is a cost that the loop measures."""

RULES = """# Rules for a change to the harness

The harness is the folder you are working in: agent.py, the agent's loop,
and prompt.md, text added to the end of the system message.

Hard rules:

1. Nothing from a particular task: no record numbers, ids, names of people,
   groups or services, and no values copied from the tasks or the traces. A
   change must make sense for a service desk its author has never seen.
2. No reference to the checks, the verifier, the scoring or the tables they
   query. The agent must do the work, not satisfy a grader.
3. The record stays as it is: execute must return the same keys, and the
   entries of conversation_flow must keep their types and fields, since
   everything that reads an attempt depends on them.
4. Stay inside the folder: no reading of other files or of the environment,
   and no network access other than through the model and tool clients the
   agent is given.
5. Text for the agent is plain English, addressed to the agent.
"""

PATTERNS = """# What a change to the harness can be (a reference, not a limit)

1. Text for the agent (prompt.md): how to work a request in general.
2. What the model is shown (agent.py): which tools are offered and how they
   are described.
3. What is sent to a tool (agent.py): a check or a repair of a call's
   arguments before the call is made.
4. What comes back (agent.py): how a tool's answer or its error is passed on
   to the model.
5. The loop (agent.py): when to go on, when to stop, how many turns.

A change that adds text or a call on every turn adds its cost on every turn.
"""
