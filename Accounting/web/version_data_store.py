"""
Version Data Store — Ethiopian Accounting System
Tracks application versions, creates tagged snapshots via BackupEngine,
and supports rollback to any previous version.

Storage: JSON-based version registry (versions.json) in data/ directory.
Snapshots: Leverage existing BackupEngine to create/restore zip archives.
"""
import json
import logging
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, 'VERSION')
CHANGELOG_FILE = os.path.join(BASE_DIR, 'CHANGELOG.md')
DATA_DIR = os.path.join(BASE_DIR, 'data')
VERSION_REGISTRY = os.path.join(DATA_DIR, 'versions.json')


def _read_version_file() -> str:
    """Read the current version string from the VERSION file."""
    try:
        with open(VERSION_FILE, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return '0.0.0'


def _write_version_file(version: str):
    """Update the VERSION file."""
    with open(VERSION_FILE, 'w', encoding='utf-8') as f:
        f.write(version)


def _read_changelog() -> str:
    """Read the full changelog."""
    try:
        with open(CHANGELOG_FILE, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ''


class VersionManager:
    """
    Manages application versioning with snapshot-based rollback.

    Each version entry stores:
      - version: Semantic version string (e.g., '1.0.0')
      - released_at: ISO timestamp
      - description: Brief release notes
      - snapshot_archive: Name of the BackupEngine zip archive (for rollback)
      - released_by: User who created the release
      - status: 'active' | 'superseded' | 'rolled-back'
      - source_files_snapshot: Optional code-level snapshot info
    """

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._registry_path = VERSION_REGISTRY
        self._versions = self._load_registry()

    # ── Registry I/O ──────────────────────────────────────────────

    def _load_registry(self) -> list:
        """Load version history from JSON file."""
        if os.path.exists(self._registry_path):
            try:
                with open(self._registry_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except (json.JSONDecodeError, IOError) as e:
                logger.error("Failed to read version registry: %s", e)
                return []
        return []

    def _save_registry(self):
        """Persist version history to JSON file."""
        with open(self._registry_path, 'w', encoding='utf-8') as f:
            json.dump(self._versions, f, indent=2, ensure_ascii=False)

    # ── Public API ────────────────────────────────────────────────

    def get_current_version(self) -> str:
        """Return the current version string from the VERSION file."""
        return _read_version_file()

    def get_changelog(self) -> str:
        """Return the full changelog text."""
        return _read_changelog()

    def list_versions(self) -> list:
        """Return all version entries, newest first."""
        return sorted(
            self._versions,
            key=lambda v: v.get('released_at', ''),
            reverse=True,
        )

    def get_version(self, version: str) -> dict | None:
        """Look up a specific version entry."""
        for v in self._versions:
            if v.get('version') == version:
                return v
        return None

    def get_active_version(self) -> dict | None:
        """Return the currently active version entry."""
        for v in self._versions:
            if v.get('status') == 'active':
                return v
        return None

    def create_version(
        self,
        version: str,
        description: str = '',
        released_by: str = 'system',
        create_snapshot: bool = True,
    ) -> dict:
        """
        Tag a new application version.

        1. Validates the version string.
        2. Creates a data snapshot via BackupEngine (if requested).
        3. Marks all previous versions as 'superseded'.
        4. Records the new version as 'active'.
        5. Updates the VERSION file.

        Returns: dict with success flag and details.
        """
        # Validate version format (semver-like)
        parts = version.split('.')
        if len(parts) < 2 or not all(p.isdigit() for p in parts):
            return {
                'success': False,
                'error': f"Invalid version format: '{version}'. Use MAJOR.MINOR or MAJOR.MINOR.PATCH (e.g., 1.0 or 1.0.0)",
            }

        # Normalize to 3-part
        if len(parts) == 2:
            version = f"{version}.0"

        # Check for duplicate
        if self.get_version(version):
            return {
                'success': False,
                'error': f"Version {version} already exists",
            }

        # Create data snapshot via BackupEngine
        snapshot_archive = None
        if create_snapshot:
            try:
                from backup_data_store import BackupEngine
                engine = BackupEngine()
                result = engine.create_backup(
                    label=f'version-{version}',
                    triggered_by=f'version-release:{released_by}',
                )
                if result.get('success'):
                    snapshot_archive = result.get('archive_name')
                    logger.info("Snapshot created for v%s: %s", version, snapshot_archive)
                else:
                    logger.warning("Snapshot failed for v%s: %s", version, result.get('error'))
            except Exception as e:
                logger.error("Failed to create snapshot for v%s: %s", version, e)

        # Mark previous active version(s) as superseded
        for v in self._versions:
            if v.get('status') == 'active':
                v['status'] = 'superseded'

        # Build the new version entry
        entry = {
            'version': version,
            'released_at': datetime.now().isoformat(),
            'description': description or f'Release {version}',
            'snapshot_archive': snapshot_archive,
            'released_by': released_by,
            'status': 'active',
        }

        self._versions.append(entry)
        self._save_registry()

        # Update VERSION file
        _write_version_file(version)
        logger.info("Version %s created by %s", version, released_by)

        return {'success': True, 'version': entry}

    def rollback_to_version(self, version: str, performed_by: str = 'system') -> dict:
        """
        Rollback the application data to a previously tagged version.

        1. Finds the target version's snapshot archive.
        2. Uses BackupEngine.restore_backup() to restore data.
        3. Updates version statuses.
        4. Updates the VERSION file.

        Returns: dict with success flag and details.
        """
        target = self.get_version(version)
        if not target:
            return {'success': False, 'error': f'Version {version} not found'}

        archive = target.get('snapshot_archive')
        if not archive:
            return {
                'success': False,
                'error': f'Version {version} has no snapshot — rollback not possible',
            }

        # Restore via BackupEngine
        try:
            from backup_data_store import BackupEngine
            engine = BackupEngine()
            result = engine.restore_backup(archive_name=archive, confirm=True)
            if not result.get('success'):
                return {
                    'success': False,
                    'error': f"Restore failed: {result.get('error', 'unknown')}",
                    'details': result,
                }
        except Exception as e:
            logger.error("Rollback to v%s failed: %s", version, e, exc_info=True)
            return {'success': False, 'error': str(e)}

        # Update statuses
        for v in self._versions:
            if v.get('status') == 'active':
                v['status'] = 'rolled-back'

        target['status'] = 'active'
        target['last_restored_at'] = datetime.now().isoformat()
        target['last_restored_by'] = performed_by

        self._save_registry()
        _write_version_file(version)

        logger.info(
            "Rolled back to v%s by %s — restored %d files",
            version, performed_by, result.get('restored_files', 0),
        )

        return {
            'success': True,
            'version': version,
            'restored_files': result.get('restored_files', 0),
            'safety_backup': result.get('safety_backup', ''),
        }

    def delete_version(self, version: str) -> dict:
        """Remove a version entry (does NOT delete the snapshot archive)."""
        target = self.get_version(version)
        if not target:
            return {'success': False, 'error': f'Version {version} not found'}
        if target.get('status') == 'active':
            return {'success': False, 'error': 'Cannot delete the active version'}

        self._versions = [v for v in self._versions if v.get('version') != version]
        self._save_registry()
        logger.info("Version %s deleted", version)
        return {'success': True}

    def seed_initial_version(self):
        """
        Initialize v1.0.0 if no versions exist yet.
        Called once during first app startup.
        """
        if not self._versions:
            self.create_version(
                version='1.0.0',
                description='Initial release — Ethiopian Accounting System with all core modules',
                released_by='system',
                create_snapshot=True,
            )
            logger.info("Seeded initial version v1.0.0")


# ── Module-level singleton ────────────────────────────────────────
version_manager = VersionManager()
