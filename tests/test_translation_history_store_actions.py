import sqlite3

from deepcat.translation_history_store import TranslationHistoryStore


def _add_qa_record(store: TranslationHistoryStore, source_text: str) -> int:
    return store.add_record(
        task_type="qa",
        source_text=source_text,
        result_text=f"{source_text} result",
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="[]",
        is_success=True,
    )


def test_rename_pin_order_and_batch_delete(tmp_path):
    store = TranslationHistoryStore(tmp_path / "history.db")
    first_id = _add_qa_record(store, "第一段对话")
    second_id = _add_qa_record(store, "第二段对话")
    third_id = _add_qa_record(store, "第三段对话")

    store._db.execute(
        "UPDATE history_records SET created_at = ? WHERE id = ?",
        ("2026-01-01 09:00:00", first_id),
    )
    store._db.execute(
        "UPDATE history_records SET created_at = ? WHERE id = ?",
        ("2026-01-02 09:00:00", second_id),
    )
    store._db.execute(
        "UPDATE history_records SET created_at = ? WHERE id = ?",
        ("2026-01-03 09:00:00", third_id),
    )
    store._db.commit()

    assert store.rename_record(first_id, "  我的会话标题  ")
    renamed = store.get_record(first_id)
    assert renamed is not None
    assert renamed["title"] == "我的会话标题"
    assert renamed["source_text"] == "第一段对话"

    assert store.set_pinned(first_id, True)
    assert store.toggle_starred(second_id) is True
    default_order = [record["id"] for record in store.get_records(task_type_filter="qa")]
    pinned_order = [
        record["id"]
        for record in store.get_records(task_type_filter="qa", pinned_first=True)
    ]
    summary_order = [
        record["id"]
        for record in store.get_record_summaries(task_type_filter="qa", pinned_first=True)
    ]
    assert default_order == [third_id, second_id, first_id]
    assert pinned_order == [first_id, third_id, second_id]
    assert summary_order == [first_id, third_id, second_id]

    assert store.delete_records([first_id, first_id, -1]) == 1
    assert store.get_record(first_id) is None
    assert store.get_record(second_id) is not None
    store.close()


