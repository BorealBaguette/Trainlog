from pathlib import Path

from src.sql import SqlTemplate

# Load CTE templates
CTE_DIR = Path(__file__).parent / "cte"
CTES = {}

for cte_file in CTE_DIR.glob("*.sql"):
    cte_name = cte_file.stem
    with open(cte_file, "r") as f:
        content = f.read()
        # Wrap in CTE format
        CTES[cte_name] = f", {cte_name} AS (\n{content}\n)"

# Base filter is always first (no comma prefix)
if "base_filter" in CTES:
    CTES["base_filter"] = (
        f"WITH base_filter AS (\n{open(CTE_DIR / 'base_filter.sql').read()}\n)"
    )


class ComposedSqlTemplate:
    """SQL template that composes CTEs dynamically"""

    def __init__(self, query_file, required_ctes=None, fragments=None):
        self.query_file = Path(query_file)
        self.required_ctes = required_ctes or []
        # Literal SQL substituted into the template at import time (column names,
        # aliases). These are never request data — see stats_by_column.sql.
        self.fragments = fragments or {}

        with open(self.query_file, "r") as f:
            self.query_template = f.read()

    def __call__(self):
        """Build the complete query with required CTEs"""
        from sqlalchemy import text

        # Build CTE chain
        cte_sql = ""
        for cte_name in self.required_ctes:
            if cte_name in CTES:
                cte_sql += CTES[cte_name]

        # Replace placeholders in query template
        query = self.query_template
        for cte_name in self.required_ctes:
            query = query.replace(f"{{{cte_name}}}", "")
        for name, sql in self.fragments.items():
            query = query.replace(f"{{{name}}}", sql)

        # Combine CTEs with query
        full_query = cte_sql + "\n\n" + query

        return text(full_query)


def _column_stats(column, alias, group_expr=None, extra_select="", extra_where=""):
    """Build a stats query grouping trips by a single text column of `trips`."""
    return ComposedSqlTemplate(
        "src/sql/stats/stats_by_column.sql",
        ["base_filter", "time_categories"],
        fragments={
            "column": column,
            "alias": alias,
            "group_expr": group_expr or f"TRIM({column})",
            "extra_select": extra_select,
            "extra_where": extra_where,
        },
    )


# Define combined queries (both trips and km in one query)
stats_operator = ComposedSqlTemplate(
    "src/sql/stats/stats_operator.sql",
    ["base_filter", "time_categories", "split_operators"],
)

stats_material = ComposedSqlTemplate(
    "src/sql/stats/stats_material.sql",
    ["base_filter", "time_categories", "split_material"],
)

stats_countries = ComposedSqlTemplate(
    "src/sql/stats/stats_countries.sql", ["base_filter", "time_categories"]
)

stats_routes = ComposedSqlTemplate(
    "src/sql/stats/stats_routes.sql", ["base_filter", "time_categories"]
)

stats_stations = ComposedSqlTemplate(
    "src/sql/stats/stats_stations.sql", ["base_filter", "time_categories"]
)

stats_year = ComposedSqlTemplate(
    "src/sql/stats/stats_year.sql", ["base_filter", "time_categories"]
)

stats_months = ComposedSqlTemplate(
    "src/sql/stats/stats_months.sql", ["base_filter", "time_categories"]
)

# Single-column dimensions. `line_name` is free text and is only trimmed — folding
# case there would merge line codes that users spell deliberately ("IC" / "ic" are
# both meant). Registrations are uppercased because they are a code, not a name.
#
# The mode() extras give each row something to show besides its name: the operator
# a line mostly runs under (89% of train lines only ever have one), and the
# aircraft type a tail number mostly flies as (97% of registrations only have
# one). mode() ignores NULLs, and NULLIF keeps blanks from winning.
stats_lines = ComposedSqlTemplate(
    "src/sql/stats/stats_lines.sql", ["base_filter", "time_categories"]
)
# Ferries group by the HULL, under its current name — one bar per ship, however the
# trips were logged and whatever the ship was called at the time. `reg` holds the hull
# key, so a crossing from before a rename lands on the same bar as one from after it,
# and the label is what the ship is called now.
#
# The NULL date is what asks for "now": vessel_display_name resolves at a moment, and no
# moment means the current identity (migration 0056). Passing each trip's own date would
# split a renamed ship into two bars, which is the opposite of grouping by hull.
#
# TRUE folds the case of anything it does not recognise, exactly as the registration
# grouping below does.
stats_vessels = _column_stats(
    "reg",
    "vehicle",
    group_expr="vessel_display_name(reg, NULL, TRUE)",
)

stats_vehicles = _column_stats(
    "reg",
    "vehicle",
    group_expr="UPPER(TRIM(reg))",
    # Only for flights: for a bus this would be a model name ("ADL Enviro200"),
    # which is not a code, has no artwork, and would ride along on every row.
    extra_select=(
        "CASE WHEN :tripType IN ('air', 'helicopter')"
        " THEN MODE() WITHIN GROUP (ORDER BY NULLIF(TRIM(material_type), ''))"
        " END AS image_code,"
    ),
)

# material_type_advanced holds either a saved trainset's name or an inline list
# of units; either way it groups as-is and gets resolved to wagons in Python
# (attach_trainset_units), which is the only place that can batch the lookups.
stats_trainsets = _column_stats("material_type_advanced", "trainset")

# Simple queries without CTEs
type_available = SqlTemplate("src/sql/stats/type_available.sql")
airport_cities = SqlTemplate("src/sql/stats/airport_cities.sql")
distinct_stat_years = SqlTemplate("src/sql/stats/distinct_stat_years.sql")
public_stats = SqlTemplate("src/sql/stats/public_stats.sql")
