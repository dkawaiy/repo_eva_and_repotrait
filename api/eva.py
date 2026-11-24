import hashlib
import json
import os
import re
import shutil
import sys
import requests
from loguru import logger


from metrics import EvaContext, CParser, FunctionMetric, ClazzMetric, ModuleMetric, RepoV2Metric, JSlangParser, \
    ModuleV2Metric,arktsParser, RustParser, RustFunctionMetric, RustClazzMetric, TSAnalyzer,ModuleV5Metric
from metrics.metric import is_test_path
from utils import LangEnum, post, resolve_archive
from .vo import EvaResult, RAResult, RAStatus, RATask


_TS_IMPL_SUFFIXES = ('.ts', '.tsx')
_JS_IMPL_SUFFIXES = ('.js', '.jsx', '.mjs', '.cjs')
_LANG_SCAN_SKIP_DIRS = {'node_modules', '.git'}


def resolve_effective_lang(resource_path: str, lang: LangEnum) -> LangEnum:
    """按实际实现文件修正语言，避免把仅有 .d.ts 的 JS 仓库当 TypeScript 解析。

    有些 npm 包在 package.json 中声明 types: index.d.ts，但实现是 index.js；
    请求方却按 TypeScript 提交。TSAnalyzer 只收集 .ts/.tsx/.d.ts，会把声明文件
    当成函数，既看不到实现，也识别不了箭头函数。这里仅在没有非声明 TS 实现、
    但存在 JS 实现时回退到 JavaScript 解析。测试/基准目录不计入判定。
    """
    if lang != LangEnum.typescript:
        return lang
    has_ts_impl = False
    has_js_impl = False
    for root, dirs, files in os.walk(resource_path):
        dirs[:] = [item for item in dirs if item not in _LANG_SCAN_SKIP_DIRS]
        for name in files:
            lower = name.lower()
            if lower.endswith('.d.ts'):
                continue
            # 构建/格式化工具的 .ts 配置不是库实现，不能据此判定仓库是 TypeScript。
            if re.search(r'\.config\.(ts|tsx)$', lower):
                continue
            rel_path = os.path.relpath(os.path.join(root, name), resource_path)
            if is_test_path(rel_path):
                continue
            if lower.endswith(_TS_IMPL_SUFFIXES):
                has_ts_impl = True
            elif lower.endswith(_JS_IMPL_SUFFIXES):
                has_js_impl = True
    if has_js_impl and not has_ts_impl:
        logger.warning(
            f"[lang] {resource_path} 只有 JS 实现（或仅 .d.ts 声明），"
            f"TypeScript 解析回退为 JavaScript"
        )
        return LangEnum.javascript
    return lang