def test_existing_history_database_is_migrated(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE history_records (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type     TEXT    NOT NULL DEFAULT 'translate',
            source_text   TEXT    NOT NULL DEFAULT '',
            result_text   TEXT    NOT NULL DEFAULT '',
            model_name    TEXT    NOT NULL DEFAULT '',
            source_lang   TEXT    NOT NULL DEFAULT '自动检测',
            target_lang   TEXT    NOT NULL DEFAULT '中英互译',
            elapsed_secs  REAL    NOT NULL DEFAULT 0.0,
            prompt_text   TEXT    NOT NULL DEFAULT '',
            is_success    INTEGER NOT NULL DEFAULT 1,
            is_starred    INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT    NOT NULL DEFAULT '2026-01-01 09:00:00'
        );
        INSERT INTO history_records
            (task_type, source_text, result_text, model_name, source_lang, target_lang, elapsed_secs, prompt_text, is_success)
        VALUES
            ('qa', '旧记录', '旧回答', 'test-model', '自动检测', '中英互译', 0.1, '[]', 1);
        """
    )
    conn.commit()
    conn.close()

    store = TranslationHistoryStore(db_path)
    record = store.get_record(1)

    assert record is not None
    assert record["title"] == ""
    assert record["is_pinned"] == 0
    assert store.rename_record(1, "迁移后的标题")
    assert store.set_pinned(1, True)
    migrated = store.get_record(1)
    assert migrated is not None
    assert migrated["title"] == "迁移后的标题"
    assert migrated["is_pinned"] == 1
    store.close()


def test_record_summaries_do_not_load_full_prompt_or_large_result(tmp_path):
    store = TranslationHistoryStore(tmp_path / "summary.db")
    large_prompt = "提示词" * 20000
    large_result = "回答" * 20000
    record_id = store.add_record(
        task_type="qa",
        source_text="问题" * 20000,
        result_text=large_result,
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text=large_prompt,
        is_success=True,
    )

    summary = store.get_record_summaries(
        limit=1,
        task_type_filter="qa",
        pinned_first=True,
        text_limit=80,
    )[0]
    full = store.get_record(record_id)

    assert summary["id"] == record_id
    assert summary["prompt_text"] == ""
    assert len(summary["source_text"]) <= 83
    assert len(summary["result_text"]) <= 83
    assert full is not None
    assert full["prompt_text"] == large_prompt
    assert full["result_text"] == large_result
    store.close()


def test_record_summaries_search_ai_chat_content_and_prompt(tmp_path):
    store = TranslationHistoryStore(tmp_path / "summary_search.db")
    first_id = store.add_record(
        task_type="qa",
        source_text="如何整理读书笔记？",
        result_text="可以用卡片盒方法，把观点、证据和行动分开记录。",
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text='[{"role":"user","content":"整理知识库"}]',
        is_success=True,
    )
    second_id = store.add_record(
        task_type="qa",
        source_text="普通问题",
        result_text="普通回答",
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text='[{"role":"assistant","content":"隐藏关键词：番茄钟复盘"}]',
        is_success=True,
    )
    title_id = store.add_record(
        task_type="qa",
        source_text="标题外的问题",
        result_text="标题外的回答",
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="[]",
        is_success=True,
    )
    assert store.rename_record(title_id, "火花笔记归档")

    by_reply = store.get_record_summaries(search_query="卡片盒", text_limit=80)
    by_prompt = store.get_record_summaries(search_query="番茄钟复盘", text_limit=80)
    by_title = store.get_record_summaries(search_query="火花笔记", text_limit=80)
    by_title_legacy = store.search("火花笔记")

    assert [record["id"] for record in by_reply] == [first_id]
    assert [record["id"] for record in by_prompt] == [second_id]
    assert by_prompt[0]["prompt_text"] == ""
    assert [record["id"] for record in by_title] == [title_id]
    assert [record["id"] for record in by_title_legacy] == [title_id]
    store.close()


def test_record_summaries_filter_by_model_and_list_model_names(tmp_path):
    store = TranslationHistoryStore(tmp_path / "model_filter.db")
    first_id = store.add_record(
        task_type="qa",
        source_text="模型 A 问题",
        result_text="回答 A",
        model_name="model-a",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="[]",
        is_success=True,
    )
    store.add_record(
        task_type="qa",
        source_text="模型 B 问题",
        result_text="回答 B",
        model_name="model-b",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="[]",
        is_success=True,
    )
    store.add_record(
        task_type="translate",
        source_text="翻译问题",
        result_text="translation",
        model_name="translate-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="",
        is_success=True,
    )

    by_model = store.get_record_summaries(
        task_type_filter="qa",
        model_name_filter="model-a",
        text_limit=80,
    )
    by_model_search = store.get_record_summaries(search_query="model-b", text_limit=80)
    by_legacy_search = store.search("translate-model")
    qa_models = store.get_model_names(task_type_filter="qa")

    assert [record["id"] for record in by_model] == [first_id]
    assert [record["model_name"] for record in by_model_search] == ["model-b"]
    assert [record["model_name"] for record in by_legacy_search] == ["translate-model"]
    assert qa_models == ["model-a", "model-b"]
    store.close()


def test_clear_task_type_only_deletes_matching_history_records(tmp_path):
    store = TranslationHistoryStore(tmp_path / "clear_task_type.db")
    qa_id = _add_qa_record(store, "AI 对话")
    translate_id = store.add_record(
        task_type="translate",
        source_text="翻译内容",
        result_text="translated",
        model_name="test-model",
        source_lang="自动检测",
        target_lang="中英互译",
        elapsed_secs=0.1,
        prompt_text="",
        is_success=True,
    )

    assert store.clear_task_type("qa") == 1
    assert store.get_record(qa_id) is None
    assert store.get_record(translate_id) is not None
    store.close()
