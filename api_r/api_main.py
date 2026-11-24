"""api_main.py

放置 API 对应的核心业务函数实现（不包含 FastAPI 路由）。

设计目标：
- 输入：领域模型地址 + 一批软件文档地址
- 输出：批次画像结果，并在后端自动决策：
  - 无需演化：返回画像 + 空模型
  - 自动演化：演化模型并用新模型重新画像，返回新模型 + 新画像
  - 需要人工判断：返回未匹配特征供前端判断

说明：FastAPI 路由仅放在项目根目录的 service.py。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlparse
import os
import re

from loguru import logger

from api_r.vo import (
    AutoProfileTaskRequest,
    AutoProfileTaskResult_t,
    InputDocRef,
    TaskStatus,
)
from core.evolver import Evolver
from core.model import DomainModel, SoftwareProfile, parse_single_repository_file
from core.profiler import Profiler
from utils_r.common import fetch_file
from utils_r.common import post

#调用eva_try方法进行度量
from api import eva,EvaResult
from metrics import EvaContext
from utils import LangEnum
from api import download_archive


def _evaluation_features(metric_result: dict) -> List[str]:
    """Keep distinct capabilities visible when a module describes several functions."""
    features = []
    for kind, field in (("函数", "functions"), ("类", "classes"), ("模块", "modules")):
        for item in metric_result.get(field, []):
            description = (item.get("description") or "").strip()
            name = (item.get("name") or "").strip()
            if not description and (kind != "函数" or not name):
                continue
            features.append(f"{kind} {name}：{description}" if name and description
                            else f"{kind} {name}" if name else description)
    return _dedupe_preserve_order(features)




MODEL_DIR = Path("models")
MODEL_HISTORY_DIR = Path("logs/model_history")
_MODEL_LOCKS: dict[str, threading.Lock] = {}
_MODEL_LOCKS_GUARD = threading.Lock()


class DomainModelPreservationError(RuntimeError):
    """Raised when a profile would silently drop features of a custom model."""


def _canonical_model_path(name: str) -> Path:
    return MODEL_DIR / f"{name}.json"


def _legacy_model_path(name: str) -> Path:
    return MODEL_DIR / f"{name}_domain_model.json"


def _model_paths(name: str) -> Tuple[Path, Path]:
    return _canonical_model_path(name), _legacy_model_path(name)


def _model_storage_name(name: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", name).strip("._") or "model"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{safe}_{digest}"


def _model_lock(name: str) -> threading.Lock:
    with _MODEL_LOCKS_GUARD:
        lock = _MODEL_LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _MODEL_LOCKS[name] = lock
        return lock


def _load_persisted_model(name: str) -> Optional[DomainModel]:
    """Load a same-name model so an omitted request cannot erase a custom model."""
    for path in _model_paths(name):
        if not path.is_file():
            continue
        try:
            model = DomainModel.load_model(path)
        except Exception as exc:
            logger.warning(f"[Service] ignore unreadable persisted model {path}: {exc}")
            continue
        if model.name != name:
            logger.warning(f"[Service] ignore persisted model with mismatched name: {path}")
            continue
        if not model.features:
            logger.warning(f"[Service] ignore persisted model without features: {path}")
            continue
        logger.info(
            f"[Service] loaded persisted domain model {path} "
            f"(features={len(model.features)}, version={model.version})"
        )
        return model
    return None


def _collect_feature_ids(model: DomainModel) -> set[str]:
    ids: set[str] = set()
    stack = list(model.features)
    while stack:
        feature = stack.pop()
        feature_id = getattr(feature, "id", None)
        if feature_id:
            ids.add(feature_id)
        stack.extend(getattr(feature, "sub_features", None) or [])
    return ids


def _feature_signature(model: DomainModel) -> str:
    payload = [feature.model_dump(mode="json", exclude_none=True) for feature in model.features]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _next_patch_version(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", (version or "").strip())
    if not match:
        return f"{version}.1" if version else "1.0.1"
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def _ensure_version_advances(original: DomainModel, current: DomainModel) -> None:
    """A changed feature set must not keep the old version number."""
    if _feature_signature(original) == _feature_signature(current):
        return
    if current.version == original.version:
        current.version = _next_patch_version(original.version)
        logger.info(
            f"[Service] feature set changed but version stayed {original.version}; "
            f"bumped to {current.version}"
        )


def _verify_preserved_features(original: Optional[DomainModel], current: DomainModel) -> None:
    """Custom models are a standard: profiling must never delete their feature points."""
    if original is None or not original.features:
        return
    missing = sorted(_collect_feature_ids(original) - _collect_feature_ids(current))
    if missing:
        detail = ", ".join(missing[:10])
        if len(missing) > 10:
            detail += f" 等 {len(missing)} 个"
        raise DomainModelPreservationError(f"画像结果删除了原领域模型的功能点: {detail}")


def _snapshot_model_files(name: str) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in _model_paths(name) if path.is_file()}


def _restore_model_files(name: str, snapshot: dict[Path, bytes]) -> None:
    """Restore canonical/legacy files byte-for-byte; delete files first created by this task."""
    for path in _model_paths(name):
        if path in snapshot:
            if not path.is_file() or path.read_bytes() != snapshot[path]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot[path])
                logger.warning(f"[Service] restored domain model file {path}")
        elif path.exists():
            path.unlink()
            logger.warning(f"[Service] removed domain model file created by failed task: {path}")


def _archive_model_snapshot(model: DomainModel, *, task_id: str, stage: str) -> None:
    """Keep immutable input/output snapshots so every model change is traceable."""
    try:
        directory = MODEL_HISTORY_DIR / _model_storage_name(model.name)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = directory / f"{timestamp}_{stage}_{task_id}_v{model.version}.json"
        model.save_model(path)
        logger.info(f"[Service] archived domain model snapshot: {path}")
    except Exception as exc:
        logger.warning(f"[Service] failed to archive domain model snapshot: {exc}")


def _failed_result(req: AutoProfileTaskRequest, error: Exception, metric_results: dict) -> AutoProfileTaskResult_t:
    message = f"failed: {error}"
    if len(message) > 500:
        message = message[:497] + "..."
    return AutoProfileTaskResult_t(
        id=req.id,
        status=TaskStatus.failed,
        message=message,
        domain_model_name=None,
        domain_model_version=None,
        profiles={},
        metric_results=metric_results,
        evolved_domain_model=None,
    )


def _read_only_model_if_available(
    req: AutoProfileTaskRequest,
    domain_model: DomainModel,
) -> Optional[DomainModel]:
    """Return a deep-copied snapshot for a task that cannot mutate the model.

    Returning None means the request may mutate/bootstrap the model and must keep
    the full write lock. For an omitted feature list, only loading the persisted
    model happens under the lock; profiling runs after the lock is released.
    """
    if req.mode == "evolve":
        return None
    if req.mode not in (None, "evaluate"):
        return None

    if domain_model.features:
        # Request already carries a complete standard; no shared model state is read.
        return domain_model.model_copy(deep=True)

    if req.mode == "evaluate":
        # Keep the existing structured error for an empty explicit standard.
        return None

    # mode=None with omitted features: resolve the same-name persisted model under
    # a short lock, copy it, then release the lock before download/metrics/profile.
    with _model_lock(domain_model.name):
        persisted = _load_persisted_model(domain_model.name)
        if persisted is None:
            return None
        snapshot = persisted.model_copy(deep=True)
    logger.warning(
        "[Service] request omitted domain model features; "
        f"using persisted model {domain_model.name!r} v{snapshot.version}"
    )
    return snapshot


def run_auto_profile_task(req: AutoProfileTaskRequest):
    """对外异步任务的核心实现：自动画像 → 如有未匹配则自动演化 → 重画像 → 返回最终模型。"""
    metric_results: dict[str, dict] = {}
    try:
        domain_model = (
            DomainModel.model_validate_json(req.domain_model)
            if isinstance(req.domain_model, str)
            else DomainModel.model_validate(req.domain_model)
        )
        read_only_model = _read_only_model_if_available(req, domain_model)
        if read_only_model is not None:
            logger.info(
                "[Service] read-only task uses a domain model snapshot; "
                "profiling runs outside the model lock"
            )
            return _execute_auto_profile_task(req, read_only_model, metric_results)
        with _model_lock(domain_model.name):
            return _execute_auto_profile_task(req, domain_model, metric_results)
    except Exception as e:
        logger.exception("Auto profile task failed")
        return _failed_result(req, e, metric_results)


def _execute_auto_profile_task(
    req: AutoProfileTaskRequest,
    domain_model: DomainModel,
    metric_results: dict[str, dict],
) -> AutoProfileTaskResult_t:
    original_model: Optional[DomainModel] = None
    model_files_before: Optional[dict[Path, bytes]] = None
    mutating = False
    try:
        # 前端可能只传名称/版本而丢失 features；优先用同名的本地模型兜底，绝不把它当空模型冷启动。
        # evaluate 是显式的“按标准评估”，不能用本地文件替换用户传入的空标准，必须直接报错。
        if not domain_model.features and req.mode != "evaluate":
            persisted = _load_persisted_model(domain_model.name)
            if persisted is not None:
                logger.warning(
                    "[Service] request omitted domain model features; "
                    f"using persisted model {domain_model.name!r} v{persisted.version}"
                )
                domain_model = persisted

        evaluate_only = req.mode == "evaluate" or (req.mode is None and bool(domain_model.features))
        if evaluate_only and not domain_model.features:
            return AutoProfileTaskResult_t(
                id=req.id,
                status=TaskStatus.failed,
                message="按标准评估需要非空的 domain_model.features",
            )
        if not domain_model.features and not getattr(req, "allow_bootstrap", False):
            return AutoProfileTaskResult_t(
                id=req.id,
                status=TaskStatus.failed,
                message=(
                    "领域模型没有功能点，已拒绝用仓库代码冷启动以避免覆盖自定义模型；"
                    "如确需新建模型，请显式设置 allow_bootstrap=true"
                ),
                domain_model_name=domain_model.name,
                domain_model_version=domain_model.version,
            )

        mutating = not evaluate_only
        if mutating:
            # 保存模型文件原始字节，任何中途失败都精确回滚，包括本次新建的 _domain_model.json。
            original_model = domain_model.model_copy(deep=True)
            model_files_before = _snapshot_model_files(domain_model.name)
            if original_model.features:
                _archive_model_snapshot(original_model, task_id=req.id, stage="input")

        # 先构造度量任务
        repos = req.repo_urls
        software_inputs: List[Tuple[str, List[str]]] = []
        # 维护一个软件名到 url 的映射，后续要用
        software_to_url = {}
        for idx, repo in enumerate(repos):
            repo_url, repo_lang = repo
            path = download_archive(repo_url, repo_lang)
            lang = LangEnum.from_render(repo_lang)

            ctx = EvaContext(
                repo=path,
                lang=lang,
                doc_path=os.path.join("docs", path),
                resource_path=os.path.join("resource", path),
                output_path=os.path.join("output", path),
            )
            eva(ctx, lang)
            repo_doc = ctx.load_repo_doc()
            repo_doc = [repo_doc.model_dump()] if repo_doc is not None else []
            data = EvaResult(
                functions=list(
                    map(lambda x: ctx.function_result(x.signature), filter(lambda x: x.visible, ctx.func_iter()))
                ),
                classes=list(
                    map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(), filter(lambda x: x.visible, ctx.clazz_iter()))
                ),
                modules=list(map(lambda x: x.model_dump(), ctx.load_module_docs())),
                repo=repo_doc,
            ).model_dump()
            result_key = repo_url.split(".com/")[-1]
            metric_results[result_key] = data

            module_doc_path = Path(os.path.join("docs", path, "modules.md"))
            module_doc = parse_single_repository_file(module_doc_path)
            software_name = _software_name_from_repo_url(repo_url, idx)
            # 记录url时去除前部域名 https://smes.oss-cn-heyuan.aliyuncs.com/ 只保留后部key
            software_to_url[software_name] = repo_url.split(".com/")[-1]
            software_inputs.append(
                (
                    software_name,
                    _evaluation_features(data) if evaluate_only else [module.description for module in module_doc.modules],
                )
            )

        # 只有显式 allow_bootstrap=true 才允许用仓库文档从零初始化；自定义模型绝不被隐式替换。
        if not domain_model.features:
            logger.info("[Service] explicit bootstrap allowed, initializing domain model from batch docs")
            _initialize_empty_domain_model_from_inputs(
                domain_model=domain_model,
                software_inputs=software_inputs,
                task_id=req.id,
            )
            if not domain_model.features:
                raise RuntimeError("冷启动初始化后领域模型仍为空")

        # 初始化 Profiler
        profiler = Profiler(domain_model=domain_model)

        # 首次画像（先按列表收集，便于聚合未匹配特征）
        first_pass_profiles: List[SoftwareProfile] = []
        for software_name, raw_features in software_inputs:
            profile = profiler.profile(
                software_name=software_name,
                raw_features=raw_features,
                flag=1,  # batch mode: always refresh to avoid stale cached profiles
            )
            profile.version = domain_model.version
            first_pass_profiles.append(profile)

        # 回调格式统一为 object/map：{software_name: profile}
        profiles: dict[str, dict] = {
            profile.software_name: profile.model_dump(exclude_none=True, exclude_unset=True)
            for profile in first_pass_profiles
        }

        # 聚合未匹配特征
        unmapped_features = _aggregate_unmapped_features(first_pass_profiles)
        logger.info(f"[Service] batch unmapped feature count: {len(unmapped_features)}")
        # 如果有未匹配特征，则演化模型并重新画像
        evolve_applied = False
        if unmapped_features and not evaluate_only:
            logger.info("[Service] unmapped features detected, evolution will be triggered")
            evolver = Evolver(domain_model=domain_model)
            synthetic_profile = SoftwareProfile(software_name="__batch__", unmapped_features=unmapped_features)
            evolve_applied = evolver.evolve(synthetic_profile)
        if evolve_applied:
            # 模型发生变化时重新画像，保持与回调 VO 一致的 map 结构
            profiles = {}
            for software_name, raw_features in software_inputs:
                profile = profiler.profile(
                    software_name=software_name,
                    raw_features=raw_features,
                    flag=1,  # force_refresh
                )
                profile.version = domain_model.version
                profiles[software_name] = profile.model_dump(exclude_none=True, exclude_unset=True)

        # 将 profiles 的 key 都换成 software_to_url 里对应的软件 url，保持和前端输入输出一致
        profiles = {software_to_url.get(k, k): v for k, v in profiles.items()}

        # evaluate 模式严格只读：不保存、不归档、不演化；evolve 才允许新增功能点。
        if not evaluate_only:
            _verify_preserved_features(original_model, domain_model)
            if original_model is not None and original_model.features:
                _ensure_version_advances(original_model, domain_model)
                _verify_preserved_features(original_model, domain_model)
            domain_model.save_model(_canonical_model_path(domain_model.name))
            _archive_model_snapshot(domain_model, task_id=req.id, stage="output")

        return AutoProfileTaskResult_t(
            id=req.id,
            status=TaskStatus.succeeded,
            message="success",
            domain_model_name=domain_model.name,
            domain_model_version=domain_model.version,
            profiles=profiles,
            metric_results=metric_results,
            evolved_domain_model=None if evaluate_only else domain_model,
        )
    except Exception:
        if mutating and model_files_before is not None:
            try:
                _restore_model_files(domain_model.name, model_files_before)
            except Exception:
                logger.exception("[Service] failed to restore domain model files after task failure")
        raise


def persist_task_result(result: AutoProfileTaskResult_t, *, base_dir: Path = Path("logs/tasks")) -> Path:
    """将任务结果落盘，便于后续查询。"""
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"{result.id}.json"
    path.write_text(result.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    return path


def auto_profile_task_worker(req: AutoProfileTaskRequest) -> None:
    """FastAPI BackgroundTasks 的 worker：执行任务、落盘，并按需回调。"""
    logger.info(f"[Service] /profile/batch receive task: {req.model_dump_json()}")
    result = run_auto_profile_task(req)
    path = persist_task_result(result)#这是一个同步函数，直接落盘后续查询即可，无需担心回调时序问题
    logger.info(f"[Service] task result persisted: id={req.id}, path={path}")

    if req.callback:
        try:
            post(req.callback, content=result.model_dump_json(indent=2, exclude_none=False))
        except Exception as e:
            logger.error(f"[Service] callback failed: id={req.id}, err={e}")


def _evolve_and_reprofile(
    *,
    model: DomainModel,
    software_inputs: List[Tuple[str, List[str]]],
    unmapped_features: List[str],
) -> tuple[List[SoftwareProfile], bool]:
    evolver = Evolver(model)
    synthetic_profile = SoftwareProfile(software_name="__batch__", unmapped_features=unmapped_features)
    evolve_applied = evolver.evolve(synthetic_profile)

    # 无论是否真正修改模型，都用“当前模型”重新画像一次，保证返回结果与模型一致
    evolved_profiles = _profile_many(model, software_inputs, force_refresh=True)
    return evolved_profiles, evolve_applied


def _profile_many(
    model: DomainModel,
    software_inputs: List[Tuple[str, List[str]]],
    *,
    force_refresh: bool,
) -> List[SoftwareProfile]:
    profiler = Profiler(model)
    flag = 1 if force_refresh else 0
    profiles: List[SoftwareProfile] = []
    for software_name, raw_features in software_inputs:
        profiles.append(profiler.profile(software_name=software_name, raw_features=raw_features, flag=flag))
    return profiles


def _load_domain_model(model_url: str) -> DomainModel:
    local_path = _local_cache_path(prefix="models/_incoming", key=model_url, suffix=".json")
    fetch_file(model_url, local_path)
    model = DomainModel.load_model(local_path)
    return model


def _load_batch_docs(
    *,
    model_name: str,
    doc_urls: Iterable[str],
) -> tuple[List[Tuple[str, List[str]]], List[InputDocRef]]:
    results: List[Tuple[str, List[str]]] = []
    input_docs: List[InputDocRef] = []
    for idx, url in enumerate(doc_urls):
        doc_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        md_path = _download_doc(model_name=model_name, doc_url=url, index=idx)
        # parse_single_repository_file 的 repo_name 来源于文件名 stem，天然带 url hash 前缀
        repository = parse_single_repository_file(md_path)
        raw_features = [m.description for m in repository.modules]
        results.append((repository.repo_name, raw_features))
        input_docs.append(
            InputDocRef(
                index=idx,
                doc_url=url,
                doc_id=doc_id,
                software_name=repository.repo_name,
            )
        )
    return results, input_docs


def _download_doc(*, model_name: str, doc_url: str, index: int) -> Path:
    basename = _safe_basename_from_url(doc_url) or f"doc_{index}"
    if not basename.endswith(".md"):
        basename = f"{basename}.md"

    # 避免不同 URL 同名覆盖：加入 url hash 前缀
    url_hash = hashlib.sha1(doc_url.encode("utf-8")).hexdigest()[:8]
    local_path = Path(f"resource/{model_name}/{url_hash}_{basename}")
    fetch_file(doc_url, local_path)
    return local_path


def _aggregate_unmapped_features(profiles: List[SoftwareProfile]) -> List[str]:
    all_unmapped: List[str] = []
    for p in profiles:
        all_unmapped.extend(p.unmapped_features or [])
    return _dedupe_preserve_order(all_unmapped)


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        key = (item or "").strip()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _safe_basename_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        name = Path(parsed.path).name
        return name or ""
    except Exception:
        return ""


def _software_name_from_repo_url(repo_url: str, index: int) -> str:
    """为每个 repo 生成稳定且唯一的软件名，避免都落到 modules_profile.json。"""
    basename = _safe_basename_from_url(repo_url)
    stem = Path(basename).stem if basename else "repo"
    stem = stem or f"repo_{index}"
    url_hash = hashlib.sha1(repo_url.encode("utf-8")).hexdigest()[:8]
    return f"{stem}_{url_hash}"


def _local_cache_path(*, prefix: str, key: str, suffix: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return Path(prefix) / f"{digest}{suffix}"


def _initialize_empty_domain_model_from_inputs(
    *,
    domain_model: DomainModel,
    software_inputs: List[Tuple[str, List[str]]],
    task_id: str,
) -> None:
    """用当前批次模块描述构造初始化文档，并复用 DomainModel.initial() 完成冷启动。"""
    if not software_inputs:
        logger.warning("[Service] skip domain bootstrap: software_inputs is empty")
        return

    bootstrap_dir = Path("resource") / "_bootstrap" / f"{domain_model.name}_{_safe_fs_name(task_id)}"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)

    for idx, (software_name, raw_features) in enumerate(software_inputs):
        file_name = f"{idx:03d}_{_safe_fs_name(software_name)}.md"
        file_path = bootstrap_dir / file_name

        lines: List[str] = [f"# {software_name}", ""]
        clean_features = [f.strip() for f in raw_features if isinstance(f, str) and f.strip()]
        if not clean_features:
            clean_features = ["No module description extracted."]

        for f_idx, feature_desc in enumerate(clean_features, start=1):
            lines.extend([
                f"### Feature {f_idx}",
                "#### Description",
                feature_desc,
                "",
            ])

        file_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        domain_model.initial(domain_model.name, str(bootstrap_dir))
        logger.info(
            f"[Service] domain bootstrap finished, feature count={len(domain_model.features)}, "
            f"bootstrap_dir={bootstrap_dir}"
        )
    except Exception as e:
        logger.exception(f"[Service] domain bootstrap failed: {e}")


def _safe_fs_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return "item"
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)