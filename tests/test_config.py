from pathlib import Path

import pytest

from kb.config import DEFAULT_SOURCES_FILE, expand, load_sources


def test_expand_plain_and_default():
    env = {"A": "x"}
    assert expand("${A}/y", env) == "x/y"
    assert expand("${B:-def}/y", env) == "def/y"
    assert expand("${B}/y", env) == "/y"
    assert expand("no vars", env) == "no vars"


def test_expand_nested_default():
    env = {"KB_SOURCES_DIR": "/srv"}
    assert expand("${KB_SRC_API:-${KB_SOURCES_DIR}/api}", env) == "/srv/api"
    env["KB_SRC_API"] = "/elsewhere"
    assert expand("${KB_SRC_API:-${KB_SOURCES_DIR}/api}", env) == "/elsewhere"


def test_expand_empty_value_counts_as_unset():
    assert expand("${A:-d}", {"A": ""}) == "d"


def test_expand_rejects_unbalanced():
    with pytest.raises(ValueError):
        expand("${A", {})


def test_sources_yaml_is_valid(monkeypatch):
    monkeypatch.setenv("KB_SOURCES_DIR", "/srv/kb-sources")
    for v in ("KB_SRC_TRAFFICPROJECT", "KB_SRC_API", "KB_SRC_BUS_AIRFLOW"):
        monkeypatch.delenv(v, raising=False)
    sources = load_sources(DEFAULT_SOURCES_FILE)
    assert [s.name for s in sources] == ["trafficproject", "api", "bus-airflow"]
    assert sources[0].path == Path("/srv/kb-sources/TrafficProject")
    assert all(s.github for s in sources)
    assert all(s.include for s in sources)
    # 只索引三個 repo，其他資料夾完全不看
    assert len(sources) == 3


def test_sources_dir_defaults_to_parent_of_kb(monkeypatch):
    monkeypatch.delenv("KB_SOURCES_DIR", raising=False)
    monkeypatch.delenv("KB_SRC_API", raising=False)
    sources = load_sources(DEFAULT_SOURCES_FILE)
    api = next(s for s in sources if s.name == "api")
    assert api.path.name == "api" and api.path.parent.name == "TKR102"
