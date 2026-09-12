"""Test package marker.

Not optional. Without it `tests` is only a namespace portion, and any regular
`tests` package that happens to sit in site-packages wins the import — which is
exactly what happened here: `from tests.grid import ...` in tests/grid/run_all.py
raised ModuleNotFoundError, so run_tests.sh could not load Track C at all.
"""
