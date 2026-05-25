def test_retrieve_uses_chroma_collection_query(monkeypatch):
    from app.integrations import vector_store

    class FakeColl:
        def query(self, query_texts, n_results=4):
            assert query_texts == ["hello"]
            assert n_results == 2
            return {"documents": [["chunk-a", "chunk-b"]]}

    class FakeClient:
        def get_or_create_collection(self, name):
            assert name == "demo-coll"
            return FakeColl()

    monkeypatch.setattr(vector_store, "client", FakeClient())
    assert vector_store.retrieve("demo-coll", "hello", top_k=2) == ["chunk-a", "chunk-b"]


def test_ingest_text_splits_and_adds(monkeypatch):
    from app.integrations import vector_store

    captured: dict = {}

    class FakeColl:
        def add(self, ids, documents, metadatas):
            captured["ids"] = ids
            captured["documents"] = documents
            captured["metadatas"] = metadatas

        def query(self, query_texts, n_results=4):
            return {"documents": [[]]}

    class FakeClient:
        def get_or_create_collection(self, name):
            assert name == "kb1"
            return FakeColl()

    monkeypatch.setattr(vector_store, "client", FakeClient())
    long_text = "x" * 900
    vector_store.ingest_text("kb1", "doc1", long_text)
    assert len(captured["documents"]) == 2
    assert captured["documents"][0] == "x" * 800
    assert captured["documents"][1] == "x" * 100
    assert captured["metadatas"] == [{"source": "doc1"}, {"source": "doc1"}]
