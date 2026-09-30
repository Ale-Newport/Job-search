import pytest

from jobagent.security import SecretStore


class Vault:
    def __init__(self):
        self.values = {"mail": "fixture-token"}
        self.reads = 0
        self.fail = False

    def get_password(self, service, name):
        self.reads += 1
        return self.values.get(name)

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("Keychain locked")
        self.values[name] = value

    def delete_password(self, service, name):
        del self.values[name]


def test_session_cache_refresh_delete_and_no_persistent_fallback(monkeypatch):
    vault, store = Vault(), SecretStore()
    monkeypatch.setattr(store, "_backend", lambda: vault)
    assert store.get("mail") == store.get("mail") == "fixture-token"
    assert vault.reads == 1
    store.set("mail", "refreshed")
    assert store.get("mail") == "refreshed"
    vault.fail = True
    with pytest.raises(RuntimeError):
        store.set("mail", "unsaved")
    assert store.get("mail") == "refreshed"
    store.delete("mail")
    assert store.get("mail") is None
