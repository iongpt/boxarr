"""The FastAPI floor is the same in every dependency file (#151, A10).

src/utils/route_path.py copies Starlette 0.40's get_route_path, and FastAPI
0.115.3 is the first release that requires Starlette 0.40. requirements.txt,
requirements-prod.txt (which the Dockerfile installs) and pyproject.toml must
all say so, or one way of installing Boxarr routes on an older Starlette.
"""

import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

REPO = Path(__file__).parents[2]
FILES = ["requirements.txt", "requirements-prod.txt", "pyproject.toml"]
FLOOR = Version("0.115.3")

# A fastapi requirement at the start of a line, quoted (pyproject.toml) or not.
_FASTAPI = re.compile(r'^\s*"?(?P<req>fastapi(?:\[[^\]]*\])?\s*[<>=!~][^"#\n]*)', re.M)


def _fastapi_requirements(name: str):
    text = (REPO / name).read_text(encoding="utf-8")
    return [Requirement(m.group("req").strip()) for m in _FASTAPI.finditer(text)]


@pytest.mark.parametrize("name", FILES)
def test_each_file_requires_fastapi_once_at_the_floor(name):
    found = _fastapi_requirements(name)

    assert len(found) == 1, (name, found)
    floors = [Version(s.version) for s in found[0].specifier if s.operator == ">="]
    assert floors and min(floors) >= FLOOR, (name, str(found[0].specifier))
    assert not found[0].specifier.contains("0.115.2"), name


def test_the_files_agree():
    specifiers = {str(_fastapi_requirements(name)[0].specifier) for name in FILES}

    assert len(specifiers) == 1, specifiers
