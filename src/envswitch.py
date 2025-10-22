import os
import json
import logging
import kopf
from typing import Any, Dict, List, Mapping, Tuple, cast

from kubernetes import client, config
from kubernetes.client import V1OwnerReference
from kubernetes.config.config_exception import ConfigException

logger = logging.getLogger("kopfoperator")


def init_k8s():
    config_file = os.getenv("KUBECONFIG", None)
    try:
        config.load_incluster_config()  # type: ignore[attr-defined]
    except ConfigException:
        # Optional for local dev:
        config.load_kube_config(config_file)  # type: ignore[attr-defined]


init_k8s()


def configure_logger(level=logging.INFO):
    """
    Configure basic logger that prints to standard out.
    Used until kopf in-cluster logging is established.
    """
    if logger.hasHandlers():
        logger.handlers.clear()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


def is_crashlooping(pod: dict, min_restarts: int) -> bool:
    """
    Return True if ANY container in the Pod is in CrashLoopBackOff.
    """
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    for st in statuses:
        state = (st.get("state") or {}).get("waiting") or {}
        if state.get("reason") == "CrashLoopBackOff" and (
            st.get("restartCount", 0) >= min_restarts
        ):
            return True
    return False


def env_merge(existing: List[dict], patch_map: Dict[str, str]) -> List[dict]:
    """
    Merge/overwrite env entries with patch_map.
    """
    index = {e["name"]: i for i, e in enumerate(existing or []) if "name" in e}
    result = list(existing or [])
    for k, v in patch_map.items():
        if k in index:
            result[index[k]]["value"] = v
        else:
            result.append({"name": k, "value": v})
    return result


def get_top_owner(
    namespace: str, owners: List[V1OwnerReference]
) -> Tuple[str, str, str]:
    """
    Walk ownerReferences up to a top-level controller we can patch.
    Returns (group, kind, name). For core kinds, group is ''.
    """

    # Start with the immediate owner and walk up (ReplicaSet -> Deployment, etc.)
    # We’ll handle common controllers explicitly.
    def _owner_tuple(ref: V1OwnerReference) -> Tuple[str, str, str]:
        # apiVersion like "apps/v1" -> group "apps"
        if ref.api_version is None:
            return "", "", ""
        if "/" in ref.api_version and ref.api_version and ref.kind and ref.name:
            group = ref.api_version.split("/")[0] if "/" in ref.api_version else ""
            return group, ref.kind, ref.name
        else:
            return "", "", ""

    # Loaders for each known intermediate controller to climb one level up if needed.
    # e.g., if owner is a ReplicaSet, find its Deployment owner.
    def _load_owners_of_replica_set(
        name: str, namespace: str
    ) -> List[V1OwnerReference]:
        rs = apps.read_namespaced_replica_set(name=name, namespace=namespace)
        return list(getattr(rs.metadata, "owner_references", []) or [])

    def _load_owners_of_job(name: str) -> List[V1OwnerReference]:
        job = batch.read_namespaced_job(name=name, namespace=namespace)
        assert job.metadata is not None
        return list(job.metadata.owner_references or [])

    def _load_owners_of_cronjob(name: str) -> List[V1OwnerReference]:
        if batch:
            cj = batch.read_namespaced_cron_job(name=name, namespace=namespace)
        else:
            cj = batch.read_namespaced_cron_job(name=name, namespace=namespace)
        assert cj.metadata is not None
        return list(cj.metadata.owner_references or [])

    stack = list(owners or [])
    seen = set()
    while stack:
        ref = stack[0]
        group, kind, name = _owner_tuple(ref)
        key = (kind, name)
        if key in seen:
            break
        seen.add(key)

        # If we hit a patchable top-level controller, return it.
        if (kind) in {
            ("apps", "Deployment"),
            ("apps", "StatefulSet"),
            ("apps", "DaemonSet"),
            ("batch", "Job"),
            ("batch", "CronJob"),
        }:
            return group, kind, name

        # Climb up one level if we’re at an intermediate controller:
        if (group, kind) == ("apps", "ReplicaSet"):
            stack = _load_owners_of_replica_set(name, namespace)
            continue
        if (group, kind) == ("batch", "Job"):
            # sometimes Job is already top; handled above. If here, try climb (e.g., from Pod -> Job -> CronJob)
            stack = _load_owners_of_job(name)
            continue
        if (group, kind) == ("batch", "CronJob"):
            stack = _load_owners_of_cronjob(name)
            continue

        # Unknown/leaf: patch the current one if it has a pod template we can edit (rare).
        return group, kind, name

    # No owners? Treat the Pod as its own “top owner” (won’t be patchable for env).
    return "", "Pod", (owners[0].name if owners else "")


