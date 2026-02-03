#!/usr/bin/env python3
"""
Database backup script for Clara.

Backs up both Clara DB and Mem0 DB to S3-compatible storage (Wasabi).
Designed to run as a cron job or manually.

Usage:
    python backup_databases.py              # Backup both databases
    python backup_databases.py --list       # List available backups
    python backup_databases.py --restore    # Restore instructions

Environment Variables:
    DATABASE_URL      - Clara PostgreSQL connection string
    MEM0_DATABASE_URL - Mem0 PostgreSQL connection string  
    S3_ENABLED=true   - Enable S3 storage
    S3_BUCKET         - S3 bucket name
    S3_ENDPOINT_URL   - S3 endpoint (e.g., https://s3.wasabisys.com)
    S3_ACCESS_KEY     - S3 access key
    S3_SECRET_KEY     - S3 secret key
    S3_REGION         - S3 region
"""

import gzip
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import boto3
from dotenv import load_dotenv

load_dotenv()

# Configuration
CLARA_DB_URL = os.getenv("DATABASE_URL", "")
MEM0_DB_URL = os.getenv("MEM0_DATABASE_URL", "")

S3_BUCKET = os.getenv("S3_BUCKET", "clara-files")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "https://s3.wasabisys.com")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

BACKUP_PREFIX = "backups"
RETENTION_DAYS = 7


def get_s3_client():
    """Create S3 client."""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
    )


def parse_db_url(url: str) -> dict:
    """Parse database URL into components."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    
    parsed = urlparse(url)
    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
    }


def dump_database(db_url: str, db_name: str) -> bytes | None:
    """Dump a PostgreSQL database using pg_dump."""
    if not db_url:
        print(f"  [SKIP] {db_name}: No DATABASE_URL configured")
        return None
    
    db = parse_db_url(db_url)
    
    # Set password in environment
    env = os.environ.copy()
    env["PGPASSWORD"] = db["password"]
    
    cmd = [
        "pg_dump",
        "-h", db["host"],
        "-p", str(db["port"]),
        "-U", db["user"],
        "-d", db["database"],
        "--format=plain",
        "--no-owner",
        "--no-acl",
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            timeout=300,  # 5 minute timeout
        )
        
        if result.returncode != 0:
            print(f"  [ERROR] {db_name}: pg_dump failed")
            print(f"    stderr: {result.stderr.decode()[:500]}")
            return None
        
        # Compress the dump
        dump_data = result.stdout
        compressed = gzip.compress(dump_data)
        
        print(f"  [OK] {db_name}: {len(dump_data):,} bytes -> {len(compressed):,} bytes (gzipped)")
        return compressed
        
    except FileNotFoundError:
        print("  [ERROR] pg_dump not found - install postgresql-client")
        return None
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] {db_name}: pg_dump timed out")
        return None
    except Exception as e:
        print(f"  [ERROR] {db_name}: {e}")
        return None


def upload_backup(s3, data: bytes, db_name: str, timestamp: str) -> bool:
    """Upload backup to S3."""
    key = f"{BACKUP_PREFIX}/{db_name}/{db_name}_{timestamp}.sql.gz"
    
    try:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            ContentType="application/gzip",
        )
        print(f"  [UPLOADED] s3://{S3_BUCKET}/{key}")
        return True
    except Exception as e:
        print(f"  [ERROR] Upload failed: {e}")
        return False


def cleanup_old_backups(s3, db_name: str):
    """Remove backups older than retention period."""
    prefix = f"{BACKUP_PREFIX}/{db_name}/"
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    
    try:
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        
        for obj in response.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=UTC) < cutoff:
                s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
                print(f"  [DELETED] {obj['Key']} (older than {RETENTION_DAYS} days)")
                
    except Exception as e:
        print(f"  [WARN] Cleanup failed: {e}")


def list_backups(s3):
    """List all available backups."""
    print(f"\nAvailable backups in s3://{S3_BUCKET}/{BACKUP_PREFIX}/\n")
    
    for db_name in ["clara", "mem0"]:
        prefix = f"{BACKUP_PREFIX}/{db_name}/"
        print(f"{db_name.upper()} backups:")
        
        try:
            response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
            
            backups = sorted(
                response.get("Contents", []),
                key=lambda x: x["LastModified"],
                reverse=True
            )
            
            if not backups:
                print("  (none)")
            else:
                for obj in backups[:10]:  # Show last 10
                    size_mb = obj["Size"] / (1024 * 1024)
                    timestamp = obj["LastModified"].strftime("%Y-%m-%d %H:%M:%S")
                    print(f"  - {obj['Key'].split('/')[-1]} ({size_mb:.2f} MB, {timestamp})")
                    
        except Exception as e:
            print(f"  Error: {e}")
        
        print()


def run_backup():
    """Run backup of all databases."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    
    print(f"\n{'='*50}")
    print(f"Clara Database Backup - {timestamp}")
    print(f"{'='*50}\n")
    
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print("[ERROR] S3 credentials not configured")
        sys.exit(1)
    
    s3 = get_s3_client()
    success = True
    
    # Backup Clara DB
    print("[1/2] Backing up Clara DB...")
    clara_data = dump_database(CLARA_DB_URL, "clara")
    if clara_data:
        if not upload_backup(s3, clara_data, "clara", timestamp):
            success = False
        cleanup_old_backups(s3, "clara")
    elif CLARA_DB_URL:
        success = False
    
    print()
    
    # Backup Mem0 DB
    print("[2/2] Backing up Mem0 DB...")
    mem0_data = dump_database(MEM0_DB_URL, "mem0")
    if mem0_data:
        if not upload_backup(s3, mem0_data, "mem0", timestamp):
            success = False
        cleanup_old_backups(s3, "mem0")
    elif MEM0_DB_URL:
        success = False
    
    print()
    print("="*50)
    if success:
        print("Backup completed successfully!")
    else:
        print("Backup completed with errors")
        sys.exit(1)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Clara database backup utility")
    parser.add_argument("--list", "-l", action="store_true", help="List available backups")
    parser.add_argument("--restore", "-r", action="store_true",
                        help="Show restore instructions")
    args = parser.parse_args()
    
    if args.list:
        s3 = get_s3_client()
        list_backups(s3)
    elif args.restore:
        print("\nTo restore from backup:")
        print("1. Download the backup: aws s3 cp s3://BUCKET/backups/DB/FILE.sql.gz .")
        print("2. Decompress: gunzip FILE.sql.gz")
        print("3. Restore: psql DATABASE_URL < FILE.sql")
        print("\nOr use the S3 console to download the file.")
    else:
        run_backup()


if __name__ == "__main__":
    main()
