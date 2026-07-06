from __future__ import annotations

import logging
import re
import time

from kubernetes import client, config
from kubernetes.stream import stream

logger = logging.getLogger(__name__)


class PodExecTimeoutError(TimeoutError):
    """Raised when an exec command exceeds the configured timeout."""


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

_TIMEOUT_EXIT_CODES = frozenset({124, 137, 143})


def exec_in_pod(
    core_v1: client.CoreV1Api,
    namespace: str,
    pod_name: str,
    command: str,
    timeout: int = 30,
) -> str:
    # Kill the process inside the pod; client-side timeout is a backup.
    client_timeout = timeout + 5
    started = time.monotonic()
    response = stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=[
            "timeout",
            "-s",
            "KILL",
            str(timeout),
            "/bin/sh",
            "-c",
            command,
        ],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
        _request_timeout=(timeout, client_timeout),
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    try:
        while response.is_open():
            elapsed = time.monotonic() - started
            if elapsed >= client_timeout:
                raise PodExecTimeoutError(
                    f"exec timed out after {timeout}s in pod {namespace}/{pod_name}"
                )
            response.update(timeout=min(1, client_timeout - elapsed))
            if response.peek_stdout():
                stdout_chunks.append(response.read_stdout())
            if response.peek_stderr():
                stderr_chunks.append(response.read_stderr())
    finally:
        response.close()

    exit_code = response.returncode
    stdout = "".join(stdout_chunks).strip()
    stderr = "".join(stderr_chunks).strip()
    if exit_code in _TIMEOUT_EXIT_CODES:
        raise PodExecTimeoutError(
            f"exec timed out after {timeout}s in pod {namespace}/{pod_name}"
        )
    if exit_code not in (0, None):
        detail = stderr or stdout or f"exit code {exit_code}"
        raise RuntimeError(detail)

    return stdout
