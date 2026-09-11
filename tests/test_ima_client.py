from deepcat.ima_client import ImaClient, ImaCredentials, note_html_to_markdown


class FakeImaClient(ImaClient):
    def __init__(self) -> None:
        super().__init__(ImaCredentials("client", "key"))
        self.calls = []

    def post(self, api_path, payload=None):
        self.calls.append((api_path, payload or {}))
        if str(api_path).endswith("import_doc"):
            return {"code": 0, "data": {"note_id": "note-1"}}
        if str(api_path).endswith("append_doc"):
            return {"code": 0, "data": {"note_id": payload["note_id"]}}
        if str(api_path).endswith("get_doc_content"):
            return {"code": 0, "data": {"content": "完整笔记正文"}}
        if str(api_path).endswith("search_note"):
            return {
                "code": 0,
                "data": {
                    "search_note_infos": [
                        {
                            "note_book_info": {
                                "note_id": "note-1",
                                "title": "产品排期",
                                "summary": "排期摘要",
                                "note_ext_info": {"folder_name": "工作"},
                            },
                            "highlightInfo": {"doc_content": "包含 <em>排期</em> 和负责人"},
                        }
                    ]
                },
            }
        if str(api_path).endswith("add_knowledge"):
            return {"code": 0, "data": {"media_id": "media-1"}}
        if str(api_path).endswith("search_knowledge"):
            return {
                "code": 0,
                "data": {
                    "info_list": [
                        {
                            "media_id": "media-note-1",
                            "title": "产品排期",
                            "highlight_content": "包含 <em>排期</em> 和负责人",
                        }
                    ]
                },
            }
        if str(api_path).endswith("get_media_info"):
            return {
                "code": 0,
                "data": {
                    "media_type": 11,
                    "notebook_ext_info": {"notebook_id": "note-1"},
                },
            }
        return {"code": 0, "data": {}}


def test_note_html_to_markdown_extracts_plain_text() -> None:
    markdown = note_html_to_markdown("标题", "<h1>旧标题</h1><p>正文 <b>加粗</b></p>")

    assert markdown.startswith("# 标题")
    assert "正文 加粗" in markdown


def test_import_note_uses_markdown_format_and_folder_fields() -> None:
    client = FakeImaClient()

    note_id = client.import_note("# 标题\n正文", folder_id="folder-1", folder_name="工作")

    assert note_id == "note-1"
    api_path, payload = client.calls[-1]
    assert api_path == "openapi/note/v1/import_doc"
    assert payload["content_format"] == 1
    assert payload["folder_id"] == "folder-1"
    assert payload["folder_name"] == "工作"


def test_add_note_to_knowledge_base_payload() -> None:
    client = FakeImaClient()

    media_id = client.add_note_to_knowledge_base(
        knowledge_base_id="kb-1",
        note_id="note-1",
        title="笔记",
        folder_id="folder-1",
    )

    assert media_id == "media-1"
    api_path, payload = client.calls[-1]
    assert api_path == "openapi/wiki/v1/add_knowledge"
    assert payload["media_type"] == 11
    assert payload["note_info"]["content_id"] == "note-1"
    assert payload["knowledge_base_id"] == "kb-1"
    assert payload["folder_id"] == "folder-1"


def test_search_notes_payload() -> None:
    client = FakeImaClient()

    client.search_notes("排期", search_type=1)

    api_path, payload = client.calls[-1]
    assert api_path == "openapi/note/v1/search_note"
    assert payload["search_type"] == 1
    assert payload["query_info"] == {"content": "排期"}
    assert payload["start"] == 0
    assert payload["end"] == 20


def test_build_note_search_prompt_reads_note_content() -> None:
    client = FakeImaClient()

    prompt = client.build_note_search_prompt("排期")

    assert "从 IMA 笔记检索到的内容" in prompt
    assert "产品排期" in prompt
    assert "排期摘要" in prompt
    assert "包含 排期 和负责人" in prompt
    assert "完整笔记正文" in prompt
    assert [call[0] for call in client.calls] == [
        "openapi/note/v1/search_note",
        "openapi/note/v1/search_note",
        "openapi/note/v1/get_doc_content",
    ]


def test_search_knowledge_payload() -> None:
    client = FakeImaClient()

    client.search_knowledge("kb-1", "排期")

    api_path, payload = client.calls[-1]
    assert api_path == "openapi/wiki/v1/search_knowledge"
    assert payload["knowledge_base_id"] == "kb-1"
    assert payload["query"] == "排期"


def test_build_knowledge_search_prompt_reads_note_result() -> None:
    client = FakeImaClient()

    prompt = client.build_knowledge_search_prompt("kb-1", "排期")

    assert "搜索词：排期" in prompt
    assert "产品排期" in prompt
    assert "包含 排期 和负责人" in prompt
    assert "完整笔记正文" in prompt
    assert [call[0] for call in client.calls] == [
        "openapi/wiki/v1/search_knowledge",
        "openapi/wiki/v1/get_media_info",
        "openapi/note/v1/get_doc_content",
    ]


def test_build_knowledge_search_prompt_reads_url_result(monkeypatch) -> None:
    class UrlImaClient(FakeImaClient):
        def post(self, api_path, payload=None):
            self.calls.append((api_path, payload or {}))
            if str(api_path).endswith("search_knowledge"):
                return {
                    "code": 0,
                    "data": {
                        "info_list": [
                            {
                                "media_id": "media-url-1",
                                "title": "网页资料",
                                "highlight_content": "包含 <em>排期</em>",
                            }
                        ]
                    },
                }
            if str(api_path).endswith("get_media_info"):
                return {
                    "code": 0,
                    "data": {
                        "media_type": 2,
                        "url_info": {
                            "url": "https://example.com/page",
                            "headers": {"X-Test-Token": "secret"},
                        },
                    },
                }
            return {"code": 0, "data": {}}

    class FakeResponse:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size=-1):
            return "<html><body><h1>标题</h1><p>网页正文内容</p></body></html>".encode("utf-8")

    seen_requests = []

    def fake_urlopen(request, timeout=0):
        seen_requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = UrlImaClient()

    prompt = client.build_knowledge_search_prompt("kb-1", "排期")

    assert "网页资料" in prompt
    assert "原文链接：https://example.com/page" in prompt
    assert "网页正文内容" in prompt
    assert seen_requests[0][0].full_url == "https://example.com/page"
