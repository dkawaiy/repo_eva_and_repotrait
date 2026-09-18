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
    StageIssue,
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




def run_auto_profile_task(req: AutoProfileTaskRequest):
    """对外异步任务的核心实现：自动画像 → 如有未匹配则自动演化 → 重画像 → 返回最终模型。

    技术性失败（下载/度量/画像/演化中的异常，以及模块生成过程中被捕获的问题）不会被伪装成
    业务上的“未匹配功能”：失败画像显式标记 failed 且不参与演化；所有阶段问题都会汇总到
    最终结果的 issues 中，并体现在 status（succeeded/partial/failed）上。
    """
    metric_results: dict[str, dict] = {}
    software_to_url: dict[str, str] = {}
    software_inputs: List[Tuple[str, List[str]]] = []
    issues: List[StageIssue] = []
    failed_software_names: set[str] = set()
    try:
        #先构造度量任务
        repos = req.repo_urls
        for idx, repo in enumerate(repos):
            repo_url, repo_lang = repo
            software_name = _software_name_from_repo_url(repo_url, idx)
            try:
                path = download_archive(repo_url)
                lang = LangEnum.from_render(repo_lang)

                ctx = EvaContext(repo=path, lang=lang,
                                doc_path=os.path.join('docs', path), resource_path=os.path.join('resource', path),
                                output_path=os.path.join('output', path))
                '''
            ###########
            #测试用，节省时间和token，把已有的文档放进去
            #把已有结果路径改成repo_url对应的路径，避免重复度量造成token和时间浪费
            base_path = '/root/docs/1b8be0d9e4164af9be6b1c96e5188e6c'
            #把base_path下的md文档复制粘贴到doc_path路径下
            for file in os.listdir(base_path):
                if file.endswith('.md'):
                    src_file = os.path.join(base_path, file)
                    dst_file = os.path.join(ctx.doc_path, file)
                    if not os.path.exists(dst_file):
                        with open(src_file, 'r', encoding='utf-8') as f_src:
                            content = f_src.read()
                        with open(dst_file, 'w', encoding='utf-8') as f_dst:
                            f_dst.write(content)
            ###########
            '''

                eva(ctx, lang)
                # 模块/文档生成过程中被捕获的异常需要进入最终结果，而不只留在日志里
                for stage, message in ctx.errors:
                    issues.append(StageIssue(scope=software_name, stage=stage, message=message))

                repo_doc = ctx.load_repo_doc()
                repo_doc = [repo_doc.model_dump()] if repo_doc is not None else []
                data = EvaResult(functions=list(map(lambda x: ctx.load_function_doc(x.signature).model_dump(),
                                                filter(lambda x: x.visible, ctx.func_iter()))),
                             classes=list(map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(),
                                              filter(lambda x: x.visible, ctx.clazz_iter()))),
                             modules=list(map(lambda x: x.model_dump(), ctx.load_module_docs())),
                             repo=repo_doc).model_dump()
                result_key = repo_url.split('.com/')[-1]
                metric_results[result_key] = data

                module_doc_path = Path(os.path.join('docs', path, 'modules.md'))
                module_doc = parse_single_repository_file(module_doc_path)
                if not module_doc.modules:
                    # modules.md 存在但未解析出任何模块：显式记录，避免“空画像=成功”的假象
                    issues.append(StageIssue(
                        scope=software_name,
                        stage="module_extract",
                        message="modules.md 未解析出任何模块描述，画像输入为空",
                    ))
                #记录url时去除前部域名https://smes.oss-cn-heyuan.aliyuncs.com/只保留后部key
                software_to_url[software_name] = repo_url.split('.com/')[-1]
                software_inputs.append(
                    (software_name, [module.description for module in module_doc.modules])
                )
            except Exception as e:
                # 单个仓库的技术性失败不拖垮整批任务：记录问题并标记该仓库失败，继续处理后续仓库
                logger.exception(f"[Service] metric/doc generation failed for repo: {repo_url}")
                failed_software_names.add(software_name)
                issues.append(StageIssue(
                    scope=software_name,
                    stage="metric",
                    message=f"{type(e).__name__}: {e}",
                ))
        if not software_inputs:
            raise RuntimeError("全部仓库均未完成度量与模块文档生成")

        # 加载领域模型
        domain_model = DomainModel.model_validate(req.domain_model)
        domain_model.save_model(Path(os.path.join("models", f"{domain_model.name}.json")))
        # 若领域模型为空，则基于本批次已生成的模块文档进行一次初始化。
        if not domain_model.features:
            logger.info("[Service] empty domain model detected, trying bootstrap initialization from batch docs")
            bootstrap_error = _initialize_empty_domain_model_from_inputs(
                domain_model=domain_model,
                software_inputs=software_inputs,
                task_id=req.id,
            )
            if bootstrap_error:
                issues.append(StageIssue(
                    scope=domain_model.name,
                    stage="domain_bootstrap",
                    message=bootstrap_error,
                ))
            domain_model.save_model(Path(os.path.join("models", f"{domain_model.name}.json")))
        # 首次画像（先按列表收集，便于聚合未匹配特征）
        first_pass_profiles = _profile_many(
            domain_model, software_inputs, force_refresh=True, version=domain_model.version,
        )

        # 聚合未匹配特征：只使用生成成功的画像。
        # 生成失败（技术性问题）的画像已被标记为 failed，其 unmapped_features 不代表业务未匹配，
        # 不能作为领域模型演化的输入，否则技术错误会被误当成新增领域知识。
        valid_profiles = [p for p in first_pass_profiles if p.is_valid]
        unmapped_features = _aggregate_unmapped_features(valid_profiles)
        logger.info(f"[Service] batch unmapped feature count: {len(unmapped_features)}")

        # 如果有未匹配特征，则演化模型并重新画像
        final_profiles = first_pass_profiles
        if unmapped_features:
            logger.info("[Service] unmapped features detected, evolution will be triggered")
            evolver = Evolver(domain_model=domain_model)
            synthetic_profile = SoftwareProfile(software_name="__batch__", unmapped_features=unmapped_features)
            try:
                evolver.evolve(synthetic_profile)
                # 重新画像，保持与回调 VO 一致的 map 结构
                final_profiles = _profile_many(
                    domain_model, software_inputs, force_refresh=True, version=domain_model.version,
                )
            except Exception as e:
                logger.exception("[Service] evolution/reprofile failed")
                issues.append(StageIssue(
                    scope=domain_model.name,
                    stage="evolution",
                    message=f"{type(e).__name__}: {e}",
                ))
        else:
            logger.info("[Service] no unmapped features, evolution skipped")

        # 画像失败信息显式进入最终结果（避免只留在日志里）
        for profile in final_profiles:
            if not profile.is_valid:
                issues.append(StageIssue(
                    scope=profile.software_name,
                    stage="profile",
                    message=profile.error or "profile generation failed",
                ))

        # 回调格式统一为 object/map：{software_name: profile}
        profiles = _profiles_to_dict(final_profiles)
        #将profiles的key都换成software_to_url里对应的软件url，保持和前端输入输出一致
        profiles = {software_to_url.get(k, k): v for k, v in profiles.items()}

        # 统一阶段结果：明确成功、部分成功、失败及原因
        failed_count = len(failed_software_names) + sum(1 for p in final_profiles if not p.is_valid)
        succeeded_count = max(0, len(repos) - failed_count)
        if succeeded_count == 0:
            status = TaskStatus.failed
            message = f"failed: 0/{len(repos)} repos produced a usable profile"
        elif failed_count == 0 and not issues:
            status = TaskStatus.succeeded
            message = "success"
        else:
            status = TaskStatus.partial
            message = f"partial success: {succeeded_count}/{len(repos)} repos succeeded, {len(issues)} issue(s) recorded"
        return AutoProfileTaskResult_t(
            id=req.id,
            status=status,
            message=message,
            domain_model_name=domain_model.name,
            domain_model_version=domain_model.version,
            profiles=profiles,
            metric_results=metric_results,
            evolved_domain_model=domain_model,
            issues=issues,
        )
    except Exception as e:
        logger.exception("Auto profile task failed")
        issues.append(StageIssue(scope=req.id, stage="task", message=f"{type(e).__name__}: {e}"))
        return AutoProfileTaskResult_t(
            id=req.id,
            status=TaskStatus.failed,
            message=f"failed: {type(e).__name__}: {e}",
            domain_model_name=None,
            domain_model_version=None,
            profiles={},
            metric_results=metric_results,
            evolved_domain_model=None,
            issues=issues,
        )


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


