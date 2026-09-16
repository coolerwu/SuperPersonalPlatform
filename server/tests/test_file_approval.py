from types import SimpleNamespace

import pytest

from server.infrastructure.file_approval import FileApprovalStore, approval_file


def test_file_lease_expiry_reopen_and_isolation(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("server.infrastructure.file_approval.time.time", lambda: clock[0])
    store = FileApprovalStore(tmp_path / "session1.json", "a")
    store.grant("/webdav/a.md")
    store = FileApprovalStore(tmp_path / "session1.json", "a")
    when = store.wrap("edit_file", lambda req: True)
    req = SimpleNamespace(tool_call={"args": {"file_path": "/webdav/a.md"}})
    assert not when(req)
    assert not FileApprovalStore(tmp_path / "session1.json", "other").allows("edit_file", req.tool_call["args"])
    assert not FileApprovalStore(tmp_path / "session2.json", "a").allows("edit_file", req.tool_call["args"])
    assert store.wrap("edit_file", lambda req: False)(req) is False
    assert store.wrap("delete", lambda req: True)(req) is True
    clock[0] = 1599.9
    assert not when(req)
    clock[0] = 1600
    assert when(req)


@pytest.mark.parametrize("path", ["/webdav/b.md", "/webdav/a.md/child", "/webdav/../a.md", "/webdav//a.md", "/webdav/a.md/", "/files/a.md"])
def test_grant_does_not_authorize_other_paths(tmp_path, path):
    store = FileApprovalStore(tmp_path / "grants.json", "a")
    store.grant("/webdav/a.md")
    assert not store.allows("write_file", {"file_path": path})


def test_invalid_and_non_file_calls():
    assert approval_file("send_attachment", {"file_path": "/webdav/a.md"}) is None
    assert approval_file("write_file", {"file_path": None}) is None
