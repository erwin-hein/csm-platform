"""Business logic. Kept independent of the rendering layer (CLAUDE.md §2 row 12) so a
JSON API or a richer client can sit on top later without a rewrite.

Conventions: every function takes the session first and the acting user second;
nothing here commits; every mutating function is decorated with @mutation.
"""
