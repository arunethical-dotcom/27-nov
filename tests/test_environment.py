import importlib.metadata
from pathlib import Path
import re


def test_installed_dependencies_match_pins():
    pins = Path("requirements.txt").read_text().splitlines()
    for pin in pins:
        name, version = pin.split("==")
        assert importlib.metadata.version(name) == version
    assert re.search(r"Python 3\.11\.\d+", Path("README.md").read_text())
