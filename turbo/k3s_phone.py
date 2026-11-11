"""k3s adapter for phones: k3s owns the phone slot, adb does the work.

Android cannot join k3s, so each phone is an extended resource on groot
(``z0.dev/phone-<name>: 1``). Before a phone run, a tiny lease pod requests
that resource; the scheduler admits one holder per phone, and ``kubectl get
pods -n phones -l app=hc-turbo`` shows what every phone is doing. The run
itself is unchanged: the same host command drives the phone over adb.

    python3 -m turbo.k3s_phone --phone tab --token RUN --deadline 3600 -- CMD...
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time

NAMESPACE = "phones"
NODE = "groot"
IMAGE = "python:3.13.13-slim-trixie"  # already cached for phone-queue
ADMIT_WAIT = 120
RETRY_EXIT = 75


def resource(phone: str) -> str:
    return f"z0.dev/phone-{phone}"


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    out = subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, timeout=60)
    if check and out.returncode:
        raise RuntimeError(f"kubectl {' '.join(args[:3])}: {out.stderr.strip()}")
    return out.stdout


def ensure_slot(phone: str) -> None:
    """Self-heal: advertise the phone's slot on groot if it is missing."""
    cap = json.loads(kubectl("get", "node", NODE, "-o", "jsonpath={.status.capacity}") or "{}")
    if cap.get(resource(phone)) == "1":
        return
    key = resource(phone).replace("/", "~1")
    patch = json.dumps([{"op": "add", "path": f"/status/capacity/{key}", "value": "1"}])
    kubectl("patch", "node", NODE, "--subresource=status", "--type=json", "-p", patch)


def pod_manifest(phone: str, token: str, deadline: int, holder: int) -> dict:
    slot = {resource(phone): "1", "cpu": "1m", "memory": "8Mi"}
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": f"hc-{phone}-{token[:40].lower()}", "namespace": NAMESPACE,
                     "labels": {"app": "hc-turbo", "z0.dev/phone": phone},
                     "annotations": {"z0.dev/holder": f"{socket.gethostname()}:{holder}",
                                     "z0.dev/run": token}},
        "spec": {
            "nodeSelector": {"kubernetes.io/hostname": NODE},
            "tolerations": [{"key": "z0.dev/host", "operator": "Exists"}],
            "restartPolicy": "Never", "activeDeadlineSeconds": deadline,
            "terminationGracePeriodSeconds": 0, "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "lease", "image": IMAGE, "imagePullPolicy": "IfNotPresent",
                "command": ["sleep", str(deadline)],
                "resources": {"requests": slot, "limits": slot},
                "securityContext": {"allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]}},
            }],
        },
    }


def holder_alive(annotation: str) -> bool:
    host, _, pid = annotation.partition(":")
    if host != socket.gethostname() or not pid.isdigit():
        return True  # not ours to judge
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reap_stale(phone: str) -> None:
    """A SIGKILLed holder leaves its lease; drop leases whose holder is gone."""
    pods = json.loads(kubectl("get", "pods", "-n", NAMESPACE, "-l",
                              f"app=hc-turbo,z0.dev/phone={phone}", "-o", "json"))["items"]
    for pod in pods:
        ann = pod["metadata"].get("annotations", {}).get("z0.dev/holder", "")
        if pod["status"].get("phase") in ("Succeeded", "Failed") or not holder_alive(ann):
            kubectl("delete", "pod", "-n", NAMESPACE, pod["metadata"]["name"],
                    "--wait=false", check=False)


def wait_running(name: str, seconds: int) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        phase = kubectl("get", "pod", "-n", NAMESPACE, name, "-o", "jsonpath={.status.phase}", check=False)
        if phase == "Running":
            return True
        if phase in ("Failed", "Succeeded"):
            return False
        time.sleep(1)
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--deadline", type=int, required=True)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        ap.error("command required")
    manifest = pod_manifest(args.phone, args.token, args.deadline + 300, os.getpid())
    name = manifest["metadata"]["name"]
    state: dict = {"child": None, "stop": None}

    def on_signal(signum, _frame):
        # Installed before the pod exists, so no signal can leak a lease.
        state["stop"] = signum
        if state["child"] is not None:
            state["child"].send_signal(signum)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    try:
        ensure_slot(args.phone)
        reap_stale(args.phone)
        kubectl("apply", "-f", "-", stdin=json.dumps(manifest))
        if not wait_running(name, ADMIT_WAIT):
            print(f"k3s-phone: {resource(args.phone)} not admitted within {ADMIT_WAIT}s", file=sys.stderr)
            return RETRY_EXIT
        if state["stop"]:
            return 128 + state["stop"]
        print(f"k3s-phone: holding {resource(args.phone)} via pod {NAMESPACE}/{name}", file=sys.stderr, flush=True)
        state["child"] = subprocess.Popen(command)
        if state["stop"]:
            state["child"].send_signal(state["stop"])
        return state["child"].wait()
    finally:
        kubectl("delete", "pod", "-n", NAMESPACE, name, "--wait=false", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
