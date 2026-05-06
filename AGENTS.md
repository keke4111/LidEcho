# Repository Guidelines

## Project Structure & Module Organization

This repository is a small single-script utility for Ubuntu lid behavior.

- `lid_manager.py`: main Python daemon-style script. It detects YesPlayMusic audio streams and manages the temporary `systemd-logind` inhibitor.
- `README.md`: user-facing setup, usage, and safety notes.
- `AGENTS.md`: contributor and agent guidance.

There is currently no package layout, test directory, or asset directory. Keep the project flat unless a new module or test suite clearly justifies more structure.

## Build, Test, and Development Commands

Run locally:

```bash
python3 lid_manager.py
```

Show CLI options:

```bash
python3 lid_manager.py --help
```

Check Python syntax without writing cache files into the repository:

```bash
python3 -c "import py_compile; py_compile.compile('lid_manager.py', cfile='/tmp/lid_manager.pyc', doraise=True)"
```

Verify runtime inhibitor state:

```bash
systemd-inhibit --list
pgrep -af lid_manager.py
```

## Coding Style & Naming Conventions

Use Python 3 with standard-library features where practical. Keep the script dependency-light; `dbus` is the only Python system binding currently required.

- Use 4-space indentation.
- Prefer small functions with explicit names, such as `target_audio_is_running`.
- Constants use `UPPER_SNAKE_CASE`.
- Avoid writing config files or service files unless the project scope changes.
- Keep comments sparse and useful; explain safety-sensitive behavior, not obvious assignments.

## Testing Guidelines

There is no formal test framework yet. For changes, perform at least:

```bash
python3 lid_manager.py --help
python3 -c "import py_compile; py_compile.compile('lid_manager.py', cfile='/tmp/lid_manager.pyc', doraise=True)"
```

For behavior changes, manually verify single-instance locking, `Ctrl+C` cleanup, and `systemd-inhibit --list` before and after YesPlayMusic playback.

## Commit & Pull Request Guidelines

This directory is not currently initialized as a Git repository, so there is no existing commit history to follow. If version control is added, use concise imperative commits, for example:

```text
Add single-instance lock
Improve terminal relaunch handling
Document manual run workflow
```

Pull requests should describe the user-visible behavior change, list manual verification steps, and call out any safety implications involving D-Bus, inhibitors, terminal relaunching, or filesystem writes.

## Security & Configuration Tips

Do not modify `/etc/systemd/logind.conf`, install user services, or add autostart behavior without explicit user approval. The intended design is temporary and reversible: hold a logind inhibitor fd while needed, then release it on stop, crash, or normal exit.
