from server.infrastructure.json_file_store import JsonFileStore


def test_json_file_store_persists_get_search_and_delete(tmp_path) -> None:
    store_path = tmp_path / "store.json"
    store = JsonFileStore(store_path)

    store.put(("assistant", "filesystem"), "/profile.md", {"content": ["hello"], "kind": "memory"})
    item = store.get(("assistant", "filesystem"), "/profile.md")

    assert item is not None
    assert item.value["content"] == ["hello"]
    assert store_path.exists()

    reloaded = JsonFileStore(store_path)
    results = reloaded.search(("assistant",), filter={"kind": "memory"})

    assert len(results) == 1
    assert results[0].key == "/profile.md"
    assert reloaded.list_namespaces(prefix=("assistant",)) == [("assistant", "filesystem")]

    reloaded.delete(("assistant", "filesystem"), "/profile.md")

    assert JsonFileStore(store_path).get(("assistant", "filesystem"), "/profile.md") is None
