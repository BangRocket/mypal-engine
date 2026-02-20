"""CLI for Clara backup service (Typer + Rich)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt
from rich.table import Table

from mypalclara.services.backup.config import BackupConfig
from mypalclara.services.backup.config_files import dump_config_files, restore_config_files
from mypalclara.services.backup.database import (
    check_db_connection,
    dump_database,
    mask_url,
    restore_database,
)
from mypalclara.services.backup.health import backup_state, start_health_server
from mypalclara.services.backup.storage import create_backend

app = typer.Typer(
    name="backup",
    help="Clara database backup service.",
    no_args_is_help=True,
)
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backup_service")


class DbFilter(str, Enum):
    clara = "clara"
    rook = "rook"
    config = "config"


def _load_config() -> BackupConfig:
    """Load config, calling dotenv first for local runs."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return BackupConfig.from_env()


def _format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def _format_age(dt: datetime) -> str:
    """Human-readable age from a datetime."""
    now = datetime.now(UTC)
    delta = now - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{int(delta.total_seconds() / 60)}m ago"
    elif hours < 24:
        return f"{hours:.1f}h ago"
    else:
        return f"{delta.days}d ago"


def _check_respawn(backend, config: BackupConfig) -> tuple[bool, str]:
    """Check respawn protection. Returns (should_skip, reason)."""
    if config.force:
        return False, ""
    last = backend.get_last_backup_time()
    if not last:
        return False, ""
    hours_since = (datetime.now(UTC) - last).total_seconds() / 3600
    if hours_since < config.respawn_hours:
        return True, f"Only {hours_since:.1f}h since last backup (min: {config.respawn_hours}h)."
    return False, ""


# ── backup run ──────────────────────────────────────────────────────────


@app.command()
def run(
    db: Annotated[Optional[DbFilter], typer.Option(help="Only backup this database")] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Bypass respawn protection")] = False,
) -> None:
    """Run database backups."""
    config = _load_config()
    if force:
        config.force = True

    backend = create_backend(config)

    # Verify S3 access early if using S3 backend
    if hasattr(backend, "verify"):
        try:
            backend.verify()
        except Exception as e:
            console.print(f"[red]Error:[/] S3 connection failed: {e}")
            raise typer.Exit(1)

    # Respawn protection
    skipped, reason = _check_respawn(backend, config)
    if skipped:
        console.print(f"[yellow]Skipped:[/] {reason} Use --force to override.")
        return

    # Determine which databases to backup
    databases = config.databases
    if db and db.value in ("clara", "rook"):
        if db.value not in databases:
            console.print(f"[red]Error:[/] {db.value} database URL not configured")
            raise typer.Exit(1)
        databases = {db.value: databases[db.value]}
    elif db and db.value == "config":
        # Non-PostgreSQL target: skip the database loop
        databases = {}

    backup_config = (not db or db.value == "config") and config.config_backup_enabled

    if not databases and not backup_config:
        console.print("[red]Error:[/] No backup targets configured")
        raise typer.Exit(1)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results: list[dict] = []

    # PostgreSQL backups
    for db_name, db_url in databases.items():
        console.print(f"\n[bold]Backing up {db_name}...[/]")

        # Check connectivity
        if not check_db_connection(db_url, db_name, config):
            console.print(f"  [red]Failed:[/] Could not connect to {db_name}")
            results.append({"db": db_name, "status": "failed", "error": "connection failed"})
            continue

        # Dump
        with console.status(f"  Dumping {db_name}..."):
            data, raw_size = dump_database(db_url, db_name, config)

        if not data:
            console.print(f"  [red]Failed:[/] pg_dump failed for {db_name}")
            results.append({"db": db_name, "status": "failed", "error": "pg_dump failed"})
            continue

        # Upload
        with console.status(f"  Uploading {db_name}..."):
            try:
                key = backend.upload(data, db_name, timestamp)
            except Exception as e:
                console.print(f"  [red]Failed:[/] Upload error: {e}")
                results.append({"db": db_name, "status": "failed", "error": str(e)})
                continue

        ratio = (1 - len(data) / raw_size) * 100 if raw_size else 0
        console.print(
            f"  [green]OK:[/] {_format_size(raw_size)} -> {_format_size(len(data))} ({ratio:.0f}% compression)"
        )

        # Cleanup old backups
        deleted = backend.cleanup_old(db_name, config.retention_days)
        if deleted:
            console.print(f"  Cleaned up {deleted} old backup(s)")

        results.append(
            {
                "db": db_name,
                "status": "success",
                "raw_size": raw_size,
                "compressed_size": len(data),
                "key": key,
            }
        )

    # Config file backup
    if backup_config:
        console.print("\n[bold]Backing up config files...[/]")
        with console.status("  Archiving config files..."):
            data, raw_size = dump_config_files(config.config_paths, config.compression_level)

        if data:
            with console.status("  Uploading config..."):
                try:
                    key = backend.upload(data, "config", timestamp)
                except Exception as e:
                    console.print(f"  [red]Failed:[/] Upload error: {e}")
                    results.append({"db": "config", "status": "failed", "error": str(e)})
                    data = None

            if data:
                ratio = (1 - len(data) / raw_size) * 100 if raw_size else 0
                console.print(
                    f"  [green]OK:[/] {_format_size(raw_size)} -> {_format_size(len(data))} ({ratio:.0f}% compression)"
                )
                deleted = backend.cleanup_old("config", config.retention_days)
                if deleted:
                    console.print(f"  Cleaned up {deleted} old backup(s)")
                results.append(
                    {
                        "db": "config",
                        "status": "success",
                        "raw_size": raw_size,
                        "compressed_size": len(data),
                        "key": key,
                    }
                )
        else:
            console.print("  [red]Failed:[/] Config file archive failed")
            results.append({"db": "config", "status": "failed", "error": "archive failed"})

    # Update respawn marker
    successes = [r for r in results if r["status"] == "success"]
    failures = [r for r in results if r["status"] == "failed"]

    if successes:
        backend.set_last_backup_time(datetime.now(UTC))

    # Summary panel
    lines = []
    for r in results:
        if r["status"] == "success":
            lines.append(f"[green]OK[/]   {r['db']}: {_format_size(r['compressed_size'])}")
        else:
            lines.append(f"[red]FAIL[/] {r['db']}: {r.get('error', 'unknown')}")

    if not lines:
        console.print("[yellow]No backup targets matched.[/]")
        return

    title = "[green]Backup Complete[/]" if not failures else "[yellow]Backup Partial[/]"
    console.print(Panel("\n".join(lines), title=title))

    if failures:
        raise typer.Exit(1)


