from observe.adapters import bash_reads, classify, command_text, programs, response_status


def test_claude_tools():
    assert classify("Bash", {"command": "git status"}).category == "bash"
    read = classify("Read", {"file_path": "/repo/a.py"})
    assert (read.category, read.files) == ("file_read", [("/repo/a.py", "read")])
    edit = classify("Edit", {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y"})
    assert (edit.category, edit.files) == ("file_write", [("/repo/a.py", "write")])
    assert classify("Grep", {"pattern": "TODO", "path": "src"}).target == "TODO  in src"
    assert classify("WebFetch", {"url": "https://x.dev/a"}).category == "web"
    task = classify("Task", {"subagent_type": "Explore", "description": "find hooks"})
    assert (task.category, task.target) == ("agent", "Explore · find hooks")


def test_mcp_tool_name():
    info = classify("mcp__notion__search_pages", {"query": "x"})
    assert (info.category, info.target) == ("mcp", "notion/search_pages")


def test_codex_apply_patch_files():
    patch = (
        "*** Begin Patch\n*** Add File: docs/new.md\n+hi\n*** Update File: src/app.py\n@@\n-a\n+b\n"
        "*** Delete File: old.txt\n*** End Patch"
    )
    info = classify("apply_patch", {"command": patch})
    assert info.category == "file_write"
    assert info.files == [("docs/new.md", "write"), ("src/app.py", "write"), ("old.txt", "delete")]
    assert info.target == "3 files"


def test_command_text_from_argv():
    assert command_text({"command": ["bash", "-lc", "ls -la"]}) == "ls -la"
    assert command_text({"cmd": "rg --files"}) == "rg --files"


def test_programs():
    assert programs("cd src && FOO=1 pytest -q | tail -5; sudo git push") == ["pytest", "tail", "git"]
    assert programs('python3 -c "import a; b()" 2>&1 | head') == ["python3", "head"]
    assert programs('for f in *.py; do export X=1; "$CH" --x; wc -l $f; done') == ["wc"]


def test_programs_skip_heredoc_bodies():
    cmd = "cat > a.py <<'EOF'\ndef f():\n    return 1\nEOF\nnode --check a.js"
    assert programs(cmd) == ["cat", "node"]
    assert bash_reads(cmd) == []


def test_bash_reads():
    assert bash_reads("sed -n '1,80p' src/app.py && head -n 20 README.md") == ["src/app.py", "README.md"]
    assert bash_reads("cat a.txt b.txt | wc -l") == ["a.txt", "b.txt"]
    assert bash_reads("sed -i 's/a/b/' x.py; cat $HOME/x; ls docs") == []
    info = classify("Bash", {"command": "nl -ba main.go"})
    assert info.files == [("main.go", "read")]


def test_response_status():
    assert response_status({"stdout": "", "interrupted": False}, "PostToolUse") == "ok"
    assert response_status({"exit_code": 2}, "PostToolUse") == "error"
    assert response_status("Process exited with code 1\nOutput:", "PostToolUse") == "error"
    assert response_status("Exit code: 0\nWall time: 0 seconds", "PostToolUse") == "ok"
    assert response_status("anything", "PostToolUseFailure") == "error"
