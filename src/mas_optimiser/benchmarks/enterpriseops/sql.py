"""Take a check's SQL apart: its shape, its tables, its conditions and the column each one tests.

Every question the package asks of a check query is answered here, so that
SQL is read in one place. Two things are kept apart:

- the *structure* (is this a count or a single value, which table is the main
  one, which tables does it read, which column does a condition test) comes
  from sqlglot's syntax tree of the query, read as SQLite;
- the *text* (the FROM clause, each condition, the left side of a condition)
  is cut out of the query as it was written, at the positions of sqlglot's
  tokens. Conditions therefore appear in the outputs in the benchmark's own
  wording, and no rewritten SQL is ever run against an attempt's database.

SQLite reads a double-quoted name as a column when a table in scope has one of
that name and as a string otherwise, where sqlglot always reads a column. The
one place this matters is deciding whether a condition reads a joined table,
and ``parse`` follows SQLite there by looking the name up in the schema's
columns.

Sources: ``docs/how-it-works.md``, section 6, the rows for ``sql``.
"""

import functools

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType

# Clauses after WHERE; a count with one of them is not taken apart.
TAILS = ("group", "having", "order", "limit", "offset")
# The tokens that end a WHERE clause.
ENDS = {
    TokenType.SEMICOLON,
    TokenType.GROUP_BY,
    TokenType.HAVING,
    TokenType.ORDER_BY,
    TokenType.LIMIT,
    TokenType.OFFSET,
}
# The tokens that end the left side of a condition.
OPERATORS = {
    TokenType.EQ,
    TokenType.NEQ,
    TokenType.LT,
    TokenType.LTE,
    TokenType.GT,
    TokenType.GTE,
    TokenType.IS,
    TokenType.IN,
    TokenType.LIKE,
    TokenType.GLOB,
    TokenType.BETWEEN,
    TokenType.NOT,
}


def _tree(query: str) -> exp.Expression | None:
    """The syntax tree of a query that holds one statement.

    Args:
        query: The SQL.

    Returns:
        The tree; ``None`` for SQL that sqlglot cannot read or that holds
        several statements. A comment after the statement does not count as
        one.
    """
    try:
        trees = [
            tree
            for tree in sqlglot.parse(query, read="sqlite")
            if tree is not None and not isinstance(tree, exp.Semicolon)
        ]
    except SqlglotError:
        return None
    return trees[0] if len(trees) == 1 else None


def tables(query: str) -> set[str]:
    """The tables a query reads, in lower case.

    Args:
        query: The SQL.

    Returns:
        The names of the tables anywhere in the query, subqueries included,
        without the names it defines itself in a WITH clause; empty for SQL
        that cannot be read.
    """
    tree = _tree(query)
    if tree is None:
        return set()
    defined = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    return {table.name.lower() for table in tree.find_all(exp.Table)} - defined


def clauses(query: str) -> tuple[str, list[str]] | None:
    """The FROM clause and the top-level conditions of a query, as the query wrote them.

    The WHERE clause is cut at each AND that stands outside brackets, outside
    a CASE and is not the AND of a BETWEEN. It ends at a GROUP BY, HAVING,
    ORDER BY, LIMIT or semicolon, or with the query's last token, so a
    comment after it is left out.

    Args:
        query: The SQL.

    Returns:
        The text between FROM and WHERE and the conditions, each stripped;
        ``None`` for a query with no WHERE of its own, with an OR outside
        brackets (which makes the split meaningless) or that cannot be read.
    """
    try:
        tokens = sqlglot.tokenize(query, read="sqlite")
    except SqlglotError:
        return None
    depth = between = case = 0
    opened = source = start = None
    end = 0
    cuts = []
    for token in tokens:
        kind = token.token_type
        if kind == TokenType.L_PAREN:
            depth += 1
        elif kind == TokenType.R_PAREN:
            depth -= 1
        elif depth == 0 and kind in ENDS:
            break
        elif depth == 0 and start is None:
            if kind == TokenType.FROM and opened is None:
                opened = token.end + 1
            elif kind == TokenType.WHERE and opened is not None:
                source = query[opened : token.start].strip()
                start = token.end + 1
        elif depth == 0 and kind == TokenType.CASE:
            case += 1
        elif depth == 0 and kind == TokenType.END:
            case -= 1
        elif depth == 0 and case == 0 and kind == TokenType.BETWEEN:
            between += 1
        elif depth == 0 and case == 0 and kind == TokenType.OR:
            return None
        elif depth == 0 and case == 0 and kind == TokenType.AND:
            if between:
                between -= 1
            else:
                cuts.append((token.start, token.end + 1))
        end = token.end + 1
    if start is None:
        return None
    pieces = []
    for cut_start, cut_end in cuts:
        pieces.append(query[start:cut_start])
        start = cut_end
    pieces.append(query[start:end])
    return source, [piece.strip() for piece in pieces]