# ── backup list ─────────────────────────────────────────────────────────


@app.command("list")
def list_backups(
    db: Annotated[Optional[DbFilter], typer.Option(help="Filter by database")] = None,
) -> None:
    """List available backups."""
    config = _load_config()
    backend = create_backend(config)

    entries = backend.list_backups(db_name=db.value if db else None)

    if not entries:
        console.print("[yellow]No backups found.[/]")
        return

    table = Table(title="Available Backups")
    table.add_column("#", style="dim", width=4)
    table.add_column("Database", style="cyan")
    table.add_column("Filename")
    table.add_column("Size", justify="right")
    table.add_column("Date")
    table.add_column("Age", style="dim")

    for i, entry in enumerate(entries, 1):
        table.add_row(
            str(i),
            entry.db_name,
            entry.filename,
            _format_size(entry.size),
            entry.modified.strftime("%Y-%m-%d %H:%M UTC"),
            _format_age(entry.modified),
        )

    console.print(table)


# ── backup restore ──────────────────────────────────────────────────────


def _detect_backup_type(filename: str) -> str:
    """Detect backup type from filename extension."""
    lower = filename.lower()
    if lower.endswith(".tar.gz"):
        return "config"
    return "postgresql"


def _infer_db_name(filename: str) -> str | None:
    """Infer db_name from filename."""
    lower = filename.lower()
    if "config" in lower:
        return "config"
    if "clara" in lower:
        return "clara"
    if "rook" in lower or "mem0" in lower:
        return "rook"
    return None


def _restore_by_type(
    backup_type: str,
    backup_data: bytes,
    db_name: str,
    db_url: str,
    target_path: str | None,
) -> bool:
    """Route restore to the correct handler based on backup type."""
    if backup_type == "config":
        from pathlib import Path

        out_dir = Path(target_path) if target_path else Path(".")
        ok = restore_config_files(backup_data, out_dir)
        if ok:
            console.print(f"\n[green]Config files extracted to:[/] {out_dir.resolve()}")
        return ok

    # PostgreSQL
    return restore_database(db_url, backup_data, db_name)


