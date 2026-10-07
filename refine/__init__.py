"""SENTINEL data-refinement (bring-your-own-data).

Upload a real sales file (CSV/Excel), have SENTINEL VERIFY it (duplicates,
nulls, schema/column issues, row-count) and hand back a REFINED, cleaned file
plus a report of everything it found and fixed.

This reuses SENTINEL's canonical sales schema and the same repair *concepts* as
the agent Fixer (dedupe, drift-rename), applied to user-supplied rows rather
than Faker-generated ones.
"""
from refine.engine import RefineReport, refine_dataframe, refine_file

__all__ = ["RefineReport", "refine_dataframe", "refine_file"]
