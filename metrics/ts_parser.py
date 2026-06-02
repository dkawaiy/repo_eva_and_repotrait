import sys
import os
import json
import logging
import subprocess
import networkx as nx
from typing import List, Optional, Set
from abc import ABCMeta
import re

from .metric import ClazzDef
from .metric import Metric, EvaContext, FuncDef, FieldDef
from utils import LangEnum

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='[TSAnalyzer] %(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)


class TSAnalyzer(Metric):
    """TypeScript 代码解析器（适配 Docker 容器环境）"""

    def __init__(self):
        super().__init__()
        self.node_path = None  # 存储检测到的 Node.js 绝对路径

    def eva(self, ctx: EvaContext):
        """核心入口：解析 TS 代码并构建建调用图"""
        target_path = ctx.resource_path  # 使用上下文中的资源路径
        logger.info(f"开始分析 TypeScript 代码（资源路径：{target_path}）")
        
        # 生成解析结果文件
        self._generate_ts_analysis_files(ctx, target_path, force=True)
        
        # 加载函数调用图
        self._load_callgraph(ctx, target_path)
        logger.info(
            f'函数调用图统计：节点数={len(ctx.callgraph.nodes)}，边数={len(ctx.callgraph.edges)}'
        )
        
        # 加载类调用图
        self._load_clazz_callgraph(ctx, target_path)
        logger.info(
            f'类调用图统计：节点数={len(ctx.clazz_callgraph.nodes)}，边数={len(ctx.clazz_callgraph.edges)}'
        )

    def _generate_ts_analysis_files(self, ctx: EvaContext, target_path: str, force: bool = False):
        """调用 Node.js 脚本解析 TS 代码"""
        methods_output = os.path.join(ctx.output_path, 'methods.jsonl')
        structs_output = os.path.join(ctx.output_path, 'structs.jsonl')
        
        if not force and os.path.exists(methods_output) and os.path.exists(structs_output):
            logger.info(f'解析结果已存在，跳过解析：{ctx.output_path}')
            return
        
        if force:
            for file in [methods_output, structs_output]:
                if os.path.exists(file):
                    os.remove(file)
                    logger.info(f'删除旧解析结果：{file}')
        
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"待解析的路径不存在：{target_path}")
        logger.info(f"确认解析目标路径存在：{target_path}")
        
        if not self._check_node_env():
            raise EnvironmentError(
                "未检测到 Node.js 环境！请在容器内执行安装命令"
            )
        
        ts_analyzer_script = os.path.join(os.path.dirname(__file__), 'ts_analyzer.js')
        if not os.path.exists(ts_analyzer_script):
            raise FileNotFoundError(f"TS 分析脚本不存在：{ts_analyzer_script}")
        logger.info(f'使用分析脚本：{ts_analyzer_script}')
        
        self._install_ts_dependency(os.path.dirname(ts_analyzer_script))
        
        cmd = [
            self.node_path,
            ts_analyzer_script,
            os.path.abspath(target_path),
            os.path.abspath(ctx.output_path)
        ]
        logger.info(f'执行解析命令：{" ".join(cmd)}')
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(ts_analyzer_script)
            )
            if result.stderr:
                logger.warning(f'Node.js 脚本警告：{result.stderr}')
            logger.info('TS 代码解析完成')
        except subprocess.CalledProcessError as e:
            logger.error(f'Node.js 执行失败（返回码 {e.returncode}）：{e.stderr}')
            raise RuntimeError(f'解析脚本执行失败：{e.stderr}') from e

    def _load_callgraph(self, ctx: EvaContext, target_path: str):
        """加载函数调用图（确保公开函数被标记为可见）"""
        ctx.callgraph = nx.DiGraph()
        methods_path = os.path.join(ctx.output_path, 'methods.jsonl')
        
        if not os.path.exists(methods_path):
            raise FileNotFoundError(f"函数信息文件不存在：{methods_path}")
        
        visible_sets = self._get_visible_functions(ctx, target_path)
        encoding = self._file_encoding(methods_path)
        
        with open(methods_path, 'r', encoding=encoding) as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.strip()
                    if not line:
                        continue
                    func_data = json.loads(line)
                    
                    # 验证必填字段
                    required = ['signature', 'name', 'code', 'filename', 'modifier', 'params', 'callees']
                    missing = [k for k in required if k not in func_data]
                    if missing:
                        logger.warning(f'跳过无效函数数据（行{line_num}）：缺少字段 {missing}')
                        continue
                    
                    # 检查文件存在性
                    func_full_path = os.path.abspath(os.path.join(target_path, func_data['filename']))
                    if not os.path.exists(func_full_path):
                        logger.warning(f'文件不存在，跳过函数（行{line_num}）：{func_full_path}')
                        continue
                    
                    # 转换参数为 FieldDef
                    func_params = [
                        FieldDef(name=p['name'], signature=p['type'], access=p.get('modifier', 'public'))
                        for p in func_data['params']
                    ]
                    
                    # 可见性判定：强制 public 函数为可见（确保被纳入 API）
                    func_modifier = func_data['modifier']
                    is_visible = (func_modifier == 'public') and (
                        func_data['name'] in visible_sets or True  # 兜底逻辑
                    )
                    
                    # 创建 FuncDef 并添加到图
                    func_def = FuncDef(
                        signature=func_data['signature'],
                        name=func_data['name'],
                        code=func_data['code'][:5000],
                        filename=func_data['filename'],
                        visible=is_visible,
                        access=func_modifier,
                        params=func_params
                    )
                    ctx.callgraph.add_node(func_def.signature, attr=func_def)
                    
                except json.JSONDecodeError as e:
                    logger.warning(f'无效 JSON（行{line_num}）：{e}')
                    continue
        
        # 构建函数调用边
        self._build_callgraph_edges(ctx)

    def _load_clazz_callgraph(self, ctx: EvaContext, target_path: str):
        """加载类调用图（修复 visible_sets 未定义问题）"""
        ctx.clazz_callgraph = nx.DiGraph()
        structs_path = os.path.join(ctx.output_path, 'structs.jsonl')
        
        if not os.path.exists(structs_path):
            raise FileNotFoundError(f"类信息文件不存在：{structs_path}")
        
        # 新增：获取可见函数集合（用于可见性判定）
        visible_sets = self._get_visible_functions(ctx, target_path)
        
        encoding = self._file_encoding(structs_path)
        with open(structs_path, 'r', encoding=encoding) as f:
            for line_num, line in enumerate(f, 1):
                try:
                    line = line.strip()
                    if not line:
                        continue
                    clazz_data = json.loads(line)
                    
                    # 验证必填字段
                    required = ['signature', 'name', 'code', 'filename', 'modifier', 'attributes', 'methods']
                    missing = [k for k in required if k not in clazz_data]
                    if missing:
                        logger.warning(f'跳过无效类数据（行{line_num}）：缺少字段 {missing}')
                        continue
                    
                    # 转换属性为 FieldDef
                    clazz_fields = [
                        FieldDef(name=f['name'], signature=f['type'], access=f.get('modifier', 'public'))
                        for f in clazz_data['attributes']
                    ]
                    
                    # 关联类的方法
                    clazz_functions = [
                        ctx.func(sig) for sig in clazz_data['methods']
                        if ctx.func(sig) is not None
                    ]
                    
                    # 补充无直接关联的方法
                    if not clazz_functions:
                        all_funcs = [ctx.callgraph.nodes[sig]['attr'] for sig in ctx.callgraph.nodes]
                        matched_funcs = []
                        for func in all_funcs:
                            if len(matched_funcs) >= 5:
                                break
                            for param in func.params:
                                if self._trim_type(param.signature) == clazz_data['name']:
                                    matched_funcs.append(func)
                                    break
                        clazz_functions = matched_funcs
                    
                    # 过滤空类
                    if not clazz_fields and not clazz_functions:
                        logger.debug(f'跳过空类：{clazz_data["signature"]}')
                        continue
                    
                    # 可见性判定：强制 public 类为可见（确保被纳入 API）
                    clazz_modifier = clazz_data['modifier']
                    is_visible = (clazz_modifier == 'public') and (
                        clazz_data['name'] in visible_sets or True  # 现在 visible_sets 已定义
                    )
                    
                    # 创建 ClazzDef 并添加到图
                    clazz_def = ClazzDef(
                        signature=clazz_data['signature'],
                        name=clazz_data['name'],
                        code=clazz_data['code'],
                        fields=clazz_fields,
                        functions=clazz_functions,
                        filename=clazz_data['filename'],
                        visible=is_visible
                    )
                    ctx.clazz_callgraph.add_node(clazz_def.signature, attr=clazz_def)
                    
                except json.JSONDecodeError as e:
                    logger.warning(f'无效 JSON（行{line_num}）：{e}')
                    continue
        
        # 构建类间关系边并移除循环
        self._build_clazz_edges(ctx)
        self._remove_cycle(ctx.clazz_callgraph)

    def _get_visible_functions(self, ctx: EvaContext, target_path: str) -> Set[str]:
        """提取可见函数（ctags 失败时手动提取）"""
        tags_path = os.path.join(ctx.output_path, 'tags')
        visible_funcs = set()
        
        # 生成 tags 文件
        if os.path.exists(tags_path):
            os.remove(tags_path)
            logger.info(f'删除旧 tags 文件：{tags_path}')
        
        logger.info(f'生成 tags 文件：{tags_path}')
        try:
            cmd = (
                f'ctags -R --languages=TypeScript --ts-kinds=f '
                f'-f {tags_path} {os.path.abspath(target_path)}'
            )
            subprocess.run(
                cmd,
                shell=True,
                check=True,
                capture_output=True,
                text=True
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f'ctags 执行失败：{e.stderr}，使用兜底逻辑')
        
        # 解析 tags 文件或手动提取
        try:
            if os.path.exists(tags_path):
                encoding = self._file_encoding(tags_path)
                with open(tags_path, 'r', encoding=encoding) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('!'):
                            continue
                        parts = line.split('\t')
                        if len(parts) >= 4 and parts[3].startswith('f'):
                            func_name = parts[0]
                            if parts[1].endswith(('.ts', '.d.ts')):
                                visible_funcs.add(func_name)
            
            # 兜底：如果 tags 为空，手动扫描文件提取函数名
            if not visible_funcs:
                logger.info('tags 提取为空，手动提取函数名')
                for root, _, files in os.walk(target_path):
                    for file in files:
                        if file.endswith('.ts'):
                            file_path = os.path.join(root, file)
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                # 匹配函数定义
                                func_matches = re.findall(r'function\s+(\w+)\s*\(', content)
                                for func_name in func_matches:
                                    visible_funcs.add(func_name)
                                # 匹配类方法
                                class_matches = re.findall(r'class\s+\w+\s*{\s*.*?(\w+)\s*\(', content, re.DOTALL)
                                for func_name in class_matches:
                                    visible_funcs.add(func_name)
                            except Exception as e:
                                logger.warning(f'手动提取失败 {file_path}：{e}')
        except Exception as e:
            logger.warning(f'解析 tags 失败：{e}')
        
        return visible_funcs

    def _check_node_env(self) -> bool:
        """检测 Node.js 环境"""
        node_paths = [
            '/usr/bin/node', '/usr/local/bin/node', '/bin/node', 'node'
        ]
        
        for path in node_paths:
            try:
                subprocess.run(
                    [path, '-v'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True
                )
                self.node_path = path
                logger.info(f'检测到 Node.js 路径：{self.node_path}')
                return True
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        
        logger.error("未找到 Node.js 环境，请先安装")
        return False

    def _install_ts_dependency(self, script_dir: str):
        """安装 TS 依赖"""
        deps = ['typescript', 'iconv-lite', 'ts-morph']
        node_modules = os.path.join(script_dir, 'node_modules')
        
        all_installed = all(
            os.path.exists(os.path.join(node_modules, dep))
            for dep in deps
        )
        if all_installed:
            logger.info('TS 依赖已安装，跳过')
            return
        
        logger.info(f'安装依赖到：{script_dir}')
        try:
            if not os.path.exists(os.path.join(script_dir, 'package.json')):
                # 创建package.json
                subprocess.run(
                    ['npm', 'init', '-y'],
                    cwd=script_dir,
                    capture_output=True,
                    text=True
                )
                
                # 修改package.json添加type: module字段
                package_json_path = os.path.join(script_dir, 'package.json')
                with open(package_json_path, 'r', encoding='utf-8') as f:
                    package_json = json.load(f)
                
                package_json['type'] = 'module'
                
                with open(package_json_path, 'w', encoding='utf-8') as f:
                    json.dump(package_json, f, indent=2)
            
            subprocess.run(
                ['npm', 'install', '--unsafe-perm'] + deps,
                cwd=script_dir,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info('TS 依赖安装完成')
        except subprocess.CalledProcessError as e:
            logger.error(f'依赖安装失败：{e.stderr}')
            raise RuntimeError(f'无法安装依赖：{e.stderr}') from e

    def _file_encoding(self, path: str) -> str:
        """检测文件编码"""
        encodings = ['utf-8', 'gbk', 'utf-16', 'ISO-8859-1']
        for enc in encodings:
            try:
                with open(path, 'r', encoding=enc) as f:
                    f.read(1024)
                return enc
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError(f"无法识别文件编码：{path}")

    def _trim_type(self, type_str: str) -> str:
        """清理类型字符串"""
        if not type_str:
            return ''
        cleaned = re.sub(r'\[\s*\]', '', type_str)
        while re.search(r'<[^<>]*>', cleaned):
            cleaned = re.sub(r'<[^<>]*>', '', cleaned)
        cleaned = cleaned.split(r'[|&]')[0].strip()
        basic_types = {'string', 'number', 'boolean', 'any', 'void', 'null', 
                      'undefined', 'object', 'symbol', 'bigint', 'unknown'}
        match = re.search(r'([A-Z][a-zA-Z0-9_]*)', cleaned)
        if match:
            class_name = match.group(1)
            return class_name if class_name.lower() not in basic_types else ''
        return ''

    def _build_callgraph_edges(self, ctx: EvaContext):
        """构建函数调用边"""
        methods_path = os.path.join(ctx.output_path, 'methods.jsonl')
        encoding = self._file_encoding(methods_path)
        with open(methods_path, 'r', encoding=encoding) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    func_data = json.loads(line)
                    caller = func_data['signature']
                    if caller not in ctx.callgraph.nodes:
                        continue
                    for callee_sig in func_data['callees']:
                        if callee_sig in ctx.callgraph.nodes:
                            ctx.callgraph.add_edge(caller, callee_sig)
                except json.JSONDecodeError:
                    continue

    def _build_clazz_edges(self, ctx: EvaContext):
        """构建类间关系边"""
        for clazz_sig in ctx.clazz_callgraph.nodes:
            clazz_def = ctx.clazz_callgraph.nodes[clazz_sig]['attr']
            for field in clazz_def.fields:
                pure_type = self._trim_type(field.signature)
                if not pure_type:
                    continue
                target_sig = next(
                    (sig for sig, data in ctx.clazz_callgraph.nodes.data('attr')
                     if data.name == pure_type),
                    None
                )
                if target_sig and target_sig != clazz_sig:
                    ctx.clazz_callgraph.add_edge(clazz_sig, target_sig)

    def _remove_cycle(self, graph: nx.DiGraph):
        """移除图中循环依赖"""
        try:
            for cycle in list(nx.simple_cycles(graph)):
                if len(cycle) >= 2:
                    u, v = cycle[-2], cycle[-1]
                    if graph.has_edge(u, v):
                        graph.remove_edge(u, v)
                        logger.debug(f'移除循环边：{u} -> {v}')
        except Exception as e:
            logger.warning(f'移除循环依赖失败：{e}')