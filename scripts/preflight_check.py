"""Submission preflight without printing secrets or private runtime data."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_CATEGORIES = {
    'base', 'brow', 'eyeshadow', 'eyeliner', 'mascara',
    'contour', 'highlighter', 'blush', 'lip',
}
SCAN_ROOTS = ('makeup_translator', 'scripts', 'tests', 'docs', 'web/src')
SCAN_SUFFIXES = {'.py', '.ts', '.tsx', '.md', '.json'}
SECRET_PATTERNS = (
    re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
    re.compile(r'\bAIza[0-9A-Za-z_-]{30,}\b'),
)


def run(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='also run Python tests and the production web build')
    parser.add_argument('--output', type=Path, help='write the JSON report to this path')
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    for required in ('.env', '.env.*', 'runtime/', 'outputs/', 'web/node_modules/'):
        if required not in gitignore:
            errors.append(f'.gitignore 缺少 {required}')
    if '!.env.example' not in gitignore:
        errors.append('.env.example 应明确允许提交')
    if (ROOT / '.env.save').exists():
        errors.append('根目录存在 .env.save，请移入 runtime/ 或移出项目')
    if not (ROOT / '.env.example').exists():
        errors.append('缺少 .env.example')

    catalog = json.loads((ROOT / 'data/product_catalog.json').read_text(encoding='utf-8'))
    categories = {item.get('category') for item in catalog}
    brands = {item.get('brand') for item in catalog if item.get('brand')}
    if not REQUIRED_CATEGORIES <= categories:
        errors.append('产品目录缺少品类：' + '、'.join(sorted(REQUIRED_CATEGORIES - categories)))
    missing_sources = [item.get('id', '<unknown>') for item in catalog if not item.get('source_url')]
    if missing_sources:
        errors.append(f'{len(missing_sources)} 条目录记录缺少 source_url')

    scanned_files = 0
    for root_name in SCAN_ROOTS:
        for path in (ROOT / root_name).rglob('*'):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in {'node_modules', 'dist', '__pycache__'} for part in path.parts):
                continue
            scanned_files += 1
            text = path.read_text(encoding='utf-8', errors='replace')
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                errors.append(f'疑似密钥出现在 {path.relative_to(ROOT)}')

    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    implementation = (ROOT / 'docs/agent-implementation.md').read_text(encoding='utf-8')
    for stale in ('54 条有来源', '47 项通过', 'Qwen 真实分析'):
        if stale in readme or stale in implementation:
            errors.append(f'文档仍包含过期表述：{stale}')
    if not (ROOT / 'web/dist/index.html').exists():
        warnings.append('尚无前端生产构建；运行 cd web && npm run build')

    if errors:
        report = {'status': 'failed', 'errors': errors, 'warnings': warnings}
    else:
        if args.full:
            run([str(ROOT / '.venv/bin/pytest'), '-q'], ROOT)
            run(['npm', 'run', 'build'], ROOT / 'web')
        report = {
            'status': 'passed',
            'catalog_records': len(catalog),
            'catalog_brands': len(brands),
            'catalog_categories': sorted(categories),
            'scanned_files': scanned_files,
            'full_checks': args.full,
            'warnings': warnings,
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        destination = args.output if args.output.is_absolute() else ROOT / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding='utf-8')
    print(rendered, end='')
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