def _profile_many(
    model: DomainModel,
    software_inputs: List[Tuple[str, List[str]]],
    *,
    force_refresh: bool,
    version: Optional[str] = None,
) -> List[SoftwareProfile]:
    """批量画像。单个软件的失败只标记该画像为 failed，不影响其他软件与整体批次。"""
    profiler = Profiler(model)
    flag = 1 if force_refresh else 0
    profiles: List[SoftwareProfile] = []
    for software_name, raw_features in software_inputs:
        try:
            profile = profiler.profile(software_name=software_name, raw_features=raw_features, flag=flag)
        except Exception as e:
            logger.exception(f"[Service] profiling raised for '{software_name}'")
            profile = SoftwareProfile(
                software_name=software_name,
                status="failed",
                error=f"{type(e).__name__}: {e}",
                unmapped_features=[],
            )
        if version is not None:
            profile.version = version
        profiles.append(profile)
    return profiles


def _profiles_to_dict(profiles: List[SoftwareProfile]) -> dict[str, dict]:
    """回调格式统一为 object/map：{software_name: profile}。"""
    return {
        profile.software_name: profile.model_dump(exclude_none=True, exclude_unset=True)
        for profile in profiles
    }


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
    """聚合业务未匹配特征。生成失败的画像（status=failed）不是业务未匹配，必须排除。"""
    all_unmapped: List[str] = []
    for p in profiles:
        if not p.is_valid:
            continue
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
) -> Optional[str]:
    """用当前批次模块描述构造初始化文档，并复用 DomainModel.initial() 完成冷启动。

    返回 None 表示成功；否则返回错误信息（会进入最终任务结果的 issues）。
    """
    if not software_inputs:
        logger.warning("[Service] skip domain bootstrap: software_inputs is empty")
        return "skip domain bootstrap: software_inputs is empty"

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
    except Exception as e:
        logger.exception(f"[Service] domain bootstrap failed: {e}")
        return f"{type(e).__name__}: {e}"

    if not domain_model.features:
        logger.error(f"[Service] domain bootstrap produced no features, bootstrap_dir={bootstrap_dir}")
        return "domain bootstrap produced no features"

    logger.info(
        f"[Service] domain bootstrap finished, feature count={len(domain_model.features)}, "
        f"bootstrap_dir={bootstrap_dir}"
    )
    return None


def _safe_fs_name(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return "item"
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)
