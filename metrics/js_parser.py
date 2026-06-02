import json
import os
import shutil
import subprocess
import tempfile
from utils import remove_cycle
import networkx as nx
import pydot
from os.path import join as pjoin
from .metric import Metric, FuncDef, FieldDef, EvaContext, ClazzDef
from typing import Set, List
import re
from loguru import logger
import sys
# @Author: 段庸
class JSlangParser(Metric):

    @classmethod
    def _load_callgraph(cls, ctx: EvaContext):
        #visible_sets = cls._get_visible_functions(ctx)
        callgraph = nx.DiGraph()
        methods_path = pjoin(ctx.output_path, 'methods.jsonl')
        if not os.path.isfile(methods_path):
            logger.warning(f"[JSlangParser] methods.jsonl not found: {methods_path}")
            ctx.callgraph = callgraph
            return

        # first pass: create nodes (with safe file reading)
        with open(methods_path, 'r') as f:
            for line in f:
                try:
                    content = json.loads(line.strip())
                except Exception as ex:
                    logger.exception(f"[JSlangParser] failed to parse methods.jsonl line: {ex}")
                    continue
                name = content.get('name')
                #visible = name in visible_sets and 'STATIC' not in content['modifier']
                signature = content.get('signature')
                filename = content.get('filename')
                access = cls._get_access(content.get('modifier', ''))
                beginLine = content.get('beginLine', 1)
                endLine = content.get('endLine', beginLine)

                params = list(map(lambda x: FieldDef(name=x['name'], signature=x['type']), content.get('params', [])))

                code = ''
                # skip placeholder-like filenames such as "<includes>"
                is_placeholder = not filename or (isinstance(filename, str) and ('<' in filename or '>' in filename or filename.startswith('<')))
                if is_placeholder:
                    logger.warning(f"[JSlangParser] placeholder or invalid filename: {filename} (signature={signature})")
                else:
                    file_path = pjoin(ctx.resource_path, filename)
                    if not os.path.isfile(file_path):
                        logger.warning(f"[JSlangParser] file not found: {file_path} (signature={signature})")
                    else:
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f2:
                                lines = f2.readlines()
                                try:
                                    b = max(1, int(beginLine))
                                    e = min(len(lines), int(endLine))
                                except Exception:
                                    b, e = 1, len(lines)
                                if b <= e:
                                    code = ''.join(lines[b - 1: e])
                        except Exception as ex:
                            logger.exception(f"[JSlangParser] error reading file {file_path}: {ex}")

                # filter out invalid/empty nodes to avoid edges pointing to empty nodes
                if not signature:
                    logger.warning(f"[JSlangParser] missing signature, skip node (filename={filename})")
                    continue
                # 如果是占位符文件且没有读取到代码，则跳过该节点
                if is_placeholder and not code:
                    logger.info(f"[JSlangParser] skipping node with placeholder filename and empty code: signature={signature}")
                    continue

                callgraph.add_node(signature,
                                   attr=FuncDef(name=name, signature=signature, params=params, filename=filename,
                                                code=code, visible=True, access=access))

        # second pass: add edges
        with open(methods_path, 'r') as f:
            for line in f:
                try:
                    content = json.loads(line.strip())
                except Exception as ex:
                    logger.exception(f"[JSlangParser] failed to parse methods.jsonl line for edges: {ex}")
                    continue
                signature = content.get('signature')
                for t in content.get('callees', []):
                    if signature in callgraph and t in callgraph:
                        callgraph.add_edge(signature, t)

        ctx.callgraph = remove_cycle(callgraph)

    @classmethod
    def _load_clazz_callgraph(cls, ctx: EvaContext):
        clazz_callgraph = nx.DiGraph()
        inherit_pairs = []
        sigs = []
        with open(pjoin(ctx.output_path, 'typedefs.jsonl'), 'r') as f:
            nameSets = set()
            for line in f:
                content = json.loads(line.strip())
                name = content['name']
                nameSets.add(name)
                signature = content['fullname']
                inherit_pairs.append([signature,content['inheritsFromTypeFullName']])
                filename = content['filename']
                fields = list(
                    map(lambda x: FieldDef(name=x['name'], signature=x['type'], access=cls._get_access(x['modifier'])),
                        content['attributes']))
                funcs = list(map(lambda n: ctx.func(n), content['methods']))
                code = cls._build_class_code(signature, fields, funcs)
                # 如果没有相关函数，则尝试为其绑定函数
                if len(funcs) == 0:
                    for node in ctx.callgraph.nodes():
                        node: FuncDef = ctx.callgraph.nodes[node]['attr']
                        if any(map(lambda x: cls._trim_type(x.signature) == name, node.params)):
                            funcs.append(node)
                    funcs = funcs[:5]
                # 如果没有函数和属性，则忽略这个类
                if len(funcs) == 0 and len(fields) == 0:
                    continue
                clazz_callgraph.add_node(signature,
                                         attr=ClazzDef(signature=signature, filename=filename, code=code,
                                                       functions=funcs, name=name,
                                                       fields=fields))
                sigs.append(signature)
        # 组合关系
        for node in list(clazz_callgraph.nodes()):
            node: ClazzDef = clazz_callgraph.nodes[node]['attr']
            for f in node.fields:
                # 如果属性的类型是其他类，则添加边
                if cls._trim_type(f.signature) in nameSets and f.signature in clazz_callgraph.nodes:
                    clazz_callgraph.add_edge(node.signature, f.signature)
        for pair in inherit_pairs:
            for sig in pair[1]:
                if sig in sigs and pair[0] in sigs:
                    clazz_callgraph.add_edge(pair[0],sig)

        ctx.clazz_callgraph = remove_cycle(clazz_callgraph)

    @classmethod
    def _build_class_code(cls, signature: str, fields: List[FieldDef], functions: List[FuncDef]) -> str:
        code = 'class ' + signature + ' {\n'
        for access in ['public', 'protected', 'private']:
            filter_fields = list(filter(lambda x: x.access == access, fields))
            filter_functions = list(filter(lambda x: x.access == access, functions))
            if len(filter_fields) == 0 and len(filter_functions) == 0:
                return ''
            s = f'{access}:\n'
            for f in filter_fields:
                s += f'  {f.signature} {f.name};\n'
            if len(filter_fields) > 0:
                s += '\n'
            for f in filter_functions:
                s += f'  {f.signature};\n'
            code += s
        code += '};'
        return code

    # 去除类型中的修饰符
    @classmethod
    def _trim_type(cls, t: str) -> str:
        return re.sub(r'[*&]|(\[\d*])|const|volatile|restrict', '', t).strip()

    @classmethod
    def _get_access(cls, modifier: str) -> str:
        if 'PRIVATE' in modifier:
            return 'private'
        elif 'PROTECTED' in modifier:
            return 'protected'
        return 'public'
    

    def eva(self, ctx: EvaContext):
        # joern 解析软件
        if not os.path.exists(pjoin(ctx.output_path, 'methods.jsonl')):
            if sys.platform.startswith("win"):
                subprocess.run(
                    f'joern.bat --script {pjoin('metrics', 'js_query.sc')} --param """output={ctx.output_path}""" --param """path={ctx.resource_path}"""',
                    shell=True
                )#
                print('flag',1)
            else:                           
                subprocess.run(['joern', '--script', pjoin('metrics', 'js_query.sc'), '--param', f'output={ctx.output_path}',
                                '--param', f'path={ctx.resource_path}'])
        # 读取函数调用图
        self._load_callgraph(ctx)
        logger.info(
            f'[CParser] callgraph size: {len(ctx.callgraph.nodes)}({len(ctx.api_iter())}), {len(ctx.callgraph.edges)}')
        # 读取类调用图
        self._load_clazz_callgraph(ctx)
        logger.info(
            f'[CParser] clazz callgraph size: {len(ctx.clazz_callgraph.nodes)}, {len(ctx.clazz_callgraph.edges)}')
    



