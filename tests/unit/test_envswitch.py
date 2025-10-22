import logging
import types
import pytest
from kubernetes.client import V1OwnerReference

# ---------- configure_logger ---------------------------------------------------


def test_configure_logger_success(fresh_envswitch, caplog):
    ar = fresh_envswitch
    with caplog.at_level(logging.DEBUG, logger="kopfoperator"):
        ar.configure_logger(level=logging.DEBUG)
        ar.logger.debug("hello")
    # We should see our message and logger should have handlers configured
    assert any("hello" in r.message for r in caplog.records)


def test_configure_logger_idempotent(fresh_envswitch, caplog):
    ar = fresh_envswitch
    with caplog.at_level(logging.INFO, logger="kopfoperator"):
        # Calling twice should not raise / create duplicate spammy handlers
        ar.configure_logger(level=logging.INFO)
        ar.configure_logger(level=logging.INFO)
        ar.logger.info("once")
    assert any("once" in r.message for r in caplog.records)


# ---------- is_crashlooping ----------------------------------------------------


def test_is_crashlooping_true(fresh_envswitch):
    ar = fresh_envswitch
    pod = {
        "status": {
            "containerStatuses": [
                {
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "restartCount": 3,
                }
            ]
        }
    }
    assert ar.is_crashlooping(pod, min_restarts=1) is True


def test_is_crashlooping_false(fresh_envswitch):
    ar = fresh_envswitch
    pod = {
        "status": {
            "containerStatuses": [
                {"state": {"waiting": {"reason": "ErrImagePull"}}, "restartCount": 99},
                {"state": {"terminated": {"reason": "Error"}}, "restartCount": 1},
            ]
        }
    }
    assert ar.is_crashlooping(pod, min_restarts=1) is False


# ---------- env_merge ----------------------------------------------------------


def test_env_merge_overwrite_and_append(fresh_envswitch):
    ar = fresh_envswitch
    existing = [{"name": "LOG_LEVEL", "value": "info"}]
    patch = {"LOG_LEVEL": "debug", "EXTRA": "1"}
    merged = ar.env_merge(existing, patch)
    assert any(e["name"] == "LOG_LEVEL" and e["value"] == "debug" for e in merged)
    assert any(e["name"] == "EXTRA" and e["value"] == "1" for e in merged)
    assert len(merged) == 2


def test_env_merge_ignores_items_without_name(fresh_envswitch):
    ar = fresh_envswitch
    existing = [{"value": "oops-no-name"}]
    patch = {"NEW": "x"}
    merged = ar.env_merge(existing, patch)
    assert any(e.get("name") == "NEW" and e["value"] == "x" for e in merged)
    # the nameless dict is preserved but doesn't break merging
    assert {"value": "oops-no-name"} in merged


# ---------- parse_selector_to_dict --------------------------------------------


def test_parse_selector_to_dict_success(fresh_envswitch):
    ar = fresh_envswitch
    assert ar.parse_selector_to_dict("a=1,b=2 , c = 3") == {
        "a": "1",
        "b": "2",
        "c": "3",
    }


def test_parse_selector_to_dict_failure(fresh_envswitch):
    ar = fresh_envswitch
    with pytest.raises(ValueError):
        ar.parse_selector_to_dict("a")  # missing '='


# ---------- get_top_owner ------------------------------------------------------


def test_get_top_owner_simple_owner(fresh_envswitch):
    ar = fresh_envswitch
    owners = [
        V1OwnerReference(
            api_version="apps/v1", kind="Deployment", name="my-deploy", uid=""
        )
    ]
    group, kind, name = ar.get_top_owner("default", owners)
    # Given the current implementation, it returns (kind, name)
    assert (group, kind, name) == ("apps", "Deployment", "my-deploy")


def test_get_top_owner_no_owners_returns_pod_tuple(fresh_envswitch):
    ar = fresh_envswitch
    group, kind, name = ar.get_top_owner("default", [])
    assert (group, kind, name) == ("", "Pod", "")


def test_get_top_owner_replicaset(fresh_envswitch, monkeypatch):
    ar = fresh_envswitch

    # The Pod's immediate owner is a ReplicaSet.
    owners = [
        V1OwnerReference(
            api_version="apps/v1", kind="ReplicaSet", name="my-replicaset", uid=""
        )
    ]

    # Stub the API call so that the ReplicaSet has a Deployment owner.
    def fake_read_rs(name: str, namespace: str):
        assert (name, namespace) == ("my-replicaset", "default")
        # Return a minimal object with .metadata.owner_references pointing to a Deployment.
        return types.SimpleNamespace(
            metadata=types.SimpleNamespace(
                owner_references=[
                    V1OwnerReference(
                        api_version="apps/v1",
                        kind="Deployment",
                        name="my-deploy",
                        uid="",
                    )
                ]
            )
        )

    monkeypatch.setattr(ar.apps, "read_namespaced_replica_set", fake_read_rs)

    group, kind, name = ar.get_top_owner("default", owners)
    assert (group, kind, name) == ("apps", "Deployment", "my-deploy")


# ---------- patch_owner_env ----------------------------------------------------


