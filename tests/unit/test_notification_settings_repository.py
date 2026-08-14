import json

from app.repositories import notification_settings_repository as module


class _Cursor:
    def execute(self, *_args):
        return None

    def fetchone(self):
        return None


class _Connection:
    def cursor(self):
        return _Cursor()

    def close(self):
        return None

    def commit(self):
        return None


def test_imports_dingtalk_fallback_without_app_credentials(tmp_path, monkeypatch):
    config = {"alerts": {"dingtalk_webhook_secret": "legacy-fallback"}}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")

    repository = module.NotificationSettingsRepository()
    saved = {}
    monkeypatch.setattr(module, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(repository, "_connection", lambda: _Connection())
    monkeypatch.setattr(
        repository,
        "save",
        lambda kind, values: saved.update(kind=kind, values=values),
    )

    assert repository._import_legacy("dingtalk") is True
    assert saved["kind"] == "dingtalk"
    assert saved["values"]["app_key"] == ""
    assert saved["values"]["app_secret"] == ""
    assert saved["values"]["fallback_webhook_secret"] == "legacy-fallback"
