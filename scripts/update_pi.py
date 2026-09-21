#!/usr/bin/env python3
"""Securely install Epigraph runtime scripts on a Raspberry Pi over OpenSSH."""

from __future__ import annotations

import argparse
import ipaddress
import re
import secrets
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

RUNTIME_FILES = ("catalog.py", "check_hardware.py", "display_quotes.py")
CATALOG_FILE = "quotes.yaml"
SERVICE_FILE = "epigraph-frame.service"
USERNAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}\Z")
HOST_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
STAGING_TOKEN_PATTERN = re.compile(r"[0-9a-f]{16}\Z")


class UpdateError(RuntimeError):
    """A safe, operator-facing update failure."""


@dataclass(frozen=True)
class SshTarget:
    user: str
    host: str
    port: int = 22

    @property
    def ssh_destination(self) -> str:
        return f"{self.user}@{self.host}"

    @property
    def scp_destination(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.user}@{host}"


CommandRunner = Callable[[Sequence[str]], None]


def validate_username(value: str) -> str:
    username = value.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise argparse.ArgumentTypeError(
            "SSH username must start with a letter or underscore and contain only "
            "letters, digits, dots, underscores, or hyphens"
        )
    return username


def validate_host(value: str) -> str:
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or len(host) > 253 or any(character.isspace() for character in host):
        raise argparse.ArgumentTypeError("Pi address must be a hostname or IP address")

    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass

    hostname = host.removesuffix(".")
    labels = hostname.split(".")
    if not labels or any(not HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise argparse.ArgumentTypeError(
            "Pi address must be a valid hostname or IP address"
        )
    return hostname.lower()


def validate_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("SSH port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("SSH port must be between 1 and 65535")
    return port


def prompt_for(
    current: str | None,
    label: str,
    validator: Callable[[str], str],
    *,
    reader: Callable[[str], str] | None = None,
) -> str:
    if current is not None:
        return validator(current)
    read_value = input if reader is None else reader
    try:
        entered = read_value(f"{label}: ")
    except EOFError as error:
        raise UpdateError(f"{label} is required") from error
    try:
        return validator(entered)
    except argparse.ArgumentTypeError as error:
        raise UpdateError(str(error)) from error


def ensure_openssh() -> None:
    missing = [program for program in ("ssh", "scp") if shutil.which(program) is None]
    if missing:
        raise UpdateError(f"required OpenSSH command not found: {', '.join(missing)}")


def run_command(command: Sequence[str]) -> None:
    print(f"+ {shlex.join(command)}", flush=True)
    subprocess.run(command, check=True)


def ssh_command(
    target: SshTarget,
    control_path: Path,
    remote_command: str,
    *,
    master: bool = False,
    tty: bool = False,
) -> list[str]:
    command = ["ssh"]
    if tty:
        command.append("-t")
    if master:
        command.extend(
            [
                "-M",
                "-o",
                "ControlPersist=60",
                "-o",
                "ServerAliveInterval=15",
            ]
        )
    command.extend(
        [
            "-S",
            str(control_path),
            "-p",
            str(target.port),
            "--",
            target.ssh_destination,
            remote_command,
        ]
    )
    return command


def scp_command(
    target: SshTarget,
    control_path: Path,
    source_files: Sequence[Path],
    remote_directory: str,
) -> list[str]:
    return [
        "scp",
        "-P",
        str(target.port),
        "-o",
        f"ControlPath={control_path}",
        "--",
        *(str(path) for path in source_files),
        f"{target.scp_destination}:{remote_directory}/",
    ]


def close_control_command(target: SshTarget, control_path: Path) -> list[str]:
    return [
        "ssh",
        "-S",
        str(control_path),
        "-O",
        "exit",
        "-p",
        str(target.port),
        "--",
        target.ssh_destination,
    ]


def remote_prepare_command(token: str) -> str:
    validate_staging_token(token)
    return f'set -eu; umask 077; mkdir "$HOME/.epigraph-update-{token}"'


def remote_cleanup_command(token: str) -> str:
    validate_staging_token(token)
    return f'rm -rf "$HOME/.epigraph-update-{token}"'


def remote_linger_command() -> str:
    return (
        "set -eu; "
        "user=$(id -un); "
        'if [ "$(loginctl show-user "$user" -p Linger --value)" != "yes" ]; then '
        'sudo loginctl enable-linger "$user"; '
        "fi"
    )


def remote_font_preflight_command() -> str:
    """Build the remote Pillow check for the renderer's styled fonts."""
    return (
        "python3 -c 'from PIL import ImageFont; "
        "[ImageFont.truetype(font, 24) for font in "
        '("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", '
        '"/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", '
        '"/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf")]\' '
        "|| { printf '%s\\n' 'Missing DejaVu styled fonts; install them "
        "with: sudo apt install fonts-dejavu-core fonts-dejavu-extra' "
        ">&2; exit 1; }"
    )


def remote_install_command(token: str) -> str:
    """Build a fixed remote transaction containing only a validated random token."""
    validate_staging_token(token)
    stage = f"$HOME/.epigraph-update-{token}"
    root = "$HOME/epigraph"
    destination = f"{root}/scripts"
    new_directory = f"{root}/.scripts-new-{token}"
    backup = f"{root}/.scripts-backup-{token}"
    catalog_destination = f"{root}/{CATALOG_FILE}"
    catalog_new = f"{root}/.{CATALOG_FILE}.new-{token}"
    catalog_backup = f"{root}/.{CATALOG_FILE}.backup-{token}"
    unit_directory = "$HOME/.config/systemd/user"
    unit_destination = f"{unit_directory}/{SERVICE_FILE}"
    unit_new = f"{unit_directory}/.{SERVICE_FILE}.new-{token}"
    unit_backup = f"{unit_directory}/.{SERVICE_FILE}.backup-{token}"
    return "\n".join(
        [
            "set -eu",
            f'stage="{stage}"',
            f'root="{root}"',
            f'destination="{destination}"',
            f'new_directory="{new_directory}"',
            f'backup="{backup}"',
            f'catalog_destination="{catalog_destination}"',
            f'catalog_new="{catalog_new}"',
            f'catalog_backup="{catalog_backup}"',
            f'unit_directory="{unit_directory}"',
            f'unit_destination="{unit_destination}"',
            f'unit_new="{unit_new}"',
            f'unit_backup="{unit_backup}"',
            "scripts_swapped=0",
            "catalog_swapped=0",
            "unit_swapped=0",
            "service_was_active=0",
            "service_was_enabled=0",
            "activation_attempted=0",
            "cleanup() {",
            "  status=$?",
            "  trap - EXIT HUP INT TERM",
            ('  rm -rf "$stage" "$new_directory" "$unit_new" "$catalog_new"'),
            '  if [ "$status" -ne 0 ]; then',
            (
                '    if [ "$activation_attempted" -eq 1 ]; then '
                "systemctl --user stop epigraph-frame.service || true; fi"
            ),
            (
                '    if [ "$service_was_enabled" -eq 0 ] && '
                '[ "$unit_swapped" -eq 1 ]; then '
                "systemctl --user disable epigraph-frame.service "
                ">/dev/null 2>&1 || true; fi"
            ),
            (
                '    if [ -e "$backup" ]; then rm -rf "$destination"; '
                'mv "$backup" "$destination"; '
                'elif [ "$scripts_swapped" -eq 1 ]; then '
                'rm -rf "$destination"; fi'
            ),
            (
                '    if [ -e "$catalog_backup" ]; then '
                'rm -f "$catalog_destination"; '
                'mv "$catalog_backup" "$catalog_destination"; '
                'elif [ "$catalog_swapped" -eq 1 ]; then '
                'rm -f "$catalog_destination"; fi'
            ),
            (
                '    if [ -e "$unit_backup" ]; then '
                'rm -f "$unit_destination"; '
                'mv "$unit_backup" "$unit_destination"; '
                'elif [ "$unit_swapped" -eq 1 ]; then '
                'rm -f "$unit_destination"; fi'
            ),
            "    systemctl --user daemon-reload || true",
            (
                '    if [ "$service_was_enabled" -eq 1 ]; then '
                "systemctl --user enable epigraph-frame.service "
                ">/dev/null 2>&1 || true; fi"
            ),
            (
                '    if [ "$service_was_active" -eq 1 ] && '
                '[ "$activation_attempted" -eq 1 ]; then '
                "systemctl --user restart epigraph-frame.service || true; fi"
            ),
            '    rm -rf "$backup" "$catalog_backup" "$unit_backup"',
            "  fi",
            '  exit "$status"',
            "}",
            "trap cleanup EXIT HUP INT TERM",
            (
                'python3 -m py_compile "$stage/catalog.py" '
                '"$stage/check_hardware.py" "$stage/display_quotes.py"'
            ),
            'test -s "$stage/quotes.yaml"',
            'test -s "$stage/epigraph-frame.service"',
            'mkdir -p "$root"',
            'mkdir "$new_directory"',
            'install -m 644 "$stage/catalog.py" "$new_directory/catalog.py"',
            (
                'install -m 755 "$stage/check_hardware.py" '
                '"$new_directory/check_hardware.py"'
            ),
            (
                'install -m 755 "$stage/display_quotes.py" '
                '"$new_directory/display_quotes.py"'
            ),
            'install -m 644 "$stage/quotes.yaml" "$catalog_new"',
            (
                'PYTHONPATH="$stage" python3 -c '
                "'from pathlib import Path; import sys; "
                "from catalog import load_catalog; "
                "load_catalog(Path(sys.argv[1]))' "
                '"$catalog_new"'
            ),
            (
                'python3 -m py_compile "$new_directory/catalog.py" '
                '"$new_directory/check_hardware.py" '
                '"$new_directory/display_quotes.py"'
            ),
            remote_font_preflight_command(),
            'mkdir -p "$unit_directory"',
            'install -m 644 "$stage/epigraph-frame.service" "$unit_new"',
            (
                "if systemctl --user is-active --quiet "
                "epigraph-frame.service; then service_was_active=1; fi"
            ),
            (
                "if systemctl --user is-enabled --quiet "
                "epigraph-frame.service; then service_was_enabled=1; fi"
            ),
            'if [ -e "$destination" ]; then mv "$destination" "$backup"; fi',
            (
                'if [ -e "$catalog_destination" ]; then '
                'mv "$catalog_destination" "$catalog_backup"; fi'
            ),
            (
                'if [ -e "$unit_destination" ]; then '
                'mv "$unit_destination" "$unit_backup"; fi'
            ),
            'mv "$new_directory" "$destination"',
            "scripts_swapped=1",
            'mv "$catalog_new" "$catalog_destination"',
            "catalog_swapped=1",
            'mv "$unit_new" "$unit_destination"',
            "unit_swapped=1",
            "systemctl --user daemon-reload",
            "systemctl --user enable epigraph-frame.service",
            "activation_attempted=1",
            "systemctl --user restart epigraph-frame.service",
            "sleep 5",
            "systemctl --user is-active --quiet epigraph-frame.service",
            ('rm -rf "$backup" "$catalog_backup" "$unit_backup" "$stage"'),
            "trap - EXIT HUP INT TERM",
            (
                'printf "%s\\n" "Installed Epigraph scripts in $destination, '
                'catalog at $catalog_destination, and started Epigraph Frame"'
            ),
        ]
    )


def validate_staging_token(token: str) -> None:
    if not STAGING_TOKEN_PATTERN.fullmatch(token):
        raise ValueError("invalid staging token")


def update_pi(
    target: SshTarget,
    source_files: Sequence[Path],
    *,
    runner: CommandRunner = run_command,
    token: str | None = None,
) -> None:
    if not source_files:
        raise UpdateError("no runtime files were selected")
    missing = [str(path) for path in source_files if not path.is_file()]
    if missing:
        raise UpdateError(f"runtime file not found: {', '.join(missing)}")
    provided_names = {path.name for path in source_files}
    required_names = {*RUNTIME_FILES, CATALOG_FILE, SERVICE_FILE}
    missing_names = sorted(required_names - provided_names)
    if missing_names:
        raise UpdateError(f"deployment file not selected: {', '.join(missing_names)}")

    staging_token = token if token is not None else secrets.token_hex(8)
    validate_staging_token(staging_token)
    staging_directory = f".epigraph-update-{staging_token}"

    with TemporaryDirectory(prefix="epigraph-ssh-") as temporary:
        control_path = Path(temporary) / "control"
        master_open = False
        try:
            runner(ssh_command(target, control_path, "true", master=True))
            master_open = True
            runner(
                ssh_command(
                    target,
                    control_path,
                    remote_prepare_command(staging_token),
                )
            )
            runner(
                scp_command(
                    target,
                    control_path,
                    source_files,
                    staging_directory,
                )
            )
            runner(
                ssh_command(
                    target,
                    control_path,
                    remote_linger_command(),
                    tty=True,
                )
            )
            runner(
                ssh_command(
                    target,
                    control_path,
                    remote_install_command(staging_token),
                )
            )
        except subprocess.CalledProcessError as error:
            if master_open:
                try:
                    runner(
                        ssh_command(
                            target,
                            control_path,
                            remote_cleanup_command(staging_token),
                        )
                    )
                except subprocess.CalledProcessError:
                    pass
            raise UpdateError(
                f"Pi update failed while running {Path(error.cmd[0]).name}"
            ) from error
        finally:
            if master_open:
                try:
                    runner(close_control_command(target, control_path))
                except subprocess.CalledProcessError:
                    print(
                        "warning: could not explicitly close the SSH control connection; "
                        "it will expire after 60 seconds",
                        file=sys.stderr,
                    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install Epigraph runtime scripts on a Raspberry Pi over OpenSSH. "
            "Passwords, when required, are prompted for directly by OpenSSH."
        )
    )
    parser.add_argument("--user", help="SSH username (prompted when omitted)")
    parser.add_argument(
        "--host", help="Pi hostname or IP address (prompted when omitted)"
    )
    parser.add_argument(
        "--port",
        type=validate_port,
        default=22,
        help="SSH port (default: 22)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    user = prompt_for(args.user, "SSH username", validate_username)
    host = prompt_for(args.host, "Pi address", validate_host)
    target = SshTarget(user=user, host=host, port=args.port)

    ensure_openssh()
    script_directory = Path(__file__).resolve().parent
    repository_root = script_directory.parent
    source_files = tuple(script_directory / filename for filename in RUNTIME_FILES) + (
        repository_root / CATALOG_FILE,
        repository_root / "systemd" / SERVICE_FILE,
    )

    print(
        f"Updating {target.ssh_destination}:~/epigraph/scripts\n"
        "Authentication is handled by OpenSSH; enter a password only in its prompt.",
        flush=True,
    )
    update_pi(target, source_files)
    print(
        "Update complete. Epigraph Frame is running under the user systemd service.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (UpdateError, argparse.ArgumentTypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