@functools.cache
def column_of(condition: str) -> str:
    """The bare column a condition tests, without alias or function.

    Args:
        condition: One condition of a check, as ``clauses`` cuts them.

    Returns:
        The name of the first column on the condition's left side
        (``status`` for ``i.status = 'new'``, ``notes`` for
        ``COALESCE(notes, '') = ''``); empty for a group of conditions, a
        condition on a subquery alone or text that cannot be read.
    """
    tree = _tree(f"SELECT 1 FROM t WHERE {condition}")
    where = tree.args.get("where") if isinstance(tree, exp.Select) else None
    node = where.this if where is not None else None
    while isinstance(node, (exp.Not, exp.Paren)):
        node = node.this
    if not isinstance(node, exp.Predicate) or isinstance(node, exp.SubqueryPredicate):
        return ""
    column = node.this.find(exp.Column)
    return column.name if column is not None else ""


def parse(query: str, columns: dict[str, list[str]]) -> dict | None:
    """Take a check query apart, if it has one of the two shapes the diagnosis explains.

    The shapes are a count of the rows of a table, perhaps joined to others,
    that meet a list of conditions (``SELECT COUNT(*) FROM t WHERE a AND b``),
    and one column of one table (``SELECT col FROM t WHERE key``).

    Args:
        query: The check's SQL.
        columns: Each table's columns, from the schema.

    Returns:
        For a count: ``kind`` ``"count"``, the main ``table`` and its
        ``alias`` (the table's name when it has none), ``main`` (the main
        table with its alias, to select from it alone), ``source`` (the whole
        FROM clause as written) and ``conditions``, each with its ``text`` as
        written, its ``left`` side as written (``None`` for a group of
        conditions) and ``joined``, whether it reads a table other than the
        main one. For a single value: ``kind`` ``"value"``, ``table`` and
        ``column``. ``None`` for any other query: not a SELECT of one count
        or column, a WITH clause, a FROM that does not open with a table, no
        WHERE, a GROUP BY, HAVING, ORDER BY or LIMIT after it, an OR outside
        brackets, or a value read through a join.
    """
    tree = _tree(query)
    if not isinstance(tree, exp.Select) or tree.args.get("with_"):
        return None
    first = tree.args.get("from_")
    if (
        len(tree.expressions) != 1
        or first is None
        or not isinstance(first.this, exp.Table)
        or tree.args.get("where") is None
    ):
        return None
    target = tree.expressions[0].unalias()
    table = first.this.name
    alias = first.this.alias or table
    joins = tree.args.get("joins") or []
    if isinstance(target, exp.Column):
        if joins:
            return None
        return {"kind": "value", "table": table, "column": target.sql(dialect="sqlite")}
    cut = clauses(query)
    if (
        not isinstance(target, exp.Count)
        or cut is None
        or any(tree.args.get(tail) for tail in TAILS)
    ):
        return None

    # What an unqualified name can belong to: the main table, or a joined one.
    lowered = {
        name.lower(): {c.lower() for c in cols} for name, cols in columns.items()
    }
    own = lowered.get(table.lower(), set())
    others = set()
    for join in joins:
        if isinstance(join.this, exp.Table):
            others |= lowered.get(join.this.name.lower(), set())
    conditions = []
    for text in cut[1]:
        # The left side as written, for a condition that tests a column:
        # what stands before its first operator outside brackets, after a
        # leading NOT. A CASE has operators of its own and is left whole.
        left, depth, begin = None, 0, 0
        for token in sqlglot.tokenize(text, read="sqlite"):
            if token.token_type == TokenType.L_PAREN:
                depth += 1
            elif token.token_type == TokenType.R_PAREN:
                depth -= 1
            elif token.start == 0 and token.token_type == TokenType.NOT:
                begin = token.end + 1
            elif depth == 0 and token.token_type == TokenType.CASE:
                break
            elif depth == 0 and token.token_type in OPERATORS:
                left = text[begin : token.start].strip() or None
                break
        if not column_of(text):
            left = None
        # A condition reads a joined table when it names another table or
        # alias, or a column that only a joined table has. A name inside a
        # subquery of the condition belongs to that subquery.
        joined = False
        wrapper = _tree(f"SELECT 1 FROM t WHERE {text}") if joins else None
        if joins and wrapper is None:
            return None
        if wrapper is not None:
            where = wrapper.args["where"]
            inner = {t.alias_or_name.lower() for t in where.find_all(exp.Table)}
            for column in where.find_all(exp.Column):
                nested = column.find_ancestor(exp.Select) is not wrapper
                prefix = column.table.lower()
                name = column.name.lower()
                if prefix:
                    if prefix not in {table.lower(), alias.lower()} and not (
                        nested and prefix in inner
                    ):
                        joined = True
                elif not nested and name not in own and name in others:
                    joined = True
        conditions.append({"text": text, "left": left, "joined": joined})
    return {
        "kind": "count",
        "table": table,
        "alias": alias,
        "main": f"{first.this.this.sql(dialect='sqlite')} {alias}",
        "source": cut[0],
        "conditions": conditions,
    }
