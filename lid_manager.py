#!/usr/bin/env python3
"""Temporarily inhibit lid suspend while monitored audio apps are playing."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import dbus


DEFAULT_TARGETS = (
    "YesPlayMusic",
    "lhpfahjibimgggaacnfckmefbooiklib",
    "crx_lhpfahjibimgggaacnfckmefbooiklib",
    "Apple Music 古典乐",
)
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_RELEASE_GRACE = 30.0

LOG = logging.getLogger("lid-manager")
TERMINAL_ENV_FLAG = "LIDECHO_IN_TERMINAL"
LOCK_FILE_NAME = "lidecho.lock"


@dataclass(frozen=True)
class AudioStream:
    state: str
    fields: tuple[str, ...]


class LidInhibitor:
    """Holds a logind inhibitor fd for as long as lid suspend is blocked."""

    def __init__(self) -> None:
        self._fd: int | None = None

    @property
    def active(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self.active:
            return

        bus = dbus.SystemBus()
        manager = bus.get_object(
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
        )
        inhibit = manager.get_dbus_method(
            "Inhibit",
            "org.freedesktop.login1.Manager",
        )
        fd = inhibit(
            "handle-lid-switch",
            "LidEcho",
            "Monitored audio app is playing",
            "block",
        )
        self._fd = fd.take()
        LOG.info("inhibitor acquired")

    def release(self) -> None:
        if self._fd is None:
            return

        fd = self._fd
        self._fd = None
        try:
            os.close(fd)
        except OSError as exc:
            LOG.warning("failed to close inhibitor fd: %s", exc)
        else:
            LOG.info("inhibitor released")


def run_command(command: list[str], timeout: float = 3.0) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.debug("%s failed: %s", command[0], exc)
        return None

    if result.returncode != 0:
        LOG.debug("%s exited with %s: %s", command[0], result.returncode, result.stderr.strip())
        return None
    return result.stdout


def parse_pactl_sink_inputs(output: str) -> list[AudioStream]:
    streams: list[AudioStream] = []
    current: list[str] = []

    for line in output.splitlines():
        if line.startswith("Sink Input #"):
            if current:
                streams.append(stream_from_pactl_block(current))
            current = [line]
        elif current:
            current.append(line)

    if current:
        streams.append(stream_from_pactl_block(current))

    return streams


def stream_from_pactl_block(lines: list[str]) -> AudioStream:
    state = ""
    fields: list[str] = []

    for line in lines:
        stripped = line.strip()
        fields.append(stripped)
        if stripped.startswith("State:"):
            state = stripped.split(":", 1)[1].strip().lower()

    return AudioStream(state=state, fields=tuple(fields))


def pactl_streams() -> list[AudioStream] | None:
    if not shutil.which("pactl"):
        return None

    output = run_command(["pactl", "list", "sink-inputs"])
    if output is None:
        return None
    return parse_pactl_sink_inputs(output)


def pw_dump_streams() -> list[AudioStream] | None:
    if not shutil.which("pw-dump"):
        return None

    output = run_command(["pw-dump"])
    if output is None:
        return None

    try:
        objects = json.loads(output)
    except json.JSONDecodeError as exc:
        LOG.debug("pw-dump returned invalid JSON: %s", exc)
        return None

    streams: list[AudioStream] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue

        info = obj.get("info")
        if not isinstance(info, dict):
            continue

        props = info.get("props")
        if not isinstance(props, dict):
            props = {}

        media_class = str(props.get("media.class", "")).lower()
        if "stream" not in media_class and "audio" not in media_class:
            continue

        state = str(info.get("state", obj.get("state", ""))).lower()
        fields = [f"{key}={value}" for key, value in props.items()]
        fields.append(f"state={state}")
        streams.append(AudioStream(state=state, fields=tuple(fields)))

    return streams


def wpctl_streams() -> list[AudioStream] | None:
    if not shutil.which("wpctl"):
        return None

    output = run_command(["wpctl", "status"])
    if output is None:
        return None

    streams: list[AudioStream] = []
    for line in output.splitlines():
        lower = line.lower()
        if "stream" in lower or "sink input" in lower or "output" in lower:
            streams.append(AudioStream(state="unknown", fields=(line.strip(),)))
    return streams


def state_is_running(state: str) -> bool:
    return state in {"running", "run"}


def process_ids_from_fields(fields: Iterable[str]) -> set[int]:
    process_ids: set[int] = set()

    for field in fields:
        if "application.process.id" not in field:
            continue
        _, _, value = field.partition("=")
        value = value.strip().strip('"')
        if value.isdigit():
            process_ids.add(int(value))

    return process_ids


def process_cmdline(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as cmdline_file:
            data = cmdline_file.read()
    except OSError:
        return None

    if not data:
        return None
    return data.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def enrich_fields_with_process_cmdline(fields: tuple[str, ...]) -> tuple[str, ...]:
    enriched = list(fields)

    for pid in process_ids_from_fields(fields):
        cmdline = process_cmdline(pid)
        if cmdline:
            enriched.append(f"process.cmdline={cmdline}")

    return tuple(enriched)


def fields_match_target(fields: Iterable[str], targets: tuple[str, ...]) -> bool:
    text = "\n".join(fields).lower()
    return any(target.lower() in text for target in targets)


def target_audio_is_running(targets: tuple[str, ...]) -> bool:
    stream_sources = (pactl_streams, pw_dump_streams, wpctl_streams)

    for source in stream_sources:
        streams = source()
        if streams is None:
            continue

        matched_unknown_state = False
        for stream in streams:
            fields = enrich_fields_with_process_cmdline(stream.fields)
            if not fields_match_target(fields, targets):
                continue
            if state_is_running(stream.state):
                return True
            if stream.state in {"", "unknown"}:
                matched_unknown_state = True

        if matched_unknown_state:
            LOG.debug("matched target stream, but playback state was not available")
        return False

    LOG.warning("no supported audio query command found; install pulseaudio-utils or pipewire-bin")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inhibit lid suspend only while monitored audio apps are playing.",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=list(DEFAULT_TARGETS),
        help="audio application name substring to match; can be passed multiple times",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="poll interval in seconds",
    )
    parser.add_argument(
        "--release-grace",
        type=float,
        default=DEFAULT_RELEASE_GRACE,
        help="seconds to wait before releasing after playback stops",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging",
    )
    return parser.parse_args()


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def lock_file_paths() -> list[str]:
    paths: list[str] = []
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        paths.append(os.path.join(runtime_dir, LOCK_FILE_NAME))
    paths.append(os.path.join("/tmp", f"lidecho-{os.getuid()}.lock"))
    return paths


def acquire_single_instance_lock() -> object | None:
    last_error: OSError | None = None

    for path in lock_file_paths():
        try:
            lock_file = open(path, "a+", encoding="utf-8")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            print("LidEcho is already running.")
            return None
        except OSError as exc:
            last_error = exc
            continue

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        return lock_file

    print(f"Failed to acquire LidEcho lock: {last_error}", file=sys.stderr)
    return None


def relaunch_in_terminal_if_needed(script_args: list[str]) -> bool:
    if sys.stdout.isatty() or os.environ.get(TERMINAL_ENV_FLAG) == "1":
        return False

    script_path = os.path.realpath(__file__)
    script_dir = os.path.dirname(script_path)
    quoted_script = shlex.quote(script_path)
    quoted_args = " ".join(shlex.quote(arg) for arg in script_args)
    script_invocation = f"python3 {quoted_script}"
    if quoted_args:
        script_invocation = f"{script_invocation} {quoted_args}"
    command = (
        f"{TERMINAL_ENV_FLAG}=1 {script_invocation}; "
        'echo; '
        'read -r -p "LidEcho has exited. Press Enter to close this window..."'
    )

    terminal_commands = [
        [
            "ptyxis",
            "--new-window",
            "--working-directory",
            script_dir,
            "--",
            "bash",
            "-lc",
            command,
        ],
        ["gnome-terminal", "--working-directory", script_dir, "--", "bash", "-lc", command],
        ["kgx", "--working-directory", script_dir, "--", "bash", "-lc", command],
        ["x-terminal-emulator", "-e", "bash", "-lc", command],
    ]

    for terminal_command in terminal_commands:
        if not shutil.which(terminal_command[0]):
            continue
        try:
            subprocess.Popen(terminal_command)
        except OSError:
            continue
        return True

    return False


def main() -> int:
    args = parse_args()
    if relaunch_in_terminal_if_needed(sys.argv[1:]):
        return 0

    configure_logging(args.debug)

    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        return 1

    targets = tuple(args.target)
    stop_requested = False
    inhibitor = LidInhibitor()
    last_seen_playing: float | None = None

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop_requested
        LOG.info("received signal %s, exiting", signum)
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    LOG.info(
        "started; targets=%s interval=%.1fs release_grace=%.1fs",
        ", ".join(targets),
        args.interval,
        args.release_grace,
    )

    try:
        while not stop_requested:
            now = time.monotonic()
            playing = target_audio_is_running(targets)

            if playing:
                last_seen_playing = now
                if not inhibitor.active:
                    inhibitor.acquire()
            elif inhibitor.active:
                elapsed = now - last_seen_playing if last_seen_playing is not None else args.release_grace
                if elapsed >= args.release_grace:
                    inhibitor.release()

            time.sleep(max(args.interval, 1.0))
    finally:
        inhibitor.release()
        instance_lock.close()
        LOG.info("stopped")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
