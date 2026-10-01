from kb.loaders import fs, git
from kb.loaders.gitutil import glob_to_regex

from .conftest import commit_all, make_source


def test_glob_semantics():
    assert glob_to_regex("*.md").match("README.md")
    assert not glob_to_regex("*.md").match("docs/x.md")
    assert glob_to_regex("src/**/*.py").match("src/a/b/c.py")
    assert glob_to_regex("src/**/*.py").match("src/c.py")
    assert glob_to_regex("**/Archive.zip").match("Archive.zip")
    assert glob_to_regex("**/Archive.zip").match("a/b/Archive.zip")
    assert glob_to_regex("raw/**").match("raw/x/y.gz")


def test_fs_loads_tracked_files_at_head_only(temp_repo):
    src = make_source(temp_repo)
    sha, docs = fs.load(src)
    paths = {d.path for d in docs}
    assert paths == {"README.md", "pipe.py", "schema.sql"}        # .env 被 gitignore，根本不在 HEAD 裡
    assert all(d.git_sha == sha for d in docs)
    readme = next(d for d in docs if d.path == "README.md")
    assert readme.kind == "doc" and readme.acl_level == 1
    assert next(d for d in docs if d.path == "pipe.py").acl_level == 2

    # 工作目錄改了但沒 commit → 讀到的仍是 HEAD 的內容
    (temp_repo / "README.md").write_text("# 改了但沒 commit\n", encoding="utf-8")
    _, docs2 = fs.load(src)
    assert next(d for d in docs2 if d.path == "README.md").text == readme.text


def test_fs_exclude_wins(temp_repo):
    src = make_source(temp_repo)
    src.exclude = ["*.sql"]
    _, docs = fs.load(src)
    assert {d.path for d in docs} == {"README.md", "pipe.py"}


def test_git_commits_become_documents(temp_repo):
    (temp_repo / "pipe.py").write_text("x = 1\n", encoding="utf-8")
    sha2 = commit_all(temp_repo, "修 pipe\n\n理由寫在這裡。")
    src = make_source(temp_repo)
    docs = git.load(src)
    assert len(docs) == 2
    latest = docs[0]
    assert latest.path == f"commits/{sha2}" and latest.kind == "commit" and latest.git_sha == sha2
    assert latest.title == "修 pipe"
    assert "理由寫在這裡。" in latest.text
    assert "pipe.py" in latest.text and "diff --git" in latest.text
    assert "+x = 1" in latest.text


def test_git_diff_truncated(temp_repo):
    (temp_repo / "big.py").write_text("\n".join(f"v{i} = {i}" for i in range(500)) + "\n", encoding="utf-8")
    commit_all(temp_repo, "大檔")
    src = make_source(temp_repo, diff_max_lines=50)
    text = git.load(src)[0].text
    assert "其餘截斷" in text
    assert text.count("\n") < 120
