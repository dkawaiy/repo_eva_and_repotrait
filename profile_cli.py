import argparse
import hashlib
import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from api_r.api_main import persist_task_result, run_auto_profile_task
from api_r.vo import AutoProfileTaskRequest
from core.model import DomainModel

ARCHIVE_BASE_DIR = Path("resource/local_archives")
ARCHIVE_CLEANUP: list[Path] = []


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    config_text = config_path.read_text(encoding="utf-8")
    config = json.loads(config_text)
    if not isinstance(config, dict):
        raise ValueError("配置文件顶层结构必须是 JSON 对象")
    return config


def normalize_repo_source(repo_source: str) -> str:
    """将本地路径和远程地址统一为 download_archive 可识别的格式。"""
    if repo_source.startswith("file://"):
        return repo_source

    source_path = Path(repo_source)
    if source_path.exists():
        if source_path.is_dir():
            return archive_directory(source_path)
        return f"file://{source_path.resolve()}"

    return repo_source


def archive_directory(directory: Path) -> str:
    """将本地目录压缩为一个稳定命名的 zip 文件，供 download_archive 使用。"""
    resolved = directory.resolve()
    stem = _safe_name(resolved.name)
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:8]
    archive_path = ARCHIVE_BASE_DIR / f"{stem}_{digest}.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())

    ARCHIVE_CLEANUP.append(archive_path)
    return f"file://{archive_path.as_posix()}"


def parse_repo_entry(entry: dict) -> tuple[str, str]:
    if not isinstance(entry, dict):
        raise ValueError("每个 repos 项必须是对象")

    repo = entry.get("path") or entry.get("repo")
    if not repo:
        raise ValueError("每个 repos 项必须包含 path 或 repo 字段")

    language = entry.get("language") or entry.get("lang")
    if not language:
        raise ValueError("每个 repos 项必须包含 language 或 lang 字段")

    return normalize_repo_source(str(repo)), str(language)


def build_task_request(config: dict[str, Any]) -> AutoProfileTaskRequest:
    domain = config.get("domain")
    if not domain or not isinstance(domain, str):
        raise ValueError("配置文件必须包含 domain 字段，且为字符串")

    repos = config.get("repos")
    if not isinstance(repos, list) or len(repos) == 0:
        raise ValueError("配置文件必须包含非空 repos 列表")

    repo_urls: list[tuple[str, str]] = []
    for entry in repos:
        repo_urls.append(parse_repo_entry(entry))

    domain_model = DomainModel(name=domain, features=[])
    return AutoProfileTaskRequest(
        id=str(uuid.uuid4()),
        domain_model=domain_model.model_dump(),
        repo_urls=repo_urls,
        callback=None,
    )


def run_configured_profile(config_path: Path) -> Path:
    config = load_config(config_path)
    req = build_task_request(config)
    try:
        result = run_auto_profile_task(req)
        path = persist_task_result(result)
        restore_profile_names(config)
        print(result.model_dump_json(indent=2, exclude_none=True, exclude_unset=True))
        print(f"任务结果已保存到: {path}")
        return path
    finally:
        for archive_path in ARCHIVE_CLEANUP:
            if archive_path.exists():
                archive_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="基于配置文件执行自动画像任务，唯一参数为 JSON 配置文件路径。"
    )
    parser.add_argument("config_file", help="JSON 配置文件，例如 profile_cli_example.json")
    args = parser.parse_args(argv)

    try:
        run_configured_profile(Path(args.config_file))
        return 0
    except Exception as exc:
        print(f"执行失败: {exc}")
        return 1


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", text.strip())
    return cleaned or "repo"


def restore_profile_names(config: dict[str, Any]) -> None:
    domain = config.get("domain")
    repos = config.get("repos", [])
    if not domain or not isinstance(repos, list):
        return

    base_dir = Path("profiles") / domain
    if not base_dir.exists():
        return

    desired_names = _build_desired_names(repos)
    repo_urls = [parse_repo_entry(entry)[0] for entry in repos]
    used_targets: set[Path] = set()

    for idx, (repo_url, desired_name) in enumerate(zip(repo_urls, desired_names)):
        software_name = _software_name_from_repo_url(repo_url, idx)
        source_path = base_dir / f"{software_name}_profile.json"
        if not source_path.exists():
            continue

        target_name = _unique_name(desired_name, used_targets, base_dir)
        target_path = base_dir / f"{target_name}_profile.json"
        source_path.rename(target_path)
        used_targets.add(target_path)


def _build_desired_names(repos: list[dict]) -> list[str]:
    names: list[str] = []
    for entry in repos:
        repo = entry.get("path") or entry.get("repo") or "repo"
        parsed = urlparse(str(repo))
        if parsed.scheme and parsed.path:
            stem = Path(parsed.path).stem
        else:
            stem = Path(str(repo)).stem
        names.append(_safe_name(stem) or "repo")
    return names


def _unique_name(name: str, used_targets: set[Path], base_dir: Path) -> str:
    candidate = name
    counter = 1
    while (base_dir / f"{candidate}_profile.json") in used_targets or (base_dir / f"{candidate}_profile.json").exists():
        counter += 1
        candidate = f"{name}_{counter}"
    return candidate


def _software_name_from_repo_url(repo_url: str, index: int) -> str:
    parsed = urlparse(repo_url)
    basename = Path(parsed.path).name
    stem = Path(basename).stem if basename else "repo"
    if not stem:
        stem = f"repo_{index}"
    url_hash = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()[:8]
    return f"{stem}_{url_hash}"


if __name__ == "__main__":
    raise SystemExit(main())
