from pixkeep import db


def test_account_columns_depend_on_migration(monkeypatch):
    monkeypatch.setattr(db, "parent_available", False)
    assert "parent_folder_id" not in db._account_cols()
    monkeypatch.setattr(db, "parent_available", True)
    assert "parent_folder_id" in db._account_cols() and "refresh_token" in db._account_cols()


def test_deferred_columns_depend_on_migration(monkeypatch):
    monkeypatch.setattr(db, "deferred_available", False)
    assert not set(db.DEFERRED_KEYS) & set(db._settings_keys())
    monkeypatch.setattr(db, "deferred_available", True)
    assert set(db.DEFERRED_KEYS) <= set(db._settings_keys())