def patch_owner_env(
    namespace: str,
    group: str,
    kind: str,
    name: str,
    env_patch: Dict[str, str],
    logger: kopf.Logger,
) -> None:
    """
    Patch the owning controller's pod template to merge env vars for all containers.
    """
    if not env_patch:
        logger.info("No ENV_PATCH provided; skipping.")
        return

    def patch_template_env(pod_spec: Mapping[str, Any]) -> Dict[str, Any]:
        containers = cast(List[Dict[str, Any]], pod_spec.get("containers", []))
        new_containers: List[Dict[str, Any]] = []
        for c in containers:
            merged_env = env_merge(c.get("env") or [], ENV_PATCH)
            new_containers.append({**c, "env": merged_env})
        return {"containers": new_containers}

    if (group, kind) == ("apps", "Deployment"):
        dep = apps.read_namespaced_deployment(name, namespace)
        if dep.spec and dep.spec.template and dep.spec.template.spec:
            tpl = dep.spec.template.spec.to_dict()
            patch = {"spec": {"template": {"spec": patch_template_env(tpl)}}}
            apps.patch_namespaced_deployment(name, namespace, patch)
            logger.info(f"Patched Deployment/{name} env in {namespace}.")
            return

    if (group, kind) == ("apps", "StatefulSet"):
        ss = apps.read_namespaced_stateful_set(name, namespace)
        if ss.spec and ss.spec.template and ss.spec.template.spec:
            tpl = ss.spec.template.spec.to_dict()
            patch = {"spec": {"template": {"spec": patch_template_env(tpl)}}}
            apps.patch_namespaced_stateful_set(name, namespace, patch)
            logger.info(f"Patched StatefulSet/{name} env in {namespace}.")
            return

    if (group, kind) == ("apps", "DaemonSet"):
        ds = apps.read_namespaced_daemon_set(name, namespace)
        if ds.spec and ds.spec.template and ds.spec.template.spec:
            tpl = ds.spec.template.spec.to_dict()
            patch = {"spec": {"template": {"spec": patch_template_env(tpl)}}}
            apps.patch_namespaced_daemon_set(name, namespace, patch)
            logger.info(f"Patched DaemonSet/{name} env in {namespace}.")
            return

    if (group, kind) == ("batch", "Job"):
        job = batch.read_namespaced_job(name, namespace)
        if job.spec and job.spec.template and job.spec.template.spec:
            tpl = job.spec.template.spec.to_dict()
            patch = {"spec": {"template": {"spec": patch_template_env(tpl)}}}
            batch.patch_namespaced_job(name, namespace, patch)
            logger.info(f"Patched Job/{name} env in {namespace}.")
            return

    if (group, kind) == ("batch", "CronJob"):
        # V1 CronJob
        cj = batch.read_namespaced_cron_job(name, namespace)
        if (
            cj.spec
            and cj.spec.job_template
            and cj.spec.job_template.spec
            and cj.spec.job_template.spec.template.spec
        ):
            tpl = cj.spec.job_template.spec.template.spec.to_dict()
            new_spec = {
                "jobTemplate": {"spec": {"template": {"spec": patch_template_env(tpl)}}}
            }
            batch.patch_namespaced_cron_job(name, namespace, {"spec": new_spec})
            logger.info(f"Patched CronJob/{name} env in {namespace}.")
            return

    # Fallback: we cannot mutate Pod.spec on a live Pod; log and exit.
    logger.warning(
        f"Cannot patch env for {kind}/{name} in {namespace}. Skipping (Pods are immutable)."
    )


def parse_selector_to_dict(sel: str) -> dict:
    """
    Takes JSON as a string and attempts to parse into
    a dict for the pod label selector.
    Supports simple equality pairs: "k=v,k2=v2"
    """
    out = {}
    if not sel:
        return out
    for pair in sel.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Unsupported selector fragment (need k=v): {pair}")
        k, v = pair.split("=", 1)
        out[k.strip()] = v.strip()
    return out


# --- Configuration (env vars) -------------------------------------------------
# Selector for pods to watch, e.g. "envswitch=true,app=myapp"
WATCH_LABELS = parse_selector_to_dict(
    os.getenv("WATCH_LABEL_SELECTOR", "envswitch=true")
)

# JSON mapping of env var -> new value, e.g. {"LOG_LEVEL":"debug","JAVA_TOOL_OPTIONS":"-Xmx256m"}
ENV_PATCH_JSON = os.getenv("ENV_PATCH_JSON", "{}")
ENV_PATCH: Dict[str, str] = json.loads(ENV_PATCH_JSON)

# Optional minimum restart count to avoid flapping (defaults to 1)
MIN_RESTARTS = int(os.getenv("MIN_RESTARTS", "1"))


@kopf.on.event("", "v1", "pods", labels=WATCH_LABELS)  # pyright: ignore[reportArgumentType]
def watch_pods(event, body, namespace, logger, **_):
    """
    Watch for pod ADDED/MODIFIED events.
    If CrashLoopBackOff detected, patch ENV values on the owning resource.
    """
    if event["type"] not in ("ADDED", "MODIFIED"):
        return

    if not is_crashlooping(body, MIN_RESTARTS):
        return

    meta = body.get("metadata", {})
    name = meta.get("name", "<unknown>")
    logger.info(f"CrashLoopBackOff detected on Pod/{name}. Attempting env patch…")

    owners: List[V1OwnerReference] = []
    for ref in meta.get("ownerReferences") or []:
        owners.append(
            V1OwnerReference(
                api_version=ref.get("apiVersion"),
                kind=ref.get("kind"),
                name=ref.get("name"),
                uid=ref.get("uid"),
                controller=ref.get("controller"),
                block_owner_deletion=ref.get("blockOwnerDeletion"),
            )
        )

    group, kind, owner_name = get_top_owner(namespace, owners)
    patch_owner_env(namespace, group, kind, owner_name, ENV_PATCH, logger)


core = client.CoreV1Api()
apps = client.AppsV1Api()
batch = client.BatchV1Api()
