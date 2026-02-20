from types import SimpleNamespace
from datetime import date, timedelta


def test_queue_book_uses_user_specific_books_output_mode(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}
    config_calls: list[tuple[str, object]] = []

    def fake_get_book_info(_book_id, fetch_download_count=False):
        assert fetch_download_count is False
        return SimpleNamespace(
            title="Test Book",
            author="Tester",
            format="epub",
            size="1 MB",
            preview=None,
            content="book (fiction)",
        )

    def fake_config_get(key, default=None, user_id=None):
        config_calls.append((key, user_id))
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return "alice@example.com" if user_id == 42 else ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.direct_download, "get_book_info", fake_get_book_info)
    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_book("book-1", user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args == {"to": "alice@example.com"}
    assert ("BOOKS_OUTPUT_MODE", 42) in config_calls


def test_queue_release_uses_user_specific_books_output_mode(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}
    config_calls: list[tuple[str, object]] = []

    def fake_config_get(key, default=None, user_id=None):
        config_calls.append((key, user_id))
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return "alice@example.com" if user_id == 42 else ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    release_data = {
        "source": "direct_download",
        "source_id": "release-1",
        "title": "Release Title",
        "release_title": "Release Title [Retail EPUB]",
        "content_type": "book (fiction)",
        "format": "epub",
        "size": "1 MB",
        "download_url": "https://audiobookbay.lu/abss/release-title/",
        "monitored_entity_id": 17,
        "monitored_book_provider": "hardcover",
        "monitored_book_provider_id": "hc-42",
        "match_score": 96,
    }

    success, error = orchestrator.queue_release(release_data, user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args.get("to") == "alice@example.com"
    history_context = task.output_args.get("history_context")
    assert isinstance(history_context, dict)
    assert history_context.get("entity_id") == 17
    assert history_context.get("provider") == "hardcover"
    assert history_context.get("provider_book_id") == "hc-42"
    assert history_context.get("release_title") == "Release Title [Retail EPUB]"
    assert history_context.get("match_score") == 96.0
    assert task.source_url == "https://audiobookbay.lu/abss/release-title/"
    assert task.monitored_entity_id == 17
    assert ("BOOKS_OUTPUT_MODE", 42) in config_calls


def test_queue_book_email_mode_without_recipient_is_queued(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_get_book_info(_book_id, fetch_download_count=False):
        assert fetch_download_count is False
        return SimpleNamespace(
            title="Test Book",
            author="Tester",
            format="epub",
            size="1 MB",
            preview=None,
            content="book (fiction)",
        )

    def fake_config_get(key, default=None, user_id=None):
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.direct_download, "get_book_info", fake_get_book_info)
    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_book("book-1", user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args == {}


def test_queue_release_skips_unreleased_book(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    add_called = {"called": False}

    def fake_config_get(key, default=None, user_id=None):
        if key == "BOOKS_OUTPUT_MODE":
            return "folder"
        return default

    def fake_add(_task):
        add_called["called"] = True
        return True

    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    future_date = (date.today() + timedelta(days=5)).isoformat()
    release_data = {
        "source": "direct_download",
        "source_id": "release-future-1",
        "title": "Future Release",
        "content_type": "book (fiction)",
        "format": "epub",
        "size": "1 MB",
        "release_date": future_date,
    }

    success, error = orchestrator.queue_release(release_data, user_id=42, username="alice")

    assert success is False
    assert error == f"Book is unreleased until {future_date}"
    assert add_called["called"] is False


def test_queue_release_email_mode_without_recipient_is_queued(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_config_get(key, default=None, user_id=None):
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    release_data = {
        "source": "direct_download",
        "source_id": "release-1",
        "title": "Release Title",
        "content_type": "book (fiction)",
        "format": "epub",
        "size": "1 MB",
    }

    success, error = orchestrator.queue_release(release_data, user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args == {}