# minimal object graph with .spec.template.spec.to_dict()
class _ObjWithToDict:
    class _Spec:
        class _Template:
            class _Spec:
                def __init__(self, containers):
                    self._containers = containers

                def to_dict(self):
                    return {"containers": self._containers}

            def __init__(self, containers):
                self.spec = _ObjWithToDict._Spec._Template._Spec(containers)

        def __init__(self, containers):
            self.template = _ObjWithToDict._Spec._Template(containers)

    def __init__(self, containers):
        self.spec = _ObjWithToDict._Spec(containers)


def test_patch_owner_env_deployment_success(fresh_envswitch, monkeypatch, dummy_logger):
    ar = fresh_envswitch

    # Arrange fake AppsV1 API with read/patch trackers
    patched = {}

    def fake_read_dep(name, namespace):
        assert (name, namespace) == ("web", "default")
        return _ObjWithToDict(
            [{"name": "c", "env": [{"name": "LOG_LEVEL", "value": "info"}]}]
        )

    def fake_patch_dep(name, namespace, patch):
        patched["args"] = (name, namespace, patch)

    monkeypatch.setattr(ar.apps, "read_namespaced_deployment", fake_read_dep)
    monkeypatch.setattr(ar.apps, "patch_namespaced_deployment", fake_patch_dep)

    # Ensure operator ENV patch is set
    monkeypatch.setattr(
        ar, "ENV_PATCH", {"LOG_LEVEL": "debug", "NEW": "x"}, raising=False
    )

    ar.patch_owner_env(
        "default", "apps", "Deployment", "web", ar.ENV_PATCH, dummy_logger
    )

    assert "args" in patched
    name, ns, patch = patched["args"]
    assert (name, ns) == ("web", "default")
    containers = patch["spec"]["template"]["spec"]["containers"]
    env = containers[0]["env"]
    assert any(e["name"] == "LOG_LEVEL" and e["value"] == "debug" for e in env)
    assert any(e["name"] == "NEW" and e["value"] == "x" for e in env)


def test_patch_owner_env_no_changes_when_empty_patch(
    fresh_envswitch, monkeypatch, dummy_logger
):
    ar = fresh_envswitch
    # Guard: with empty env_patch, should skip and not call client
    monkeypatch.setattr(
        ar.apps,
        "read_namespaced_deployment",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(
        ar.apps,
        "patch_namespaced_deployment",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    ar.patch_owner_env("ns", "apps", "Deployment", "x", {}, dummy_logger)
    assert any("No ENV_PATCH provided" in m for m in dummy_logger.infos)


def test_patch_owner_env_unknown_kind_warns(fresh_envswitch, dummy_logger):
    ar = fresh_envswitch
    ar.patch_owner_env("ns", "unknown", "WeirdKind", "x", {"A": "B"}, dummy_logger)
    assert any(
        "Cannot patch env for WeirdKind/x in ns" in w for w in dummy_logger.warns
    )


# ---------- watch_pods ---------------------------------------------------------


def test_watch_pods_triggers_patch_on_crashloop(fresh_envswitch, monkeypatch):
    ar = fresh_envswitch

    event = {"type": "ADDED"}
    body = {
        "metadata": {
            "name": "web-abc",
            "ownerReferences": [
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "web",
                    "uid": "1",
                    "controller": True,
                }
            ],
        },
        "status": {
            "containerStatuses": [
                {
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "restartCount": 2,
                }
            ]
        },
    }

    # Force thresholds and patch map
    monkeypatch.setattr(ar, "MIN_RESTARTS", 1, raising=False)
    monkeypatch.setattr(ar, "ENV_PATCH", {"X": "Y"}, raising=False)

    called = {}

    def fake_patch(ns, group, kind, name, env_patch, logger):
        called["args"] = (ns, group, kind, name, env_patch)

    monkeypatch.setattr(ar, "patch_owner_env", fake_patch)

    class Log:
        def __init__(self):
            self.msgs = []

        def info(self, m):
            self.msgs.append(m)

        def warning(self, m):
            self.msgs.append(m)

    ar.watch_pods(event=event, body=body, namespace="default", logger=Log())

    assert called["args"] == ("default", "apps", "Deployment", "web", {"X": "Y"})


def test_watch_pods_ignores_non_crashloop_or_event_type(fresh_envswitch, monkeypatch):
    ar = fresh_envswitch

    # Case 1: event type not eligible
    called = {"n": 0}
    monkeypatch.setattr(
        ar, "patch_owner_env", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    body_ok = {
        "status": {
            "containerStatuses": [
                {
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                    "restartCount": 3,
                }
            ]
        },
        "metadata": {
            "ownerReferences": [
                {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web"}
            ]
        },
    }
    ar.watch_pods(
        event={"type": "DELETED"},
        body=body_ok,
        namespace="default",
        logger=types.SimpleNamespace(info=lambda *_: None),
    )
    assert called["n"] == 0

    # Case 2: not crashlooping
    ar.watch_pods(
        event={"type": "ADDED"},
        body={
            "status": {
                "containerStatuses": [
                    {"state": {"waiting": {"reason": "Running"}}, "restartCount": 0}
                ]
            },
            "metadata": {
                "ownerReferences": [
                    {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web"}
                ]
            },
        },
        namespace="default",
        logger=types.SimpleNamespace(info=lambda *_: None),
    )
    assert called["n"] == 0
