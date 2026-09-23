"""Verify the compact release, not the archived all-trial study."""
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v2_offcial_best_deep_tuning as entry


def main():
    manifest = json.loads((ROOT / 'config/fintune_v2_release.json').read_text())
    errors = []
    for rel, expected in manifest['files'].items():
        p = ROOT / rel
        if not p.is_file():
            errors.append('Missing: ' + rel)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            errors.append('Changed: ' + rel)
    if errors:
        raise ValueError('\n'.join(errors))
    entry.verify_release()
    audit = json.loads((entry.STUDY / 'audit.json').read_text())
    count = 0
    for rel, digest in audit['artifact_hashes'].items():
        path = entry.STUDY / rel
        if str(path.relative_to(ROOT)) in manifest['files']:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Retained audited artifact changed: ' + rel)
            count += 1
    print(json.dumps(dict(status='PASS', fixed_candidate='f0019',
        release_files=len(manifest['files']), retained_audited_artifacts=count,
        scope='COMPACT_RELEASE_INTEGRITY_NOT_NEW_OOS_OR_FULL_ARCHIVED_TRIAL_AUDIT',
        submission_status='BLOCK_SUBMISSION'), indent=2))


if __name__ == '__main__':
    main()
