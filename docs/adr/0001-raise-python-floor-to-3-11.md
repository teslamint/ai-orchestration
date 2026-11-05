# Raise the Minimum Python Version to 3.11

## Status
Accepted, 2026-11-05

## Context
Python 3.10 reached end of life in October 2026. The 17 open Dependabot alerts affect `filelock`; fixed versions (4.2.1+) require Python 3.11 or later, while the Python 3.10 lock branch selects vulnerable `filelock` 4.1.0.

## Decision
Raise the minimum supported Python version to 3.11 and update the dependency lock accordingly. The alternative to keep 3.10 and dismiss the alerts as dev-only was rejected.

## Consequences
Python 3.10 users can no longer install the project. The dependency lock can use fixed `filelock` releases without a Python 3.10 resolution branch.