def eva(ctx: EvaContext, lang: LangEnum):
    lang = resolve_effective_lang(ctx.resource_path, lang)
    ctx.lang = lang
    # 预处理，生成函数调用图、类调用图
    if lang == LangEnum.cpp:
        CParser().eva(ctx)
    elif lang == LangEnum.javascript:
        JSlangParser().eva(ctx)
    elif lang == LangEnum.rust:
        RustParser().eva(ctx)
    elif lang == LangEnum.arkts:
        arktsParser().eva(ctx)
    elif lang == LangEnum.typescript:
        TSAnalyzer().eva(ctx)
    else:
        raise NotImplementedError(f'{lang} not supported')
    # 生成软件目录结构，TODO：暂时不用了
    # StructureMetric().eva(ctx)
    # 生成函数文档 - 对Rust语言使用增强版本
    if lang == LangEnum.rust:
        RustFunctionMetric().eva(ctx)
    else:
        FunctionMetric().eva(ctx)
    # 生成类文档 - 对Rust语言使用增强版本
    if lang == LangEnum.rust:
        RustClazzMetric().eva(ctx)
    else:
        ClazzMetric().eva(ctx)
    # 生成模块文档，若API数量过多，则使用V5版本
    if len(ctx.api_iter()) > 300:
        ModuleV5Metric().eva(ctx,merge_op=True)
    else:
        ModuleV5Metric().eva(ctx)
    # 生成仓库文档
    if sys.platform.startswith("win"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    RepoV2Metric().eva(ctx)
    mark_docs_complete(ctx)

def eva_try(req: RATask,ctx:EvaContext,lang:LangEnum):
    lang = resolve_effective_lang(ctx.resource_path, lang)
    ctx.lang = lang
    # 预处理，生成函数调用图、类调用图
    if lang == LangEnum.cpp:
        CParser().eva(ctx)
    elif lang == LangEnum.javascript:
        JSlangParser().eva(ctx)
    elif lang == LangEnum.rust:
        RustParser().eva(ctx)
    elif lang == LangEnum.arkts:
        arktsParser().eva(ctx)
    elif lang == LangEnum.typescript:
        TSAnalyzer().eva(ctx)
    else:
        raise NotImplementedError(f'{lang} not supported')
    # 生成软件目录结构，TODO：暂时不用了
    # StructureMetric().eva(ctx)
    # 生成函数文档
    try:
        if lang == LangEnum.rust:
            RustFunctionMetric().eva(ctx)
        else:
            FunctionMetric().eva(ctx)
    except Exception as e:
        logger.error(f'fail to generate doc for {req.repo}, err={e}')
        # 回调传错误
        data = json.dumps({'repo': {'description': str(e)}})
        post(req.callback,
             content=RAResult(id=req.id, status=RAStatus.function_fail.value, message=str(e),
                              result=data).model_dump_json(
                 exclude_none=True, exclude_unset=True))
        return 0
    
    # 生成类文档
    try:
        if lang == LangEnum.rust:
            RustClazzMetric().eva(ctx)
        else:
            ClazzMetric().eva(ctx)
    except Exception as e:
        logger.error(f'fail to generate doc for {req.repo}, err={e}')
        # 回调传错误
        data = EvaResult(functions=list(map(lambda x: ctx.function_result(x.signature),
                                            filter(lambda x: x.visible, ctx.func_iter()))),
                         classes=[],
                         modules=[],
                         repo=[])
        post(req.callback,
             content=RAResult(id=req.id, status=RAStatus.clazz_fail.value, message=str(e),
                              result=data.model_dump_json()).model_dump_json(
                 exclude_none=True, exclude_unset=True))
        return 0
    # 生成模块文档，若API数量过多，则使用V2版本
    if len(ctx.api_iter()) > 300:
        try:
            ModuleV5Metric().eva(ctx,merge_op=True)
        except Exception as e:
            logger.error(f'fail to generate doc for {req.repo}, err={e}')
            # 回调传错误
            data = EvaResult(functions=list(map(lambda x: ctx.function_result(x.signature),
                                                filter(lambda x: x.visible, ctx.func_iter()))),
                            classes=list(map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(),
                                            filter(lambda x: x.visible, ctx.clazz_iter()))),
                            modules=[],
                            repo=[])
            post(req.callback,
                content=RAResult(id=req.id, status=RAStatus.module_fail.value, message=str(e),
                                result=data.model_dump_json()).model_dump_json(
                    exclude_none=True, exclude_unset=True))
            return 0
    else:
        try:
            ModuleV5Metric().eva(ctx)
        except Exception as e:
            logger.error(f'fail to generate doc for {req.repo}, err={e}')
            # 回调传错误
            data = EvaResult(functions=list(map(lambda x: ctx.function_result(x.signature),
                                                filter(lambda x: x.visible, ctx.func_iter()))),
                            classes=list(map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(),
                                            filter(lambda x: x.visible, ctx.clazz_iter()))),
                            modules=[],
                            repo=[])
            post(req.callback,
                content=RAResult(id=req.id, status=RAStatus.module_fail.value, message=str(e),
                                result=data.model_dump_json()).model_dump_json(
                    exclude_none=True, exclude_unset=True))
            return 0
    # 生成仓库文档
    if sys.platform.startswith("win"):
        os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    try:
        RepoV2Metric().eva(ctx)
    except Exception as e:
        logger.error(f'fail to generate doc for {req.repo}, err={e}')
        # 回调传错误
        data = EvaResult(functions=list(map(lambda x: ctx.function_result(x.signature),
                                            filter(lambda x: x.visible, ctx.func_iter()))),
                         classes=list(map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(),
                                          filter(lambda x: x.visible, ctx.clazz_iter()))),
                         modules=list(map(lambda x: x.model_dump(), ctx.load_module_docs())),
                         repo=[])
        post(req.callback,
             content=RAResult(id=req.id, status=RAStatus.repo_fail.value, message=str(e),
                              result=data.model_dump_json()).model_dump_json(
                 exclude_none=True, exclude_unset=True))
        return 0
    # /tools/hcl 走 eva_try 而不是 eva()，这里也必须写完整标记，否则每次都会重建文档。
    mark_docs_complete(ctx)
    return 1


