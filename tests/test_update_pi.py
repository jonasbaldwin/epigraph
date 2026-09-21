import argparse
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from update_pi import (
    SshTarget,
    UpdateError,
    close_control_command,
    parse_args,
    prompt_for,
    remote_font_preflight_command,
    remote_install_command,
    remote_linger_command,
    scp_command,
    ssh_command,
    update_pi,
    validate_host,
    validate_port,
    validate_username,
)


class InputValidationTests(unittest.TestCase):
    def test_username_accepts_normal_ssh_user(self) -> None:
        self.assertEqual(validate_username(" pi-user "), "pi-user")

    def test_username_rejects_option_or_destination_injection(self) -> None:
        for value in ("-oProxyCommand=x", "pi@host", "pi user", "pi;whoami"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                validate_username(value)

    def test_host_accepts_hostname_ipv4_and_ipv6(self) -> None:
        self.assertEqual(validate_host("Epigraph-Frame.local"), "epigraph-frame.local")
        self.assertEqual(validate_host("192.168.1.25"), "192.168.1.25")
        self.assertEqual(validate_host("[2001:db8::1]"), "2001:db8::1")

    def test_host_rejects_options_remote_specs_and_shell_text(self) -> None:
        for value in ("-oProxyCommand=x", "pi@host", "host:/tmp", "host;whoami", "a b"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                validate_host(value)

    def test_port_must_be_in_tcp_range(self) -> None:
        self.assertEqual(validate_port("22"), 22)
        for value in ("0", "65536", "not-a-port"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                validate_port(value)

    def test_missing_values_are_prompted(self) -> None:
        with patch("builtins.input", return_value="pi") as input_mock:
            result = prompt_for(None, "SSH username", validate_username)

        self.assertEqual(result, "pi")
        input_mock.assert_called_once_with("SSH username: ")

    def test_password_is_not_a_command_line_option(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["--password", "secret"])


class CommandConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = SshTarget("pi", "epigraph-frame.local", 2222)
        self.control = Path("/tmp/control")

    def test_master_ssh_uses_control_connection_and_default_host_key_policy(
        self,
    ) -> None:
        command = ssh_command(self.target, self.control, "true", master=True)

        self.assertEqual(command[0], "ssh")
        self.assertIn("ControlPersist=60", command)
        self.assertIn("ServerAliveInterval=15", command)
        self.assertIn("pi@epigraph-frame.local", command)
        self.assertNotIn("StrictHostKeyChecking=no", command)
        self.assertNotIn("password", " ".join(command).lower())

    def test_scp_brackets_ipv6_and_uses_argument_array(self) -> None:
        target = SshTarget("pi", "2001:db8::1")
        command = scp_command(
            target,
            self.control,
            (Path("check_hardware.py"), Path("display_quotes.py")),
            ".epigraph-update-0123456789abcdef",
        )

        self.assertEqual(command[0], "scp")
        self.assertEqual(
            command[-1],
            "pi@[2001:db8::1]:.epigraph-update-0123456789abcdef/",
        )

    def test_remote_install_compiles_before_atomic_directory_swap(self) -> None:
        command = remote_install_command("0123456789abcdef")

        first_compile = command.index("python3 -m py_compile")
        catalog_validation = command.index("from catalog import load_catalog")
        old_directory_move = command.index('mv "$destination" "$backup"')
        new_directory_move = command.index('mv "$new_directory" "$destination"')
        self.assertLess(first_compile, catalog_validation)
        self.assertLess(catalog_validation, old_directory_move)
        self.assertLess(old_directory_move, new_directory_move)
        self.assertIn('install -m 644 "$stage/catalog.py"', command)
        self.assertIn('install -m 755 "$stage/check_hardware.py"', command)
        self.assertIn('install -m 755 "$stage/display_quotes.py"', command)
        self.assertIn('install -m 644 "$stage/quotes.yaml"', command)
        self.assertIn('mv "$backup" "$destination"', command)
        self.assertIn("epigraph-frame.service", command)
        self.assertIn("systemctl --user enable", command)
        enable = command.rindex("systemctl --user enable epigraph-frame.service")
        restart = command.rindex("systemctl --user restart epigraph-frame.service")
        health_check = command.rindex(
            "systemctl --user is-active --quiet epigraph-frame.service"
        )
        self.assertLess(enable, restart)
        self.assertLess(restart, health_check)
        self.assertIn('mv "$unit_backup" "$unit_destination"', command)
        self.assertIn("service_was_active", command)
        self.assertEqual(
            command.count("systemctl --user restart epigraph-frame.service"), 2
        )

    def test_remote_install_checks_styled_fonts_before_swapping_files(self) -> None:
        command = remote_install_command("0123456789abcdef")

        font_check = command.index("fonts-dejavu-core fonts-dejavu-extra")
        old_directory_move = command.index('mv "$destination" "$backup"')
        self.assertLess(font_check, old_directory_move)
        font_directory = "/usr/share/fonts/truetype/dejavu/"
        self.assertIn(f"{font_directory}DejaVuSans-Bold.ttf", command)
        self.assertIn(f"{font_directory}DejaVuSans-Oblique.ttf", command)
        self.assertIn(f"{font_directory}DejaVuSans-BoldOblique.ttf", command)

    def test_linger_setup_uses_remote_sudo_without_password_data(self) -> None:
        command = remote_linger_command()
        ssh = ssh_command(self.target, self.control, command, tty=True)

        self.assertIn("-t", ssh)
        self.assertIn("sudo loginctl enable-linger", command)
        self.assertNotIn("password", command.lower())

    def test_control_close_uses_same_target_and_socket(self) -> None:
        command = close_control_command(self.target, self.control)

        self.assertEqual(command[0], "ssh")
        self.assertIn("exit", command)
        self.assertIn(str(self.control), command)
        self.assertEqual(command[-1], self.target.ssh_destination)


class FontPreflightTests(unittest.TestCase):
    def run_preflight(
        self, image_font_source: str
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pil = root / "PIL"
            pil.mkdir()
            (pil / "__init__.py").write_text("", encoding="utf-8")
            (pil / "ImageFont.py").write_text(image_font_source, encoding="utf-8")
            sentinel = root / "mutation-reached"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = directory
            environment["MUTATION_SENTINEL"] = str(sentinel)
            result = subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    remote_font_preflight_command()
                    + '\nprintf reached > "$MUTATION_SENTINEL"',
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )
            return result, sentinel.exists()

    def test_failure_aborts_before_following_mutation(self) -> None:
        result, mutation_reached = self.run_preflight(
            'def truetype(font, size):\n    raise RuntimeError("font unavailable")\n'
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "fonts-dejavu-core fonts-dejavu-extra",
            result.stderr,
        )
        self.assertFalse(mutation_reached)

    def test_success_allows_following_mutation(self) -> None:
        result, mutation_reached = self.run_preflight(
            "def truetype(font, size):\n    return object()\n"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(mutation_reached)


class DeploymentTransactionTests(unittest.TestCase):
    def make_sources(self, directory: str) -> tuple[Path, ...]:
        root = Path(directory)
        sources = (
            root / "catalog.py",
            root / "check_hardware.py",
            root / "display_quotes.py",
            root / "quotes.yaml",
            root / "epigraph-frame.service",
        )
        for source in sources:
            source.write_text("deployment content\n", encoding="utf-8")
        return sources

    def test_success_uploads_installs_and_closes_connection(self) -> None:
        with TemporaryDirectory() as directory:
            sources = self.make_sources(directory)
            commands: list[list[str]] = []

            update_pi(
                SshTarget("pi", "epigraph-frame.local"),
                sources,
                runner=lambda command: commands.append(list(command)),
                token="0123456789abcdef",
            )

        self.assertEqual(
            [command[0] for command in commands],
            ["ssh", "ssh", "scp", "ssh", "ssh", "ssh"],
        )
        self.assertIn("ControlPersist=60", commands[0])
        self.assertIn("mkdir", commands[1][-1])
        self.assertEqual(commands[2][0], "scp")
        self.assertIn("-t", commands[3])
        self.assertIn("enable-linger", commands[3][-1])
        self.assertIn("python3 -m py_compile", commands[4][-1])
        self.assertIn("quotes.yaml", commands[4][-1])
        self.assertIn("systemctl --user enable", commands[4][-1])
        self.assertIn("systemctl --user restart", commands[4][-1])
        self.assertIn("systemctl --user is-active --quiet", commands[4][-1])
        self.assertIn("-O", commands[5])
        self.assertIn("exit", commands[5])

    def test_failed_upload_cleans_staging_and_closes_connection(self) -> None:
        with TemporaryDirectory() as directory:
            sources = self.make_sources(directory)
            commands: list[list[str]] = []

            def fail_scp(command: list[str]) -> None:
                commands.append(list(command))
                if command[0] == "scp":
                    raise subprocess.CalledProcessError(1, command)

            with self.assertRaises(UpdateError):
                update_pi(
                    SshTarget("pi", "epigraph-frame.local"),
                    sources,
                    runner=fail_scp,
                    token="0123456789abcdef",
                )

        self.assertEqual(
            [command[0] for command in commands], ["ssh", "ssh", "scp", "ssh", "ssh"]
        )
        self.assertIn("rm -rf", commands[3][-1])
        self.assertIn("-O", commands[4])
        self.assertFalse(any("py_compile" in command[-1] for command in commands))

    def test_missing_source_fails_before_connecting(self) -> None:
        commands: list[list[str]] = []

        with self.assertRaises(UpdateError):
            update_pi(
                SshTarget("pi", "epigraph-frame.local"),
                (Path("missing.py"),),
                runner=lambda command: commands.append(list(command)),
            )

        self.assertEqual(commands, [])


class SystemdUnitTests(unittest.TestCase):
    def test_user_unit_starts_frame_at_boot_and_restarts_only_on_failure(self) -> None:
        unit = (
            Path(__file__).resolve().parents[1] / "systemd/epigraph-frame.service"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ExecStart=/usr/bin/python3 %h/epigraph/scripts/display_quotes.py", unit
        )
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("WantedBy=default.target", unit)


class ProvisioningDocumentationTests(unittest.TestCase):
    def test_pi_apt_install_includes_styled_font_packages(self) -> None:
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )
        apt_command = next(
            line
            for line in readme.splitlines()
            if line.startswith("sudo apt install -y ")
        )
        packages = apt_command.split()

        self.assertIn("fonts-dejavu-core", packages)
        self.assertIn("fonts-dejavu-extra", packages)


if __name__ == "__main__":
    unittest.main()
