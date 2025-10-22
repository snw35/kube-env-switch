import importlib
import sys
import pytest
import os
import textwrap


class DummyLogger:
    def __init__(self):
        self.infos = []
        self.warns = []

    def info(self, msg):
        self.infos.append(msg)

    def warning(self, msg):
        self.warns.append(msg)


@pytest.fixture(scope="session", autouse=True)
def fake_kubeconfig(tmp_path_factory):
    """Session-wide dummy kubeconfig."""
    d = tmp_path_factory.mktemp("kube")
    kubeconfig = d / "config"
    kubeconfig.write_text(
        textwrap.dedent("""
        apiVersion: v1
        kind: Config
        clusters:
        - cluster:
            server: https://example.invalid
            certificate-authority-data: ""
          name: dummy
        contexts:
        - context:
            cluster: dummy
            user: dummy
          name: dummy
        current-context: dummy
        users:
        - name: dummy
          user:
            token: dummy-token
    """).strip()
    )
    os.environ["KUBECONFIG"] = str(d / "config")
    os.environ["KUBERNETES_SERVICE_HOST"] = "127.0.0.1"
    os.environ["KUBERNETES_SERVICE_PORT"] = "443"
    yield str(kubeconfig)


@pytest.fixture
def dummy_logger():
    return DummyLogger()


@pytest.fixture(autouse=True)
def fresh_envswitch(monkeypatch):
    """
    Ensure we import a fresh copy of envswitch for each test so global
    state (ENV_PATCH, clients, etc.) can be changed independently.
    """
    if "envswitch" in sys.modules:
        del sys.modules["envswitch"]
    mod = importlib.import_module("envswitch")
    yield mod
