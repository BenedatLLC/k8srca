"""dkgg's packaging: what an editable install cannot check."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_the_query_template_ships_with_the_package():
    """arch_query.py is copied into every skill; it must be in the wheel."""
    template = ROOT / "src" / "dkgg" / "templates" / "arch_query.py"
    assert template.exists()
    wheel = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert any(template.is_relative_to(ROOT / p) for p in wheel["packages"])


def test_the_version_is_stated_once():
    import dkgg

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == dkgg.__version__