def eva_with_response(req: RATask):
    try:
        path = download_archive(req.repo, req.language)
        lang = LangEnum.from_render(req.language)
        ctx = EvaContext(repo=req.name, lang=lang, doc_path=os.path.join('docs', path),
                         resource_path=os.path.join('resource', path),
                         output_path=os.path.join('output', path))
        if not eva_try(req,ctx, lang):
            return
        repo_doc = ctx.load_repo_doc()
        repo_doc = [repo_doc.model_dump()] if repo_doc is not None else []
        data = EvaResult(functions=list(map(lambda x: ctx.function_result(x.signature),
                                            filter(lambda x: x.visible, ctx.func_iter()))),
                         classes=list(map(lambda x: ctx.load_clazz_doc(x.signature).model_dump(),
                                          filter(lambda x: x.visible, ctx.clazz_iter()))),
                         modules=list(map(lambda x: x.model_dump(), ctx.load_module_docs())),
                         repo=repo_doc)

        # 回调传结果，重试几次
        post(req.callback,
             content=RAResult(id=req.id,
                              status=RAStatus.success.value,
                              message='ok',
                              result=data.model_dump_json())
             .model_dump_json(exclude_none=True, exclude_unset=True))
    except Exception as e:
        logger.error(f'fail to generate doc for {req.repo}, err={e}')
        # 回调传错误
        data = json.dumps({'repo': {'description': str(e)}})
        post(req.callback,
             content=RAResult(id=req.id, status=RAStatus.fail.value, message=str(e),
                              result=data).model_dump_json(
                 exclude_none=True, exclude_unset=True))


# 文档缓存版本：模块/仓库文档生成逻辑变化时递增，避免复用旧口径的文档。
_DOC_CACHE_VERSION = 'issue32-40-docs-v5'


def _archive_cache_key(content: bytes, lang: str = '') -> str:
    """按归档内容与语言生成稳定目录名，保证同一份代码复用同一套文档。"""
    digest = hashlib.sha256()
    digest.update(_DOC_CACHE_VERSION.encode('utf-8'))
    digest.update(b'\0')
    digest.update(str(lang or '').encode('utf-8'))
    digest.update(b'\0')
    digest.update(content)
    return digest.hexdigest()[:32]


def mark_docs_complete(ctx: EvaContext) -> None:
    """标记本次文档已完整生成；只有带标记的缓存目录才会被后续任务复用。"""
    marker = os.path.join(ctx.doc_path, '.complete')
    with open(marker, 'w', encoding='utf-8') as f:
        f.write(_DOC_CACHE_VERSION)


# 下载仓库
def download_archive(repo: str, lang: str = '') -> str:
    if repo.startswith('file://'):
        repo = repo[7:]
        if os.path.exists(repo) and os.path.isfile(repo):
            with open(repo, 'rb') as f:
                logger.info(f'fetch repo from local file {repo} {os.path.getsize(repo)}')
                content = f.read()
        else:
            raise FileNotFoundError(repo)
    else:
        response = requests.get(repo)
        if response.status_code == 403:
            raise Exception(response.text)
        logger.info(f'fetch repo {response.status_code} {len(response.content)}')
        content = response.content

    archive = resolve_archive(content)
    if archive is None:
        raise ValueError('unsupported archive format')

    # 同一份归档内容(含语言)总是落到同一个目录：后续任务可直接复用
    # 已生成的函数/模块/仓库文档，避免同一代码两次评估得到不同模块划分与简介。
    save_path = _archive_cache_key(content, lang)
    resource_dir = os.path.join('resource', save_path)
    docs_dir = os.path.join('docs', save_path)
    if os.path.isdir(resource_dir):
        if not os.path.exists(os.path.join(docs_dir, '.complete')):
            logger.info(f'reuse source {save_path}, but docs cache is incomplete; rebuild docs')
            shutil.rmtree(docs_dir, ignore_errors=True)
            shutil.rmtree(os.path.join('output', save_path), ignore_errors=True)
        else:
            logger.info(f'reuse cached source and docs for {save_path}')
        return save_path

    archive.decompress(resource_dir)
    return save_path
