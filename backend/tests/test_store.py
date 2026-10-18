from backend.app.store import Store


def test_chat_run_and_event_lifecycle(tmp_path) -> None:
    store = Store(tmp_path / "app.sqlite3")
    store.initialize()
    chat = store.create_chat()
    message = store.add_message(chat["id"], "user", "Compare compounds")
    run = store.create_run(chat["id"], message["id"], "test-model")

    event_id = store.emit(run["id"], "run.started", {"ok": True})
    store.set_run_status(run["id"], "completed")

    events = store.events_after(run["id"], 0)
    assert events[0]["id"] == event_id
    assert events[0]["payload"] == {"ok": True}
    assert store.get_run(run["id"])["status"] == "completed"


def test_chat_can_be_renamed_and_deleted_with_related_records(tmp_path) -> None:
    store = Store(tmp_path / "app.sqlite3")
    store.initialize()
    chat = store.create_chat()
    store.update_chat_title(chat["id"], "Renamed analysis")
    message = store.add_message(chat["id"], "user", "Compare compounds")
    run = store.create_run(chat["id"], message["id"], "test-model")
    store.set_run_status(run["id"], "completed")
    experiment = store.save_experiment(chat["id"], "Saved result", "")

    deleted = store.delete_chat(chat["id"])

    assert store.list_chats() == []
    assert store.list_experiments() == []
    assert deleted == {"run_ids": [run["id"]], "experiment_ids": [experiment["id"]]}
