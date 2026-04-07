"""Compatibility package for importing the project as `epet.*`.

This keeps the existing top-level module layout working while also supporting
older tests and tooling that expect a package named `epet`.
"""

from importlib import import_module
import sys


def _alias_package(alias: str, target: str):
    module = import_module(target)
    sys.modules[alias] = module
    return module


core = _alias_package("epet.core", "core")
plugins = _alias_package("epet.plugins", "plugins")
simulator = _alias_package("epet.simulator", "simulator")

