import os
import subprocess
from pathlib import Path

BACKUP_SCRIPT = Path(__file__).parents[1] / "backup.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_backup_encrypts_uploads_and_applies_retention(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "rclone.log"
    key_path = tmp_path / "backup.key"
    key_path.write_text("test-only-encryption-key")

    _write_executable(bin_dir / "pg_dump", "#!/bin/sh\nprintf 'database-dump'\n")
    _write_executable(
        bin_dir / "gpg",
        """#!/bin/sh
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    shift
    output="$1"
  fi
  shift
done
printf 'encrypted:' > "$output"
cat >> "$output"
""",
    )
    _write_executable(bin_dir / "rclone", '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_RCLONE_LOG"\n')

    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "TEST_RCLONE_LOG": str(log_path),
        "AI_DATABASE_URL": "postgresql://plane_ai:test@ai-db:5432/plane_ai",
        "BACKUP_ACCESS_KEY_ID": "test-access-key",
        "BACKUP_SECRET_ACCESS_KEY": "test-secret-key",
        "BACKUP_ENDPOINT": "https://storage.example.test",
        "BACKUP_BUCKET": "backups",
        "BACKUP_PREFIX": "plane-ai/postgres",
        "BACKUP_RETENTION_DAYS": "7",
        "ENCRYPTION_KEY_FILE": str(key_path),
    }

    subprocess.run(["sh", str(BACKUP_SCRIPT)], check=True, env=environment)

    calls = log_path.read_text().splitlines()
    assert calls[0].startswith("copyto /tmp/plane-ai-")
    assert ".dump.gpg idrive:backups/plane-ai/postgres/plane-ai-" in calls[0]
    assert calls[1] == "delete idrive:backups/plane-ai/postgres --min-age 7d --include *.dump.gpg --s3-no-check-bucket"


def test_backup_refuses_to_run_without_encryption_key(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "AI_DATABASE_URL": "postgresql://plane_ai:test@ai-db:5432/plane_ai",
        "BACKUP_ACCESS_KEY_ID": "test-access-key",
        "BACKUP_SECRET_ACCESS_KEY": "test-secret-key",
        "BACKUP_ENDPOINT": "https://storage.example.test",
        "BACKUP_BUCKET": "backups",
        "ENCRYPTION_KEY_FILE": str(tmp_path / "missing.key"),
    }

    result = subprocess.run(["sh", str(BACKUP_SCRIPT)], capture_output=True, text=True, env=environment)

    assert result.returncode == 1
    assert "Backup encryption key is unavailable" in result.stderr
