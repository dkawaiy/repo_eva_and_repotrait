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
class arktsParser(Metric):

    @classmethod
    def _load_callgraph(cls, ctx: EvaContext):
        #visible_sets = cls._get_visible_functions(ctx)
        callgraph = nx.DiGraph()

        with open(pjoin(ctx.output_path, 'methods.jsonl'), 'r') as f:
            for line in f:
                content = json.loads(line.strip())
                name = content['name']
                #visible = name in visible_sets and 'STATIC' not in content['modifier']
                signature = content['signature']
                filename = content['filename']
                #access = cls._get_access(content['modifier'])
                beginLine = content['beginLine']
                #endLine = content['endLine']
                code = content['code']
                params = list(map(lambda x: FieldDef(name=x['name'], signature=x['type']), content['params']))
                callgraph.add_node(signature,
                                   attr=FuncDef(name=name, signature=signature, params=params, filename=filename,
                                                code=code, visible=True, access=True))
        with open(pjoin(ctx.output_path, 'methods.jsonl'), 'r') as f:        
            for line in f:
                content = json.loads(line.strip())
                if content['outermethod'] is None and content['outermethods'] in callgraph:
                    callgraph.add_edge(content['outermethod'], signature)
                for t in content['callees']:
                    if signature in callgraph and t in callgraph:
                        callgraph.add_edge(signature, t)
        ctx.callgraph = remove_cycle(callgraph)

    @classmethod
    def _load_clazz_callgraph(cls, ctx: EvaContext):
        clazz_callgraph = nx.DiGraph()
        #inherit_pairs = []
        sigs = []
        with open(pjoin(ctx.output_path, 'classes.jsonl'), 'r') as f:
            nameSets = set()
            for line in f:
                content = json.loads(line.strip())
                name = content['name']
                nameSets.add(name)
                signature = content['fullname']
                #inherit_pairs.append([signature,content['inheritsFromTypeFullName']])
                sigs.append(signature)
                filename = content['filename']
                fields = list(
                    map(lambda x: FieldDef(name=x['name'], signature=x['type'], access=x['access']),
                        content['attributes']))
                funcs = list(map(lambda n: ctx.func(n["signature"]), content['methods']))
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
            # 组合关系
            for node in list(clazz_callgraph.nodes()):
                node: ClazzDef = clazz_callgraph.nodes[node]['attr']
                for f in node.fields:
                    # 如果属性的类型是其他类，则添加边
                    if cls._trim_type(f.signature) in sigs and f.signature in clazz_callgraph.nodes:
                        clazz_callgraph.add_edge(node.signature, f.signature)
            '''
            for pair in inherit_pairs:
                for sig in pair[1]:
                    if sig in sigs:
                        clazz_callgraph.add_edge(pair[0],sig)'''

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
        # 解析软件
        if not os.path.exists(pjoin(ctx.output_path, 'methods.jsonl')):
            if sys.platform.startswith("win"):
                subprocess.run(
                    f'npx ts-node /arkanalyzer/arkts_test.ts "{ctx.resource_path}" "{ctx.output_path}"',
                    shell=True
                )#在windows运行这个地方要改成对应的目录
            else:
                print(ctx.resource_path,ctx.output_path)
                src = "/root/metrics/arkts_test.ts"
                dst = "/arkanalyzer/arkts_test.ts"
                # 每次运行前覆盖
                shutil.copyfile(src, dst)
                subprocess.run([
                    'npx', 'ts-node', '/arkanalyzer/arkts_test.ts',
                    pjoin('/root',ctx.resource_path),
                    pjoin('/root',ctx.output_path)
                ], cwd='/arkanalyzer')
        # 读取函数调用图
        self._load_callgraph(ctx)
        logger.info(
            f'[CParser] callgraph size: {len(ctx.callgraph.nodes)}({len(ctx.api_iter())}), {len(ctx.callgraph.edges)}')
        # 读取类调用图
        self._load_clazz_callgraph(ctx)
        logger.info(
            f'[CParser] clazz callgraph size: {len(ctx.clazz_callgraph.nodes)}, {len(ctx.clazz_callgraph.edges)}')

