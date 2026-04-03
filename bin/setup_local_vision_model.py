from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path


DEFAULT_MODEL_ID = 'google/vit-base-patch16-224-in21k'
DEFAULT_TORCH_VERSION = '2.5.1'
TORCH_CPU_INDEX_URL = 'https://download.pytorch.org/whl/cpu'


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sanitize_model_name(model_id: str) -> str:
    value = model_id.strip().lower()
    value = value.replace('\\', '/')
    value = value.split('/')[-1] if '/' in value else value
    value = re.sub(r'[^a-z0-9._-]+', '-', value)
    return value or 'vision-model'


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f'[run] {" ".join(cmd)}')
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _install_dependencies(python_exe: Path, requirements_file: Path) -> None:
    if not requirements_file.exists():
        raise FileNotFoundError(f'requirements file missing: {requirements_file}')
    _run(
        [
            str(python_exe),
            '-m',
            'pip',
            'install',
            '--index-url',
            TORCH_CPU_INDEX_URL,
            f'torch=={DEFAULT_TORCH_VERSION}',
        ]
    )
    _run([str(python_exe), '-m', 'pip', 'install', '-r', str(requirements_file)])


def _upsert_env(env_file: Path, updates: dict[str, str]) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_file.exists():
        lines = env_file.read_text(encoding='utf-8').splitlines()

    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            output.append(line)
            continue
        key, _ = line.split('=', 1)
        key = key.strip()
        if key in remaining:
            output.append(f'{key}={remaining.pop(key)}')
        else:
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append('')
        output.append('# Vision local model')
        for key, value in remaining.items():
            output.append(f'{key}={value}')

    env_file.write_text('\n'.join(output) + '\n', encoding='utf-8')
    print(f'[ok] updated env file: {env_file}')


def main() -> int:
    parser = argparse.ArgumentParser(description='Install and localize BetterPPT vision model runtime.')
    parser.add_argument('--backend-root', type=Path, default=_repo_root() / 'source' / 'backend')
    parser.add_argument('--model-id', default=os.getenv('BETTERPPT_TEMPLATE_VISION_MODEL', DEFAULT_MODEL_ID))
    parser.add_argument('--target-dir', type=Path, default=None)
    parser.add_argument('--cache-dir', type=Path, default=None)
    parser.add_argument('--env-file', type=Path, default=None)
    parser.add_argument('--skip-install-deps', action='store_true')
    parser.add_argument('--skip-download', action='store_true')
    parser.add_argument('--skip-env-write', action='store_true')
    args = parser.parse_args()

    backend_root = args.backend_root.resolve()
    python_exe = backend_root / '.venv' / 'Scripts' / 'python.exe'
    requirements_file = backend_root / 'requirements-vision.txt'
    env_file = (args.env_file or backend_root / '.env').resolve()
    model_name = _sanitize_model_name(args.model_id)
    target_dir = (args.target_dir or (backend_root / 'models' / 'vision' / model_name)).resolve()
    cache_dir = (args.cache_dir or (backend_root / 'models' / 'hf_cache')).resolve()

    if not python_exe.exists():
        raise FileNotFoundError(f'python executable not found: {python_exe}')

    print(f'[info] backend root: {backend_root}')
    print(f'[info] python: {python_exe}')
    print(f'[info] model id: {args.model_id}')
    print(f'[info] model dir: {target_dir}')
    print(f'[info] cache dir: {cache_dir}')

    if not args.skip_install_deps:
        _install_dependencies(python_exe, requirements_file)

    if not args.skip_download:
        _run(
            [
                str(python_exe),
                '-c',
                (
                    'from huggingface_hub import snapshot_download;'
                    f"snapshot_download(repo_id={args.model_id!r}, local_dir={str(target_dir)!r}, "
                    f"cache_dir={str(cache_dir)!r}, local_dir_use_symlinks=False)"
                ),
            ]
        )

    _run(
        [
            str(python_exe),
            '-c',
            (
                'from transformers import AutoImageProcessor,AutoModel,AutoProcessor;'
                f"model_dir={str(target_dir)!r};cache_dir={str(cache_dir)!r};"
                'proc_cls=AutoImageProcessor if AutoImageProcessor is not None else AutoProcessor;'
                "proc_cls.from_pretrained(model_dir, local_files_only=True, cache_dir=cache_dir);"
                "m=AutoModel.from_pretrained(model_dir, local_files_only=True, cache_dir=cache_dir);"
                'm.eval();print("verify-ok")'
            ),
        ]
    )

    if not args.skip_env_write:
        _upsert_env(
            env_file,
            {
                'BETTERPPT_TEMPLATE_VISION_MODEL': args.model_id,
                'BETTERPPT_TEMPLATE_VISION_MODEL_PATH': str(target_dir),
                'BETTERPPT_TEMPLATE_VISION_CACHE_DIR': str(cache_dir),
            },
        )

    print('[done] local vision model setup complete')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
