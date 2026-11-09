"""Pair one explicitly chosen local executor; never edit runtime profiles or install services.

Run as `python -m slate_connect` (or the packaged slate-connect entrypoint). Pairing codes
are prompted with getpass or read from an owner-only --code-file, never command arguments.
The server credential only authorizes Slate API calls; --exec separately authorizes the
local process, which can access this user's files. Doctor never runs it implicitly.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import getpass
import hashlib
import importlib

# Python >=3.11 is required by this package; pre-3.7 compatibility is not supported.
import importlib.resources  # nosemgrep: python.lang.compatibility.python37.python37-compatibility-importlib2  # noqa: E501
import importlib.util
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import warnings
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx

VERSION = "1"
HEARTBEAT_SECONDS = 30
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ADAPTERS = {"hermes-gateway": "hermes/run_task.py", "hermes-cli": "hermes/run_task_cli.py"}


class SetupError(Exception):
    """A safe, actionable message, never raw remote or subprocess diagnostics."""


def origin(value: str) -> str:
    try:
        p = urlsplit(value)
        _ = p.port
    except ValueError:
        raise SetupError("Use a valid server origin, such as https://slate.example.") from None
    if (
        p.scheme not in ("https", "http")
        or not p.hostname
        or p.username
        or p.password
        or p.query
        or p.fragment
        or p.path not in ("", "/")
        or "\\" in value
        or any(ch.isspace() for ch in value)
    ):
        raise SetupError("Use only the server origin: no credentials, path, query or fragment.")
    if p.scheme == "http":
        try:
            loopback = ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:
            loopback = p.hostname == "localhost"
        if not loopback:
            raise SetupError("Non-loopback servers require HTTPS.")
    return value.rstrip("/")


def _no_links(path: Path):
    for part in [path, *path.parents]:
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise SetupError("Connection storage must not use symbolic links or junctions.")


@lru_cache(maxsize=1)
def _windows_sid() -> str:
    exe = Path(os.environ["SYSTEMROOT"]) / "System32/whoami.exe"
    result = subprocess.run(
        [str(exe), "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    sid = next(csv.reader([result.stdout.strip()]))[-1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise SetupError("Cannot determine the current Windows user for private storage.")
    return sid


def _windows_security(path: Path, *, set_private: bool = False) -> str:
    """Set/read a protected owner-only Windows DACL using OS APIs (no optional package)."""
    from ctypes import wintypes as w

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        ctypes.POINTER(w.LPVOID),
        ctypes.POINTER(w.DWORD),
    ]
    adv.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID]
    adv.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        w.LPVOID,
        w.DWORD,
        w.DWORD,
        ctypes.POINTER(w.LPWSTR),
        ctypes.POINTER(w.DWORD),
    ]
    kernel.LocalFree.argtypes = [w.LPVOID]
    if set_private:
        pointer = w.LPVOID()
        flags = "OICI" if path.is_dir() else ""
        descriptor = f"D:P(A;{flags};FA;;;{_windows_sid()})"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            descriptor, 1, ctypes.byref(pointer), None
        ):
            raise SetupError("Windows could not create a private credential ACL.")
        try:
            if not adv.SetFileSecurityW(str(path), 4 | 0x80000000, pointer):
                raise SetupError("Windows could not protect the credential directory.")
        finally:
            kernel.LocalFree(pointer)
    needed = w.DWORD()
    adv.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(needed))
    buf = ctypes.create_string_buffer(needed.value)
    if not adv.GetFileSecurityW(str(path), 4, buf, needed, ctypes.byref(needed)):
        raise SetupError("Cannot inspect Windows credential permissions.")
    text = w.LPWSTR()
    if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        buf, 1, 4, ctypes.byref(text), None
    ):
        raise SetupError("Cannot verify Windows credential permissions.")
    try:
        return text.value
    finally:
        kernel.LocalFree(text)


def check_private(path: Path) -> None:
    _no_links(path)
    if not path.exists():
        raise SetupError("Local connection file is missing. Connect again.")
    if os.name == "nt":
        dacl = _windows_security(path)
        aces = re.findall(r"\(([^)]+)\)", dacl)
        if not aces or any(
            ace.split(";")[0] != "A" or ace.split(";")[-1] != _windows_sid() for ace in aces
        ):
            raise SetupError("Connection storage is not owner-only; fix its Windows permissions.")
    else:
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SetupError("Connection storage must belong to you and deny group/other access.")


def _private_dir(path: Path):
    _no_links(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    if os.name == "nt":
        _windows_security(path, set_private=True)
    check_private(path)


def _write_new(path: Path, value: dict):
    _no_links(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # The parent is already private: no interval with publicly readable credentials.
        check_private(path)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            fd = -1
            json.dump(value, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def default_root() -> Path:
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "Slate/connections"
        )
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "slate/connections"


def _request(client, method, url, *, token=None, body=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = client.request(method, url, headers=headers, json=body, follow_redirects=False)
    except httpx.HTTPError:
        raise SetupError(
            "Server unavailable. Check its address, HTTPS certificate and network."
        ) from None
    if response.status_code >= 300:
        advice = {
            401: "Credential rejected. Reconnect this device.",
            403: "Permission denied. Ask an account manager to check the selected scope.",
            410: "Pairing code expired or already used. Create a new connection.",
            426: "Update the local runner to match the server.",
            429: "Too many attempts. Wait a minute before trying again.",
        }
        raise SetupError(
            advice.get(
                response.status_code, f"Server refused the request (HTTP {response.status_code})."
            )
        )
    try:
        return response.json() if response.content else None
    except ValueError:
        raise SetupError("Server returned an invalid response. Check the server origin.") from None


@lru_cache(maxsize=1)
def _runner_module():
    try:
        return importlib.import_module("slate_runner")
    except ImportError:
        # Running this standalone source beside its canonical runner is also supported.
        path = Path(__file__).with_name("slate_runner.py")
        if not path.is_file():
            raise SetupError(
                "Install the slate-runner package in this Python environment."
            ) from None
        spec = importlib.util.spec_from_file_location("slate_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _health(client, url):
    data = _request(client, "GET", url + "/healthz")
    needed = data.get("min_runner_protocol") if isinstance(data, dict) else None
    if not isinstance(needed, int) or needed > _runner_module().RUNNER_PROTOCOL:
        raise SetupError("Update the local runner to match the server protocol before connecting.")
    return data


def _asset(adapter):
    try:
        path = importlib.resources.files("slate_runner_assets").joinpath(ADAPTERS[adapter])
        if not path.is_file():
            raise FileNotFoundError
        return Path(str(path))
    except (ModuleNotFoundError, FileNotFoundError):
        raise SetupError(
            "Install the current slate-runner package with adapter assets, then reconnect."
        ) from None


def _command(config):
    if config["adapter"] == "custom":
        return config["command"]
    path = _asset(config["adapter"])
    argv = [sys.executable, str(path)]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def _runtime(config):
    if not Path(config["cwd"]).is_dir():
        raise SetupError(
            "Project directory is missing. Restore it or reconnect with the correct directory."
        )
    if config["adapter"] == "custom":
        try:
            parts = shlex.split(config.get("command") or "")
        except ValueError:
            parts = []
        if not parts or not shutil.which(parts[0]):
            raise SetupError(
                "Local executable unavailable. Install it or reconnect with the correct command."
            )
    else:
        _asset(config["adapter"])
        if not config.get("profile"):
            raise SetupError("Select a local runtime profile explicitly when connecting.")
        if config["adapter"] == "hermes-cli" and not shutil.which("hermes"):
            raise SetupError(
                "Runtime CLI unavailable. Install it and check its official --help locally."
            )
        if config["adapter"] == "hermes-gateway" and not config.get("endpoint"):
            raise SetupError("Choose the runtime gateway endpoint explicitly when connecting.")
    key = config.get("runtime_key_env")
    if key and not os.environ.get(key):
        raise SetupError(
            "The explicitly selected runtime credential environment variable is unset."
        )


def connect_local(
    *,
    client,
    url: str,
    code: str,
    root: Path,
    name: str,
    adapter: str,
    command: str | None,
    cwd: Path,
    device_name: str,
    profile: str | None,
    endpoint: str | None,
    runtime_key_env: str | None,
    probe_command: str | None = None,
) -> dict:
    url = origin(url)
    if not NAME.fullmatch(name) or (
        os.name == "nt"
        and name.upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *[f"COM{i}" for i in range(10)],
            *[f"LPT{i}" for i in range(10)],
        }
    ):
        raise SetupError("Choose a simple local name using letters, numbers, dash or underscore.")
    if adapter not in ("custom", *ADAPTERS):
        raise SetupError("Choose custom, hermes-cli or hermes-gateway.")
    if runtime_key_env and (not ENV_NAME.fullmatch(runtime_key_env) or adapter != "hermes-gateway"):
        raise SetupError(
            "Runtime credential environment variables are supported for the gateway adapter."
        )
    if endpoint:
        # Runtime endpoint may include a profile path, but no URL credentials or queries.
        p = urlsplit(endpoint)
        origin(f"{p.scheme}://{p.netloc}")
        if p.query or p.fragment:
            raise SetupError("Runtime endpoints cannot contain a query or fragment.")
    root = Path(root).absolute()
    directory = root / name
    _no_links(root)
    if directory.exists():
        raise SetupError(
            "Connection already exists. Disconnect it and choose a new local name; "
            "nothing overwritten."
        )
    config = {
        "url": url,
        "adapter": adapter,
        "command": command if adapter == "custom" else None,
        "cwd": str(Path(cwd).absolute()),
        "profile": profile,
        "endpoint": endpoint,
        "runtime_key_env": runtime_key_env,
        "name": name,
        "probe_command": probe_command if adapter == "custom" else None,
    }
    _runtime(config)
    _health(client, url)
    if not root.exists():
        _private_dir(root)
    check_private(root)
    _private_dir(directory)  # reserve before consuming a one-use code
    exchanged = False
    try:
        result = _request(
            client,
            "POST",
            url + "/api/v1/agent-connections/exchange",
            body={
                "code": code.strip(),
                "adapter": adapter,
                "device_name": device_name,
                "version": VERSION,
                "capabilities": _capabilities(config),
            },
        )
        exchanged = True
        connection = result["connection"]
        config.update(
            connection_id=connection["id"],
            account_id=connection["account_id"],
            account_handle=result["account_handle"],
            list_ids=connection["list_ids"],
            server_protocol=result["server_protocol"],
        )
        _write_new(directory / "credentials.json", {"token": result["token"]})
        _write_new(directory / "config.json", config)
        # Verify the exact target by authenticated readback, not exchange success alone.
        rows = _request(
            client,
            "GET",
            url + "/api/v1/agent-connections?account_id=" + config["account_id"],
            token=result["token"],
        )["connections"]
        if not any(row["id"] == config["connection_id"] for row in rows):
            raise SetupError(
                "Credential saved, but connection verification failed. Run doctor before run."
            )
        return config
    except Exception:
        if not exchanged:
            directory.rmdir()
        # After an ambiguous/successful exchange preserve saved credentials for recovery;
        # do not silently delete the only local copy of a live credential.
        raise


def _load(directory):
    directory = Path(directory)
    check_private(directory)
    for name in ("config.json", "credentials.json"):
        check_private(directory / name)
    try:
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        credential = json.loads((directory / "credentials.json").read_text(encoding="utf-8"))
        origin(config["url"])
        return config, credential["token"]
    except (ValueError, KeyError):
        raise SetupError(
            "Invalid local connection files. Reconnect without overwriting runtime profiles."
        ) from None


def _work_kinds(config):
    # Canonical packaged adapters' offline --capabilities declarations. Unknown custom
    # commands conservatively receive tasks only; never infer chat from executable presence.
    return ["task", "chat", "doc"] if config["adapter"] == "hermes-gateway" else ["task"]


def _capabilities(config):
    kinds = _work_kinds(config)
    if config["adapter"] == "hermes-gateway" or config.get("probe_command"):
        kinds.append("probe")
    return kinds


def _fingerprint(config):
    payload = {"config": config, "version": VERSION}
    key = config.get("runtime_key_env")
    if key:
        payload["credential_sha256"] = hashlib.sha256(os.environ.get(key, "").encode()).hexdigest()
    if config["adapter"] in ADAPTERS:
        payload["adapter_sha256"] = hashlib.sha256(
            _asset(config["adapter"]).read_bytes()
        ).hexdigest()
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _proof_matches(config, directory):
    try:
        path = Path(directory) / "probe-receipt.json"
        if not path.exists():
            return False
        check_private(path)
        proof = json.loads(path.read_text(encoding="utf-8"))
        return (
            proof.get("fingerprint") == _fingerprint(config)
            and bool(proof.get("verified_at"))
            and bool(re.fullmatch(r"[0-9a-f]{64}", proof.get("nonce_sha256", "")))
        )
    except (OSError, ValueError, TypeError, SetupError):
        return False


def _probe(config, directory):
    nonce = secrets.token_hex(32)
    if config["adapter"] == "hermes-gateway":
        argv = [sys.executable, str(_asset(config["adapter"])), "--probe"]
    elif config["adapter"] == "custom" and config.get("probe_command"):
        argv = shlex.split(config["probe_command"])
    else:
        return None, nonce
    env = _environment(config, directory)
    # A probe is not a task and is never handed Slate authority/context.
    env = {k: v for k, v in env.items() if not k.startswith("SLATE_")}
    try:
        result = subprocess.run(
            argv,
            input=json.dumps({"nonce": nonce}),
            text=True,
            capture_output=True,
            timeout=90,
            cwd=str(config["cwd"]),
            env=env,
        )
        proof = json.loads(result.stdout)
        if (
            result.returncode != 0
            or not isinstance(proof, dict)
            or proof.get("ok") is not True
            or proof.get("nonce") != nonce
        ):
            return None, nonce
        if config["adapter"] == "hermes-gateway" and (
            proof.get("terminal") != "run.completed" or not proof.get("session_id")
        ):
            return None, nonce
        return proof, nonce
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, nonce


def doctor(directory: Path, *, client, test: bool = False) -> dict:
    config, token = _load(directory)
    health = _health(client, config["url"])
    api = config["url"] + "/api/v1"
    me = _request(client, "GET", api + "/me", token=token)
    if me.get("account", {}).get("id") != config["account_id"]:
        raise SetupError("Credential identity does not match this connection. Reconnect.")
    rows = _request(
        client, "GET", api + "/agent-connections?account_id=" + config["account_id"], token=token
    )["connections"]
    row = next((x for x in rows if x["id"] == config["connection_id"]), None)
    if not row or row.get("state") == "revoked":
        raise SetupError("This connection is no longer authorized. Reconnect.")
    work_kinds = [kind for kind in _work_kinds(config) if kind in row.get("capabilities", [])]
    lists = _request(client, "GET", api + "/lists", token=token)
    if not set(config["list_ids"]) <= {x["id"] for x in lists}:
        raise SetupError("Selected list access changed. Ask a manager to restore it or reconnect.")
    try:
        _runtime(config)
    except SetupError as error:
        return {"state": "runtime_unavailable", "detail": str(error)}
    if test:
        if "probe" not in _capabilities(config) or "probe" not in row.get("capabilities", []):
            return {
                "state": "needs_setup",
                "detail": "No supported probe configured. Reconnect with a gateway or explicit "
                "--probe-command; no test was run.",
            }
        receipt = Path(directory) / "probe-receipt.json"
        if receipt.exists():
            check_private(receipt)
            receipt.unlink()  # a failed retest must never retain earlier proof
        proof, nonce = _probe(config, directory)
        check = "probe_passed" if proof else "needs_setup"
        _request(
            client,
            "POST",
            api + "/agent-connections/" + config["connection_id"] + "/heartbeat",
            token=token,
            body={"check": check},
        )
        rows = _request(
            client,
            "GET",
            api + "/agent-connections?account_id=" + config["account_id"],
            token=token,
        )["connections"]
        exact = next((x for x in rows if x["id"] == config["connection_id"]), None)
        if not proof:
            return {
                "state": "needs_setup",
                "detail": "Agent test failed or unconfirmed. Inspect the native test session; "
                "no ready claim was made.",
            }
        if not exact or exact.get("state") != "ready" or not exact.get("verified_at"):
            raise SetupError(
                "Agent test returned, but server verification readback failed. "
                "Run doctor before retrying."
            )
        _write_new(
            receipt,
            {
                "fingerprint": _fingerprint(config),
                "nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
                "verified_at": exact["verified_at"],
            },
        )
        return {"state": "ready", "detail": "Agent test passed; connection active."}
    if row.get("verified_at") and _proof_matches(config, directory):
        return {
            "state": "ready" if row.get("state") == "ready" else "connected",
            "detail": "Agent test previously passed; no new test run.",
            "verified_at": row["verified_at"],
            "work_kinds": work_kinds,
        }
    return {
        "state": "connected",
        "detail": "Connected; agent test not run. "
        "Runtime credentials and execution remain unverified.",
        "work_kinds": work_kinds,
        "server_version": health.get("version"),
        "server_protocol": health["min_runner_protocol"],
    }


def _environment(config, directory):
    env = dict(os.environ)
    env["SLATE_WORK_KINDS"] = ",".join(_work_kinds(config))
    # Do not let an unrelated parent profile select a different home.
    if config["adapter"] in ADAPTERS:
        env.pop("HERMES_HOME", None)
        env["HERMES_PROFILE"] = config["profile"]
        env["HERMES_CWD"] = str(config["cwd"])
        env["HERMES_STATE"] = str(directory / "approvals.json")
        env.pop("HERMES_API_KEY", None)
        if config.get("endpoint"):
            env["HERMES_API_URL"] = config["endpoint"]
        if config.get("runtime_key_env"):
            env["HERMES_API_KEY"] = os.environ[config["runtime_key_env"]]
    return env


def run_local(directory: Path, *, client, once: bool = False, item: str | None = None) -> int:
    config, token = _load(directory)
    status = doctor(directory, client=client)
    if status["state"] not in ("connected", "ready"):
        raise SetupError(status["detail"])
    if status["state"] != "ready":
        print(
            "Warning: agent test not verified; explicitly running unverified executor.",
            file=sys.stderr,
        )
    kinds = status.get("work_kinds", _work_kinds(config))
    if not kinds or (item and "task" not in kinds):
        raise SetupError("This connection does not support the requested work kind. Reconnect.")
    environment = _environment(config, directory)
    environment["SLATE_WORK_KINDS"] = ",".join(kinds)
    runner = _runner_module().Runner(
        config["url"],
        token,
        _command(config),
        client=client,
        state_path=directory / "runner-state.json",
        env=environment,
        cwd=config["cwd"],
        log=lambda message: None,
    )  # never forward raw remote/runtime diagnostics
    stop = threading.Event()
    failed = threading.Event()
    stopping_lock = threading.RLock()
    stopper = None

    def request_stop(*_):
        # Signal handlers execute inside the main run stack. Waiting there for the
        # runner's own active run would deadlock; only one independent stopper may wait.
        nonlocal stopper
        with stopping_lock:
            if stopper is None:
                stopper = threading.Thread(target=runner.stop, daemon=True)
                stopper.start()

    heartbeat_url = (
        config["url"] + "/api/v1/agent-connections/" + config["connection_id"] + "/heartbeat"
    )

    def beat():
        _request(
            client,
            "POST",
            heartbeat_url,
            token=token,
            body={"check": "ready" if _proof_matches(config, directory) else "connected"},
        )

    def monitor():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                beat()
            except SetupError:
                failed.set()
                request_stop()
                return

    beat()  # fail before any task mutation if connection authority has disappeared
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    old_signals = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_signals[sig] = signal.signal(sig, request_stop)
    try:
        result = runner.once(item) if once else runner.forever()
        if failed.is_set():
            raise SetupError(
                "Connection heartbeat failed; local runner stopped. Run doctor before restarting."
            )
        return result if isinstance(result, int) else 0
    except KeyboardInterrupt:
        return 130
    except (httpx.HTTPError, _runner_module().SlateHTTPError):
        raise SetupError(
            "Runner request failed. Run doctor before restarting; task state was not guessed."
        ) from None
    finally:
        stop.set()
        request_stop()
        stopper.join()
        thread.join(timeout=35)
        for sig, old in old_signals.items():
            signal.signal(sig, old)


def disconnect(directory: Path, *, client) -> None:
    config, token = _load(directory)
    _request(
        client,
        "DELETE",
        config["url"] + "/api/v1/agent-connections/" + config["connection_id"],
        token=token,
    )
    try:
        result = client.get(
            config["url"] + "/api/v1/me",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
    except httpx.HTTPError:
        raise SetupError(
            "Disconnect sent but not verified. Local credentials retained; "
            "retry after checking the server."
        ) from None
    if result.status_code != 401:
        raise SetupError("Revocation is not verified. Local credentials retained.")
    (directory / "credentials.json").unlink()


def local_list(root: Path) -> list[dict]:
    if not root.exists():
        return []
    check_private(root)
    results = []
    for directory in sorted(root.iterdir()):
        if directory.is_dir():
            check_private(directory)
            path = directory / "config.json"
            if path.is_file():
                check_private(path)
                config = json.loads(path.read_text(encoding="utf-8"))
                results.append(
                    {
                        "name": directory.name,
                        "connection_id": config["connection_id"],
                        "state": "saved (health not checked)"
                        if (directory / "credentials.json").exists()
                        else "disconnected",
                    }
                )
    return results


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse otherwise echoes unknown argument values (including an accidental code).
        self.print_usage(sys.stderr)
        self.exit(
            2,
            "slate-connect: invalid arguments; use --help. "
            "Pairing codes are prompted or read from --code-file.\n",
        )


def _prompt_code():
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass("Single-use pairing code: ")
        except getpass.GetPassWarning:
            raise SetupError(
                "No private terminal. Use an owner-only --code-file instead."
            ) from None


def parser():
    p = _SafeParser(description=__doc__, allow_abbrev=False)
    p.add_argument(
        "--root", type=Path, default=default_root(), help="Slate connection storage only"
    )
    commands = p.add_subparsers(dest="action", required=True)
    create = commands.add_parser("connect", allow_abbrev=False)
    create.add_argument("--url", required=True)
    create.add_argument("--code-file", type=Path)
    create.add_argument("--name")
    create.add_argument("--adapter", choices=["custom", *ADAPTERS])
    create.add_argument(
        "--exec",
        dest="command",
        help="Local command, without credentials; separate authority from Slate access",
    )
    create.add_argument(
        "--probe-command",
        help="Trusted custom probe: nonce JSON stdin; {ok:true,nonce:<same>} stdout; zero exit",
    )
    create.add_argument("--cwd", type=Path)
    create.add_argument("--profile")
    create.add_argument("--endpoint")
    create.add_argument(
        "--runtime-key-env",
        help="Explicit existing environment variable for gateway authentication",
    )
    create.add_argument("--device-name", default=socket.gethostname())
    for name in ("doctor", "run", "disconnect"):
        sub = commands.add_parser(name, allow_abbrev=False)
        sub.add_argument("name")
        if name == "doctor":
            sub.add_argument(
                "--test",
                action="store_true",
                help="Only a documented harmless probe; never a real task",
            )
        if name == "run":
            sub.add_argument("--once", action="store_true")
            sub.add_argument("--item")
    commands.add_parser("list")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "list":
            print(json.dumps(local_list(args.root), indent=2))
            return 0
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            if args.action == "connect":
                name = args.name or input("Local connection name: ").strip()
                adapter = (
                    args.adapter
                    or input("Adapter (hermes-gateway / hermes-cli / custom): ").strip()
                )
                profile = args.profile
                endpoint = args.endpoint
                command = args.command
                if adapter in ADAPTERS:
                    profile = profile or input("Chosen local runtime profile: ").strip()
                if adapter == "hermes-gateway":
                    endpoint = (
                        endpoint
                        or input("Chosen gateway endpoint (including profile path): ").strip()
                    )
                if adapter == "custom":
                    command = (
                        command or input("Local command (no secrets; runs only with run): ").strip()
                    )
                cwd = args.cwd or Path(input("Project working directory: ").strip()).expanduser()
                if args.code_file:
                    check_private(args.code_file)
                    code = args.code_file.read_text(encoding="utf-8").strip()
                else:
                    code = _prompt_code()
                connect_local(
                    client=client,
                    url=args.url,
                    code=code,
                    root=args.root,
                    name=name,
                    adapter=adapter,
                    command=command,
                    cwd=cwd,
                    device_name=args.device_name,
                    profile=profile,
                    endpoint=endpoint,
                    runtime_key_env=args.runtime_key_env,
                    probe_command=args.probe_command,
                )
                print(
                    "Connected; agent test not run. "
                    "No runtime profiles changed and no service installed."
                )
                print(f"Start only this connection with: slate-connect run {name}")
                return 0
            if not NAME.fullmatch(args.name):
                raise SetupError("Invalid local connection name.")
            directory = args.root / args.name
            if args.action == "doctor":
                result = doctor(directory, client=client, test=args.test)
                print(json.dumps(result, indent=2))
                return 0 if result["state"] in ("connected", "ready") else 1
            if args.action == "disconnect":
                disconnect(directory, client=client)
                print(
                    "Credential revoked and removed locally. "
                    "Remote execution stopping is not verified."
                )
                return 0
            if args.item and not args.once:
                raise SetupError("Use --once with --item.")
            return run_local(directory, client=client, once=args.once, item=args.item)
    except (SetupError, OSError, ValueError, KeyError, EOFError) as error:
        # OS/JSON exceptions may contain sensitive paths/content; only our own messages escape.
        message = (
            str(error)
            if isinstance(error, SetupError)
            else "Local setup failed. Check paths, permissions and configuration."
        )
        print("slate-connect: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
