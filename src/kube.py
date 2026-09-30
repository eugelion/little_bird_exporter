from __future__ import annotations

import logging
import re
import time

from kubernetes import client, config
from kubernetes.stream import stream

logger = logging.getLogger(__name__)


class PodExecTimeoutError(TimeoutError):
    pass

def load_core_v1() -> client.CoreV1Api:
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.info("Loaded local kubeconfig")
    return client.CoreV1Api()


def list_pods_matching(
    core_v1: client.CoreV1Api,
    namespace: str,
    name_pattern: re.Pattern[str],
) -> list[str]:
    pods = core_v1.list_namespaced_pod(namespace=namespace)
    return sorted(
        pod.metadata.name
        for pod in pods.items
        if pod.metadata.name and name_pattern.search(pod.metadata.name)
    )

def exec_in_pod(
    core_v1: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    command: str,
    timeout: int = 30,
) -> str:
    started = time.monotonic()
    # stream() temporarily monkeypatches api_client.request with a websocket call.
    # That is not thread-safe, so exec through a dedicated ApiClient instead of the
    # shared one, otherwise concurrent list_namespaced_pod calls go over websocket.
    with client.ApiClient(core_v1.api_client.configuration) as exec_api_client:
        response = stream(
            client.CoreV1Api(exec_api_client).connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["/bin/sh", "-c", command],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    try:
        while response.is_open():
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                raise PodExecTimeoutError(
                    f"exec timed out after {timeout}s in pod {namespace}/{pod_name}"
                )
            response.update(timeout=min(1, timeout - elapsed))
            if response.peek_stdout():
                stdout_chunks.append(response.read_stdout())
            if response.peek_stderr():
                stderr_chunks.append(response.read_stderr())
    finally:
        response.close()
    exit_code = response.returncode
    stdout = "".join(stdout_chunks).strip()
    stderr = "".join(stderr_chunks).strip()
    if exit_code not in (0, None):
        detail = stderr or stdout or f"exit code {exit_code}"
        raise RuntimeError(detail)

    return stdout
