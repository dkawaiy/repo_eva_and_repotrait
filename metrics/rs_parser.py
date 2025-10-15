import json
import os
import subprocess
import sys
from os.path import join as pjoin
from typing import Set, List
import shutil

import networkx as nx
from loguru import logger

from utils import remove_cycle
from . import ClazzDef
from .metric import Metric, EvaContext, FuncDef, FieldDef
from .rust_extensions import (
    RustFuncDefExtension, RustClazzDefExtension, RustFieldDefExtension,
    RustProjectInfo, RustWorkspaceInfo, RustExtensionManager
)


class RustParser(Metric):

    def eva(self, ctx: EvaContext):
        # 使用rust_analyzer解析软件
        if not os.path.exists(pjoin(ctx.output_path, 'methods.jsonl')):
            self._run_rust_analyzer(ctx)
        
        # 读取函数调用图
        self._load_callgraph(ctx)
        logger.info(
            f'[RustParser] callgraph size: {len(ctx.callgraph.nodes)}({len(ctx.api_iter())}), {len(ctx.callgraph.edges)}')
        
        # 读取类调用图（结构体、枚举等类型）
        self._load_clazz_callgraph(ctx)
        logger.info(
            f'[RustParser] clazz callgraph size: {len(ctx.clazz_callgraph.nodes)}, {len(ctx.clazz_callgraph.edges)}')
        
        # 加载和处理文档信息
        self._load_documentation(ctx)
        logger.info('[RustParser] Documentation loaded and integrated')

    @classmethod
    def _run_rust_analyzer(cls, ctx: EvaContext):
        """运行rust_analyzer二进制文件生成分析结果"""
        # 查找rust_analyzer二进制文件
        rust_analyzer_path = cls._find_rust_analyzer()
        
        # 构建命令
        cmd = [
            rust_analyzer_path,
            '--source', ctx.resource_path,
            '--output', ctx.output_path,
            '--include-private',  # 包含私有函数
            '--skip-deps',        # 跳过外部依赖
            '--max-depth', '10'   # 调用链深度限制
        ]
        
        logger.info(f'[RustParser] Running rust_analyzer: {" ".join(cmd)}')
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            if result.stdout:
                logger.debug(f'[RustParser] rust_analyzer stdout: {result.stdout}')
            if result.stderr:
                logger.warning(f'[RustParser] rust_analyzer stderr: {result.stderr}')
        except subprocess.CalledProcessError as e:
            logger.error(f'[RustParser] rust_analyzer failed: {e}')
            logger.error(f'[RustParser] stdout: {e.stdout}')
            logger.error(f'[RustParser] stderr: {e.stderr}')
            raise RuntimeError(f"rust_analyzer execution failed: {e}")
        except FileNotFoundError:
            raise RuntimeError(f"rust_analyzer binary not found at: {rust_analyzer_path}")

    @classmethod
    def _find_rust_analyzer(cls) -> str:
        """查找rust_analyzer二进制文件"""
        # 首先尝试当前项目的rust_analyzer
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rust_analyzer_dir = pjoin(project_root, 'rust_analyzer')
        
        possible_paths = [
            pjoin(rust_analyzer_dir, 'target', 'release', 'rust_analyzer'),
            pjoin(rust_analyzer_dir, 'target', 'debug', 'rust_analyzer'),
            'rust_analyzer'  # 在PATH中查找
        ]
        
        for path in possible_paths:
            if path == 'rust_analyzer':
                # 检查PATH中的命令
                try:
                    subprocess.run([path, '--version'], capture_output=True, check=True)
                    return path
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
            else:
                # 检查本地文件
                if os.path.exists(path) and os.path.isfile(path):
                    return path
        
        raise FileNotFoundError("rust_analyzer binary not found")

    @classmethod
    def file_encoding(cls, path):
        """检测文件编码"""
        encodings = ['utf-8', 'gbk', 'utf-16', 'ISO-8859-1']
        for enc in encodings:
            try:
                with open(path, encoding=enc) as f:
                    content = f.read()
                return enc
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("无法识别编码")
    
    @classmethod
    def _load_callgraph(cls, ctx: EvaContext):
        """从methods.jsonl加载函数调用图"""
        callgraph = nx.DiGraph()
        
        methods_file = pjoin(ctx.output_path, 'methods.jsonl')
        if not os.path.exists(methods_file):
            logger.warning(f'[RustParser] methods.jsonl not found: {methods_file}')
            ctx.callgraph = callgraph
            return

        encoding = cls.file_encoding(methods_file)
        with open(methods_file, 'r', encoding=encoding) as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    content = json.loads(line.strip())
                except json.JSONDecodeError as e:
                    logger.warning(f'[RustParser] Failed to parse JSON line: {line.strip()[:100]}..., error: {e}')
                    continue
                
                name = content.get('name', '')
                signature = content.get('signature', '')
                raw_filename = content.get('filename', '')
                modifier = content.get('modifier', '')
                begin_line = content.get('beginLine', 0)
                end_line = content.get('endLine', 0)
                return_type = content.get('returnType', '')
                
                # 清理filename路径，移除resource/项目名前缀
                filename = cls._clean_filename_path(raw_filename, ctx.repo)
                
                # 判断可见性：PUBLIC修饰符的函数对外可见
                visible = 'PUBLIC' in modifier
                access = cls._get_access(modifier)
                
                # 读取源代码 (使用原始filename路径)
                code = cls._get_source_code(ctx.resource_path, raw_filename, begin_line, end_line)
                
                # 处理参数
                params = []
                for param in content.get('params', []):
                    param_name = param.get('name', '')
                    param_type = param.get('type', '')
                    params.append(FieldDef(name=param_name, signature=param_type))
                
                # 创建函数定义
                func_def = FuncDef(
                    name=name,
                    signature=signature,
                    params=params,
                    filename=filename,
                    code=code,
                    visible=visible,
                    access=access
                )
                
                callgraph.add_node(signature, attr=func_def)
                
                # 添加调用关系
                for callee in content.get('callees', []):
                    if callee and callee.strip():  # 确保callee不为空
                        callgraph.add_edge(signature, callee)

        ctx.callgraph = remove_cycle(callgraph)

    @classmethod
    def _load_clazz_callgraph(cls, ctx: EvaContext):
        """从structs.jsonl加载类型调用图"""
        clazz_callgraph = nx.DiGraph()
        
        structs_file = pjoin(ctx.output_path, 'structs.jsonl')
        if not os.path.exists(structs_file):
            logger.warning(f'[RustParser] structs.jsonl not found: {structs_file}')
            ctx.clazz_callgraph = clazz_callgraph
            return

        encoding = cls.file_encoding(structs_file)
        with open(structs_file, 'r', encoding=encoding) as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    content = json.loads(line.strip())
                except json.JSONDecodeError as e:
                    logger.warning(f'[RustParser] Failed to parse JSON line: {line.strip()[:100]}..., error: {e}')
                    continue
                
                name = content.get('name', '')
                fullname = content.get('fullname', '')
                raw_filename = content.get('filename', '')
                begin_line = content.get('beginLine', 0)
                
                # 清理filename路径，移除resource/项目名前缀
                filename = cls._clean_filename_path(raw_filename, ctx.repo)
                
                # 处理字段/属性
                fields = []
                for attr in content.get('attributes', []):
                    field_name = attr.get('name', '')
                    field_type = attr.get('type', '')
                    field_modifier = attr.get('modifier', '')
                    field_access = cls._get_access(field_modifier)
                    fields.append(FieldDef(name=field_name, signature=field_type, access=field_access))
                
                # 处理方法
                funcs = []
                for method_name in content.get('methods', []):
                    # 尝试从callgraph中找到对应的方法
                    func_def = ctx.func(method_name) if ctx.callgraph else None
                    if func_def:
                        funcs.append(func_def)
                
                # 生成类的代码表示
                code = cls._build_struct_code(name, fields, funcs)
                
                # 如果既没有字段也没有方法，跳过这个类型
                if len(fields) == 0 and len(funcs) == 0:
                    continue
                
                clazz_def = ClazzDef(
                    signature=fullname,
                    filename=filename,
                    code=code,
                    functions=funcs,
                    name=name,
                    fields=fields
                )
                
                clazz_callgraph.add_node(fullname, attr=clazz_def)
        
        # 分析类型之间的组合关系
        for node in list(clazz_callgraph.nodes()):
            node_def: ClazzDef = clazz_callgraph.nodes[node]['attr']
            for field in node_def.fields:
                # 如果字段类型是其他已知类型，添加依赖边
                field_type = cls._normalize_type(field.signature)
                if field_type in clazz_callgraph.nodes and field_type != node:
                    clazz_callgraph.add_edge(node, field_type)

        ctx.clazz_callgraph = remove_cycle(clazz_callgraph)

    @classmethod
    def _clean_filename_path(cls, raw_filename: str, repo_name: str) -> str:
        """清理文件名路径，移除不必要的前缀"""
        if not raw_filename:
            return raw_filename
        
        # 移除 resource/项目名/ 前缀
        resource_prefix = f"resource/{repo_name}/"
        if raw_filename.startswith(resource_prefix):
            return raw_filename[len(resource_prefix):]
        
        # 移除单独的 resource/ 前缀
        if raw_filename.startswith("resource/"):
            return raw_filename[len("resource/"):]
        
        return raw_filename

    @classmethod
    def _get_source_code(cls, resource_path: str, filename: str, begin_line: int, end_line: int) -> str:
        """读取指定行范围的源代码"""
        if not filename:
            return ""
        
        # 首先尝试直接路径
        file_path = pjoin(resource_path, filename)
        if os.path.exists(file_path):
            return cls._read_file_lines(file_path, begin_line, end_line)
        
        # 如果直接路径不存在，在resource_path下递归搜索文件
        # 提取文件名用于搜索
        base_filename = os.path.basename(filename)
        for root, _, files in os.walk(resource_path):
            if base_filename in files:
                file_path = pjoin(root, base_filename)
                return cls._read_file_lines(file_path, begin_line, end_line)
        
        logger.warning(f'[RustParser] Source file not found: {filename} in {resource_path}')
        return ""
    
    @classmethod
    def _read_file_lines(cls, file_path: str, begin_line: int, end_line: int) -> str:
        """读取文件指定行范围的内容"""
        try:
            encoding = cls.file_encoding(file_path)
            with open(file_path, 'r', encoding=encoding) as f:
                lines = f.readlines()
                if begin_line > 0 and end_line >= begin_line and end_line <= len(lines):
                    return ''.join(lines[begin_line - 1:end_line])
                else:
                    return ""
        except Exception as e:
            logger.warning(f'[RustParser] Failed to read source code from {file_path}: {e}')
            return ""

    @classmethod
    def _build_struct_code(cls, name: str, fields: List[FieldDef], functions: List[FuncDef]) -> str:
        """构建Rust结构体的代码表示"""
        code = f'struct {name} {{\n'
        
        # 添加字段
        for field in fields:
            visibility = 'pub ' if field.access == 'public' else ''
            code += f'    {visibility}{field.name}: {field.signature},\n'
        
        code += '}\n\n'
        
        # 添加impl块（如果有方法）
        if functions:
            code += f'impl {name} {{\n'
            for func in functions:
                # 简化的方法签名
                func_name = func.name
                params = ', '.join([p.name + ': ' + p.signature for p in func.params])
                code += f'    fn {func_name}({params}) {{ ... }}\n'
            code += '}\n'
        
        return code

    @classmethod
    def _get_access(cls, modifier: str) -> str:
        """从修饰符字符串中提取访问权限"""
        if 'PRIVATE' in modifier:
            return 'private'
        elif 'PUBLIC' in modifier:
            return 'public'
        else:
            return 'private'  # Rust默认是私有的

    @classmethod
    def _normalize_type(cls, type_str: str) -> str:
        """标准化类型字符串，去除泛型参数等"""
        if not type_str:
            return ""
        
        # 去除常见的包装类型和修饰符
        type_str = type_str.replace('&', '').replace('mut ', '').strip()
        
        # 去除泛型参数
        if '<' in type_str:
            type_str = type_str.split('<')[0]
        
        # 去除Option、Vec等包装
        if type_str.startswith(('Option<', 'Vec<', 'Result<')):
            return ""
        
        return type_str

    @classmethod
    def _load_documentation(cls, ctx: EvaContext):
        """从documentation.json加载并整合文档信息"""
        doc_file = pjoin(ctx.output_path, 'documentation.json')
        if not os.path.exists(doc_file):
            logger.warning(f'[RustParser] documentation.json not found: {doc_file}')
            return

        try:
            encoding = cls.file_encoding(doc_file)
            with open(doc_file, 'r', encoding=encoding) as f:
                doc_data = json.load(f)
            
            # 整合函数文档信息
            cls._integrate_function_docs(ctx, doc_data.get('functions', []))
            
            # 整合类型文档信息
            cls._integrate_type_docs(ctx, doc_data.get('types', []))
            
            # 存储项目级别信息到context中
            cls._store_project_info(ctx, doc_data.get('project', {}))
            
            logger.info('[RustParser] Successfully integrated documentation data')
            
        except Exception as e:
            logger.warning(f'[RustParser] Failed to load documentation: {e}')

    @classmethod
    def _integrate_function_docs(cls, ctx: EvaContext, function_docs: List):
        """整合函数文档信息到现有的FuncDef中"""
        doc_map = {func['signature']: func for func in function_docs}
        
        # 更新callgraph中的函数定义
        for signature in ctx.callgraph.nodes():
            func_def: FuncDef = ctx.callgraph.nodes[signature]['attr']
            if signature in doc_map:
                doc = doc_map[signature]
                
                # 创建Rust扩展信息
                rust_ext = RustFuncDefExtension(
                    doc_comment=doc.get('doc_comment'),
                    examples=doc.get('examples', []),
                    errors=doc.get('errors', []),
                    return_doc=doc.get('return_doc'),
                    # 从modifier中提取Rust特性
                    is_async='ASYNC' in func_def.access,
                    is_unsafe='UNSAFE' in func_def.access,
                    is_const='CONST' in func_def.access,
                    is_pub=func_def.visible
                )
                
                # 附加扩展信息
                RustExtensionManager.attach_func_extension(func_def, rust_ext)
                
                # 更新参数信息
                param_docs = {p['name']: p for p in doc.get('parameters', [])}
                for param in func_def.params:
                    if param.name in param_docs:
                        param_ext = RustFieldDefExtension(
                            description=param_docs[param.name].get('description')
                        )
                        RustExtensionManager.attach_field_extension(param, param_ext)

    @classmethod
    def _integrate_type_docs(cls, ctx: EvaContext, type_docs: List):
        """整合类型文档信息到现有的ClazzDef中"""
        doc_map = {t['fullname']: t for t in type_docs}
        
        # 更新clazz_callgraph中的类型定义
        for signature in ctx.clazz_callgraph.nodes():
            clazz_def: ClazzDef = ctx.clazz_callgraph.nodes[signature]['attr']
            if signature in doc_map:
                doc = doc_map[signature]
                
                # 创建Rust类型扩展信息
                rust_ext = RustClazzDefExtension(
                    doc_comment=doc.get('doc_comment'),
                    examples=doc.get('examples', []),
                    type_kind=doc.get('type_kind', 'struct'),
                    implemented_traits=doc.get('implemented_traits', [])
                )
                
                # 附加扩展信息
                RustExtensionManager.attach_clazz_extension(clazz_def, rust_ext)
                
                # 更新字段信息
                field_docs = {f['name']: f for f in doc.get('fields', [])}
                for field in clazz_def.fields:
                    if field.name in field_docs:
                        field_ext = RustFieldDefExtension(
                            description=field_docs[field.name].get('description'),
                            visibility=field_docs[field.name].get('visibility', 'private')
                        )
                        RustExtensionManager.attach_field_extension(field, field_ext)

    @classmethod
    def _store_project_info(cls, ctx: EvaContext, project_info: dict):
        """存储项目级别信息到context中"""
        # 创建结构化的Rust项目信息
        rust_project = RustProjectInfo(
            name=project_info.get('name', ''),
            version=project_info.get('version', ''),
            description=project_info.get('description'),
            authors=project_info.get('authors', []),
            license=project_info.get('license'),
            repository=project_info.get('repository'),
            readme=project_info.get('readme')
        )
        ctx.rust_project_info = rust_project
        
        # 存储workspace信息
        workspace_info = project_info.get('workspace_info')
        if workspace_info:
            rust_workspace = RustWorkspaceInfo(
                members=workspace_info.get('members', []),
                member_docs=workspace_info.get('member_docs', {})
            )
            ctx.rust_workspace_info = rust_workspace