@app.command()
def restore(
    file: Annotated[Optional[str], typer.Option("--file", "-f", help="Restore from local backup file")] = None,
    target: Annotated[
        Optional[str],
        typer.Option("--target", "-t", help="Target database URL (PostgreSQL) or output path (config)"),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
) -> None:
    """Restore a database from backup."""
    config = _load_config()

    if file:
        # Restore from local file
        from pathlib import Path

        path = Path(file)
        if not path.exists():
            console.print(f"[red]Error:[/] File not found: {file}")
            raise typer.Exit(1)

        backup_data = path.read_bytes()
        backup_type = _detect_backup_type(path.name)

        # Infer db_name from filename
        db_name = _infer_db_name(path.name)
        if not db_name:
            console.print("[yellow]Cannot determine backup type from filename.[/]")
            console.print("Filename should contain 'clara', 'rook', or 'config'.")
            raise typer.Exit(1)

        # Verify it's gzipped (check magic number)
        if backup_data[:2] != b"\x1f\x8b":
            console.print("[red]Error:[/] File does not appear to be gzipped")
            raise typer.Exit(1)

        # PostgreSQL restores need a DB URL
        db_url = ""
        if backup_type == "postgresql":
            db_url = target or config.databases.get(db_name, "")
            if not db_url:
                console.print(f"[red]Error:[/] No URL configured for {db_name} database")
                raise typer.Exit(1)

        console.print(f"Backup:   [bold]{path.name}[/] ({_format_size(len(backup_data))})")
        console.print(f"Type:     [bold]{backup_type}[/]")
        if backup_type == "postgresql":
            console.print(f"Database: [bold]{db_name}[/]")
            console.print(f"Target:   [bold]{mask_url(db_url)}[/]")
        elif target:
            console.print(f"Output:   [bold]{target}[/]")

        if not yes and not Confirm.ask("\n[yellow]This will overwrite data. Continue?[/]"):
            console.print("Aborted.")
            raise typer.Exit(0)

        with console.status("Restoring..."):
            ok = _restore_by_type(backup_type, backup_data, db_name, db_url, target)

        if ok:
            console.print("[green]Restore completed successfully.[/]")
        else:
            console.print("[red]Restore failed.[/]")
            raise typer.Exit(1)
        return

    # Interactive: pick from available backups
    backend = create_backend(config)
    entries = backend.list_backups()

    if not entries:
        console.print("[yellow]No backups found.[/]")
        raise typer.Exit(1)

    table = Table(title="Select a backup to restore")
    table.add_column("#", style="dim", width=4)
    table.add_column("Database", style="cyan")
    table.add_column("Filename")
    table.add_column("Size", justify="right")
    table.add_column("Date")

    for i, entry in enumerate(entries, 1):
        table.add_row(
            str(i),
            entry.db_name,
            entry.filename,
            _format_size(entry.size),
            entry.modified.strftime("%Y-%m-%d %H:%M UTC"),
        )

    console.print(table)

    choice = IntPrompt.ask("\nSelect backup number", default=1)
    if choice < 1 or choice > len(entries):
        console.print("[red]Invalid selection.[/]")
        raise typer.Exit(1)

    entry = entries[choice - 1]
    db_name = entry.db_name
    backup_type = _detect_backup_type(entry.filename)

    # PostgreSQL restores need a DB URL
    db_url = ""
    if backup_type == "postgresql":
        db_url = target or config.databases.get(db_name, "")
        if not db_url:
            console.print(f"[red]Error:[/] No URL configured for {db_name} database. Use --target to specify one.")
            raise typer.Exit(1)

    console.print(
        f"\nBackup:   [bold]{entry.filename}[/] ({_format_size(entry.size)}, {entry.modified.strftime('%Y-%m-%d %H:%M UTC')})"
    )
    console.print(f"Type:     [bold]{backup_type}[/]")
    if backup_type == "postgresql":
        console.print(f"Database: [bold]{db_name}[/]")
        console.print(f"Target:   [bold]{mask_url(db_url)}[/]")
    elif target:
        console.print(f"Output:   [bold]{target}[/]")

    if not yes and not Confirm.ask("\n[yellow]This will overwrite data. Continue?[/]"):
        console.print("Aborted.")
        raise typer.Exit(0)

    with console.status("Downloading backup..."):
        backup_data = backend.download(entry.key)

    with console.status("Restoring..."):
        ok = _restore_by_type(backup_type, backup_data, db_name, db_url, target)

    if ok:
        console.print("[green]Restore completed successfully.[/]")
    else:
        console.print("[red]Restore failed.[/]")
        raise typer.Exit(1)


# ── backup status ───────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """Show backup service status and configuration."""
    config = _load_config()
    backend = create_backend(config)

    lines = []
    lines.append(f"[bold]Storage:[/]        {config.storage_type}")

    if config.storage_type == "s3":
        lines.append(f"[bold]S3 Bucket:[/]      {config.s3_bucket}")
        lines.append(f"[bold]S3 Endpoint:[/]    {config.s3_endpoint_url}")
    else:
        lines.append(f"[bold]Backup Dir:[/]     {config.local_backup_dir.resolve()}")

    lines.append("")
    lines.append("[bold]Databases:[/]")
    for name, url in config.databases.items():
        lines.append(f"  {name}: {mask_url(url)}")
    if not config.databases:
        lines.append("  [yellow](none configured)[/]")

    lines.append("")
    lines.append("[bold]Config Files:[/]")
    if config.config_backup_enabled:
        for p in config.config_paths:
            lines.append(f"  {p}")
    else:
        lines.append("  [dim](not configured)[/]")

    lines.append("")
    lines.append(f"[bold]Retention:[/]      {config.retention_days} days")
    lines.append(f"[bold]Cron Schedule:[/]  {config.cron_schedule}")

    last = backend.get_last_backup_time()
    if last:
        lines.append(f"[bold]Last Backup:[/]   {last.strftime('%Y-%m-%d %H:%M UTC')} ({_format_age(last)})")
    else:
        lines.append("[bold]Last Backup:[/]   [dim]never[/]")

    entries = backend.list_backups()
    lines.append(f"[bold]Total Backups:[/] {len(entries)}")

    console.print(Panel("\n".join(lines), title="Backup Service Status"))


# ── backup serve ────────────────────────────────────────────────────────


@app.command()
def serve(
    schedule: Annotated[
        Optional[str], typer.Option("--schedule", "-s", help="Cron schedule (default from env)")
    ] = None,
) -> None:
    """Run as long-lived daemon with built-in cron scheduler + health server."""
    from mypalclara.services.backup.cron import run_scheduler

    config = _load_config()
    cron_schedule = schedule or config.cron_schedule

    # Start health server
    backup_state["status"] = "ready"
    start_health_server(config.health_port)
    console.print(f"Health server on port {config.health_port}")
    console.print(f"Cron schedule: {cron_schedule}")

    from mypalclara.services.backup.cron import next_run_time, parse_cron_schedule

    cron_minute, cron_hour = parse_cron_schedule(cron_schedule)
    first_run = next_run_time(cron_minute, cron_hour)
    console.print(f"Next backup at: {first_run.strftime('%Y-%m-%d %H:%M')}")

    def do_backup():
        try:
            # Invoke the run command logic directly
            backup_state["status"] = "running"
            _run_backup(config)
            backup_state["status"] = "completed"
            backup_state["last_backup"] = datetime.now(UTC).isoformat()
        except Exception as e:
            backup_state["status"] = "error"
            backup_state["last_error"] = str(e)
            logger.exception("Backup failed")

    run_scheduler(cron_schedule, do_backup)


def _run_backup(config: BackupConfig) -> None:
    """Core backup logic used by both `run` command and `serve` scheduler."""
    backend = create_backend(config)

    # Respawn protection
    skipped, reason = _check_respawn(backend, config)
    if skipped:
        logger.info(f"Skipped: {reason}")
        return

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    successes = 0
    failures = 0

    # PostgreSQL backups
    for db_name, db_url in config.databases.items():
        logger.info(f"Backing up {db_name}...")

        if not check_db_connection(db_url, db_name, config):
            logger.error(f"[{db_name}] Connection failed")
            failures += 1
            continue

        data, raw_size = dump_database(db_url, db_name, config)
        if not data:
            logger.error(f"[{db_name}] Dump failed")
            failures += 1
            continue

        try:
            backend.upload(data, db_name, timestamp)
            successes += 1
            backend.cleanup_old(db_name, config.retention_days)
        except Exception as e:
            logger.error(f"[{db_name}] Upload failed: {e}")
            failures += 1

    # Config file backup
    if config.config_backup_enabled:
        logger.info("Backing up config files...")
        data, raw_size = dump_config_files(config.config_paths, config.compression_level)
        if data:
            try:
                backend.upload(data, "config", timestamp)
                successes += 1
                backend.cleanup_old("config", config.retention_days)
            except Exception as e:
                logger.error(f"[config] Upload failed: {e}")
                failures += 1
        else:
            logger.error("[config] Archive failed")
            failures += 1

    if successes:
        backend.set_last_backup_time(datetime.now(UTC))

    backup_state["backups_completed"] += successes
    logger.info(f"Backup complete: {successes} succeeded, {failures} failed")

    if failures and not successes:
        raise RuntimeError(f"All {failures} backups failed")
