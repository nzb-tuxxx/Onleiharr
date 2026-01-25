from __future__ import annotations

import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from onleiharr.config import GourouConfig

logger = logging.getLogger(__name__)


class GourouError(Exception):
    """Raised when a libgourou wrapper command fails."""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


@dataclass(frozen=True)
class DownloadResult(CommandResult):
    output_path: Path | None


@dataclass(frozen=True)
class LoanInfo:
    id_hash: str
    loan_id: str
    operator_url: str
    validity: str
    name: str
    path: Path


class GourouClient:
    def __init__(self, config: GourouConfig):
        self.config = config

    def has_binaries(self, names: list[str]) -> bool:
        return all(self._find_bin(name) is not None for name in names)

    def activate_device(
        self,
        username: str | None = None,
        password: str | None = None,
        output_dir: Path | None = None,
        anonymous: bool = False,
        hobbes_version: str | None = None,
        random_serial: bool = False,
    ) -> CommandResult:
        if anonymous:
            if username or password:
                raise GourouError("Anonymous mode does not accept username/password.")
        else:
            if not username:
                raise GourouError("Username is required when not using anonymous mode.")

        cmd = [self._resolve_bin("adept_activate")]
        cmd.extend(self._verbose_flags())
        if anonymous:
            cmd.append("-a")
        else:
            cmd.extend(["-u", username])
            if password is not None:
                cmd.extend(["-p", password])
        if hobbes_version:
            cmd.extend(["-H", hobbes_version])
        if random_serial:
            cmd.append("-r")
        output_dir = output_dir or self.config.adept_dir
        if output_dir:
            cmd.extend(["-O", str(output_dir)])

        return self._run(cmd)

    def download_acsm(
        self,
        acsm_path: Path,
        output_dir: Path | None = None,
        output_file: str | None = None,
        resume: bool = False,
        notify: bool = True,
        adept_dir: Path | None = None,
    ) -> DownloadResult:
        if not acsm_path.exists():
            raise GourouError(f"ACSM file not found: {acsm_path}")

        adept_dir = self._require_adept_dir(adept_dir)

        cmd = [self._resolve_bin("acsmdownloader")]
        cmd.extend(self._verbose_flags())
        cmd.extend(["-D", str(adept_dir)])
        if not notify:
            cmd.append("-N")
        if resume:
            cmd.append("-r")

        output_dir = output_dir or self.config.download_dir
        if output_dir:
            cmd.extend(["-O", str(output_dir)])
        if output_file:
            cmd.extend(["-o", output_file])

        cmd.append(str(acsm_path))

        result = self._run(cmd)
        output_path = self._parse_created_path(result.stdout)
        return DownloadResult(stdout=result.stdout, stderr=result.stderr, output_path=output_path)

    def export_private_key(
        self,
        output_dir: Path | None = None,
        output_file: str | None = None,
        adept_dir: Path | None = None,
    ) -> DownloadResult:
        adept_dir = self._require_adept_dir(adept_dir)

        cmd = [self._resolve_bin("acsmdownloader")]
        cmd.extend(self._verbose_flags())
        cmd.extend(["-D", str(adept_dir), "-e"])

        if output_dir:
            cmd.extend(["-O", str(output_dir)])
        if output_file:
            cmd.extend(["-o", output_file])

        result = self._run(cmd)
        output_path = self._parse_export_path(result.stdout)
        return DownloadResult(stdout=result.stdout, stderr=result.stderr, output_path=output_path)

    def list_loans(self, adept_dir: Path | None = None) -> list[LoanInfo]:
        adept_dir = self._require_adept_dir(adept_dir)
        loans_dir = adept_dir / "loans"
        if not loans_dir.exists():
            return []

        loans: list[LoanInfo] = []
        for entry in sorted(loans_dir.glob("*.xml")):
            loan = self._parse_loan_file(entry)
            if loan:
                loans.append(loan)
        return loans

    def return_loan(self, loan_id_hash: str, notify: bool = True, adept_dir: Path | None = None) -> CommandResult:
        cmd = [self._resolve_bin("adept_loan_mgt")]
        cmd.extend(self._verbose_flags())
        if not notify:
            cmd.append("-N")
        adept_dir = self._require_adept_dir(adept_dir)
        cmd.extend(["-D", str(adept_dir)])
        cmd.extend(["-r", loan_id_hash])
        return self._run(cmd)

    def delete_loan(self, loan_id_hash: str, adept_dir: Path | None = None) -> CommandResult:
        cmd = [self._resolve_bin("adept_loan_mgt")]
        cmd.extend(self._verbose_flags())
        adept_dir = self._require_adept_dir(adept_dir)
        cmd.extend(["-D", str(adept_dir)])
        cmd.extend(["-d", loan_id_hash])
        return self._run(cmd)

    def remove_drm(
        self,
        input_file: Path,
        output_dir: Path | None = None,
        output_file: str | None = None,
        adept_dir: Path | None = None,
    ) -> CommandResult:
        if not input_file.exists():
            raise GourouError(f"Input file not found: {input_file}")

        adept_dir = self._require_adept_dir(adept_dir)

        cmd = [self._resolve_bin("adept_remove")]
        cmd.extend(self._verbose_flags())
        cmd.extend(["-D", str(adept_dir)])
        if output_dir:
            cmd.extend(["-O", str(output_dir)])
        if output_file:
            cmd.extend(["-o", output_file])
        cmd.append(str(input_file))

        return self._run(cmd)

    def _verbose_flags(self) -> list[str]:
        return ["-v"] * max(0, int(self.config.verbose))

    def _resolve_bin(self, name: str) -> str:
        path = self._find_bin(name)
        if path is None:
            raise GourouError(
                f"Required binary '{name}' not found in PATH. "
                "Set gourou.bin_dir or ONLEIHARR_GOUROU_BIN_DIR."
            )
        return path

    def _find_bin(self, name: str) -> str | None:
        path = shutil.which(name)
        if path:
            return path

        if self.config.bin_dir:
            search_path = f"{self.config.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            path = shutil.which(name, path=search_path)
            if path:
                return path
            candidate = self.config.bin_dir / name
            if candidate.is_file():
                return str(candidate)

        return None

    def _run(self, cmd: list[str]) -> CommandResult:
        env = dict(os.environ)
        if self.config.bin_dir:
            env["PATH"] = f"{self.config.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        if self.config.adept_dir:
            env["ADEPT_DIR"] = str(self.config.adept_dir)

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.config.timeout_secs,
                env=env,
            )
        except FileNotFoundError as exc:
            raise GourouError(f"Binary not found: {cmd[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise GourouError(f"Command timed out after {self.config.timeout_secs}s: {cmd[0]}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            message = f"{cmd[0]} failed with exit code {exc.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise GourouError(message) from exc

        return CommandResult(stdout=completed.stdout, stderr=completed.stderr)

    def _resolve_adept_dir(self, override: Path | None = None) -> Path | None:
        if override:
            return override
        if self.config.adept_dir:
            return self.config.adept_dir
        env_dir = os.getenv("ADEPT_DIR")
        if env_dir:
            return Path(env_dir)

        default_dir = Path.home() / ".config" / "adept"
        if self._has_adept_files(default_dir):
            return default_dir

        candidates = [
            Path.cwd(),
            Path.cwd() / ".adept",
            Path.cwd() / "adobe-digital-editions",
            Path.cwd() / ".adobe-digital-editions",
        ]
        for candidate in candidates:
            if self._has_adept_files(candidate):
                return candidate

        return None

    def _require_adept_dir(self, override: Path | None = None) -> Path:
        adept_dir = self._resolve_adept_dir(override)
        if not adept_dir:
            raise GourouError(
                "ADEPT directory not found. Run `adept_activate --anonymous` once, or set "
                "gourou.adept_dir / ONLEIHARR_GOUROU_ADEPT_DIR / ADEPT_DIR."
            )
        if not self._has_adept_files(adept_dir):
            raise GourouError(
                f"ADEPT directory missing device files: {adept_dir}. "
                "Run `adept_activate --anonymous` once to initialize."
            )
        return adept_dir

    @staticmethod
    def _has_adept_files(directory: Path) -> bool:
        return (
            (directory / "device.xml").exists()
            and (directory / "activation.xml").exists()
            and (directory / "devicesalt").exists()
        )

    @staticmethod
    def _parse_created_path(stdout: str) -> Path | None:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("Created "):
                return Path(line[len("Created ") :].strip())
        return None

    @staticmethod
    def _parse_export_path(stdout: str) -> Path | None:
        prefix = "Private license key exported to "
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith(prefix):
                return Path(line[len(prefix) :].strip())
        return None

    @staticmethod
    def _parse_loan_file(path: Path) -> LoanInfo | None:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            logger.warning("Skipping invalid loan token file: %s", path)
            return None

        root = tree.getroot()
        if root.tag != "loanToken":
            logger.warning("Skipping unexpected loan token format: %s", path)
            return None

        def _text(tag: str) -> str:
            node = root.find(tag)
            return node.text.strip() if node is not None and node.text else ""

        loan_id = _text("id")
        operator_url = _text("operatorURL")
        validity = _text("validity")
        name = _text("name")

        if not loan_id:
            logger.warning("Skipping loan token without id: %s", path)
            return None

        return LoanInfo(
            id_hash=path.stem,
            loan_id=loan_id,
            operator_url=operator_url,
            validity=validity,
            name=name,
            path=path,
        )
