"""Fidelity check for Markdown rewrites against the committed version (HEAD).

    .venv/bin/python research/results/docs_rewrite/check_fidelity.py <file list> [--json out.json]

For each file: numbers, link targets and inline-code spans present in HEAD must still be present after the
rewrite (set containment); no relative link may break that resolved before (links already broken in HEAD,
e.g. to gitignored outputs, are not counted). Prints one line per file and
exits non-zero if any file has missing tokens or broken links.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NUMBER = re.compile(r'(?<![\w.])[-+−]?\d+(?:[.,]\d+)*%?')
LINK = re.compile(r'\]\(([^)\s]+)\)')
CODE = re.compile(r'`([^`\n]+)`')


def tokens(text: str) -> dict:
    return dict(numbers=set(NUMBER.findall(text)), links=set(LINK.findall(text)), code=set(CODE.findall(text)))


def broken_links(path: Path, text: str) -> list:
    out = []
    for target in LINK.findall(text):
        if re.match(r'^[a-z]+:', target) or target.startswith('#'):
            continue
        file = target.split('#')[0]
        if file and not (path.parent / file).resolve().exists():
            out.append(target)
    return out


def main(argv):
    files = [l.strip() for l in Path(argv[1]).read_text().splitlines() if l.strip()]
    report, bad = {}, 0
    for name in files:
        old = subprocess.run(['git', 'show', f'HEAD:{name}'], cwd=ROOT, capture_output=True, text=True).stdout
        new = (ROOT / name).read_text()
        a, b = tokens(old), tokens(new)
        missing = {k: sorted(a[k] - b[k]) for k in a if a[k] - b[k]}
        broken = sorted(set(broken_links(ROOT / name, new)) - set(broken_links(ROOT / name, old)))  # new breakage only
        report[name] = dict(old_chars=len(old), new_chars=len(new), missing=missing, broken_links=broken)
        ok = not missing and not broken
        bad += not ok
        print(f'{"OK  " if ok else "FAIL"} {len(old):>6} -> {len(new):>6}  {name}'
              + ('' if ok else f'  missing={ {k: len(v) for k, v in missing.items()} } broken={len(broken)}'))
    if '--json' in argv:
        Path(argv[argv.index('--json') + 1]).write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f'files {len(files)}  failing {bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
