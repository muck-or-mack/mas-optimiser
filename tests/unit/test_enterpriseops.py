import importlib
from pathlib import Path

from mas_optimiser.benchmarks.enterpriseops import GYM_ROOT


def test_benchmark_modules_load_from_the_submodule():
    mcp_client = importlib.import_module("benchmark.mcp_client")
    assert Path(mcp_client.__file__).is_relative_to(GYM_ROOT)
