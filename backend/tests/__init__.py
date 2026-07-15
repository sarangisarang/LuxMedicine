"""Marks tests as a package so cross-module imports resolve.

Without this, `from tests.test_pipeline import ...` works under `python -m pytest` (which
puts the working directory on sys.path) and fails under bare `pytest` (which does not).
CI runs the latter, so the difference hid an ImportError from every local run until CI
caught it.
"""
