"""Code domain plugin — semantic version control for source code.

Treats code as a structured, semantic system rather than text files.
The unit of change is a *symbol* (function, class, method, variable) —
not a line. Two commits that reformat a file without changing semantics
produce identical symbol content IDs and therefore no delta.

Language support
----------------
- Python (*.py, *.pyi): Full AST-based symbol extraction via stdlib ``ast``.
- All other files: file-level tracking with raw-bytes identity.

Extending the language support
-------------------------------
Implement :class:`~muse.plugins.code.ast_parser.LanguageAdapter` and
register the instance in :data:`~muse.plugins.code.ast_parser.ADAPTERS`.
"""
