"""Deliberate formatting violations to exercise the lint-autofix heal job."""

import os
import sys


def f(a, b):
    return {"k": a, "k2": b}
