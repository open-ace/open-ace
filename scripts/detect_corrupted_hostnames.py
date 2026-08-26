#!/usr/bin/env python3
"""
Detect corrupted hostnames in the database (Issue #3081).

This script scans the remote_machines table for hostname and machine_name
fields that contain mojibake patterns, helping administrators identify
affected machines that need to be re-registered.

Usage:
    python scripts/detect_corrupted_hostnames.py

Output:
    - List of machines with corrupted hostnames
    - Summary statistics
    - Repair suggestions
"""

import re
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.repositories.database import Database, adapt_sql


def detect_mojibake(value: str | None) -> bool:
    """
    Detect if a string contains mojibake patterns.

    Args:
        value: String to check (can be None)

    Returns:
        True if mojibake is detected, False otherwise
    """
    if not value:
        return False

    # Common mojibake patterns
    patterns = [
        r"\?{2,}",  # Two or more consecutive question marks
        r"�",  # Unicode replacement character (U+FFFD)
        r"[\x00-\x08\x0B\x0C\x0E-\x1F]",  # Control characters
    ]

    return any(re.search(pattern, value) for pattern in patterns)


def main():
    """Main function to detect and report corrupted hostnames."""
    print("=" * 70)
    print("Corrupted Hostname Detection Script (Issue #3081)")
    print("=" * 70)
    print()

    # Initialize database connection
    db = Database()

    # Query all machines
    with db.connection() as conn:
        cursor = conn.cursor()

        # Get all machines
        query = adapt_sql(
            """
            SELECT machine_id, machine_name, hostname, status, created_at
            FROM remote_machines
            ORDER BY created_at DESC
            """
        )
        cursor.execute(query)

        machines = cursor.fetchall()

    if not machines:
        print("No machines found in database.")
        return 0

    print(f"Total machines in database: {len(machines)}")
    print()

    # Check each machine
    corrupted_machines = []
    for machine in machines:
        machine_id = machine[0]
        machine_name = machine[1]
        hostname = machine[2]
        status = machine[3]
        created_at = machine[4]

        is_hostname_corrupted = detect_mojibake(hostname)
        is_machine_name_corrupted = detect_mojibake(machine_name)

        if is_hostname_corrupted or is_machine_name_corrupted:
            corrupted_machines.append(
                {
                    "machine_id": machine_id,
                    "machine_name": machine_name,
                    "hostname": hostname,
                    "status": status,
                    "created_at": created_at,
                    "hostname_corrupted": is_hostname_corrupted,
                    "machine_name_corrupted": is_machine_name_corrupted,
                }
            )

    # Report results
    if not corrupted_machines:
        print("✅ No corrupted hostnames detected!")
        print()
        print("All hostname and machine_name fields contain valid UTF-8 encoding.")
        return 0

    # Found corrupted hostnames
    print(f"⚠️  Found {len(corrupted_machines)} machine(s) with corrupted hostnames:")
    print()

    for i, machine in enumerate(corrupted_machines, 1):
        print(f"{i}. Machine ID: {machine['machine_id']}")
        print(f"   Status: {machine['status']}")
        print(f"   Created: {machine['created_at']}")

        if machine["hostname_corrupted"]:
            print(f"   ❌ Hostname (corrupted): {machine['hostname']}")

        if machine["machine_name_corrupted"]:
            print(f"   ❌ Machine Name (corrupted): {machine['machine_name']}")

        print()

    # Provide repair suggestions
    print("=" * 70)
    print("Repair Suggestions:")
    print("=" * 70)
    print()
    print("To fix corrupted hostnames, follow these steps:")
    print()
    print("1. Delete the machine from the admin panel:")
    print("   - Navigate to: Management → Remote Machines")
    print("   - Find the corrupted machine")
    print("   - Click 'Deregister' to delete it")
    print()
    print("2. On the remote machine, stop the Agent:")
    print("   - Open Task Manager")
    print("   - Find and terminate the Python process running agent.py")
    print("   - Or run: Stop-ScheduledTask -TaskName 'OpenACEAgent'")
    print()
    print("3. Delete the Agent configuration directory:")
    print("   - Remove: %USERPROFILE%\\.open-ace-agent")
    print()
    print("4. Re-register the machine:")
    print("   - Get a new registration token from the admin panel")
    print("   - Run the installation script with the new token")
    print("   - Verify that hostname displays correctly")
    print()
    print("=" * 70)
    print()
    print(f"Affected machine IDs (for reference):")
    for machine in corrupted_machines:
        print(f"  - {machine['machine_id']}")

    print()

    # Return exit code
    return 1 if corrupted_machines else 0


if __name__ == "__main__":
    sys.exit(main())