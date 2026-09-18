import os
import threading
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple, TypeVar, Optional, Type

import networkx as nx
from loguru import logger

from utils import LangEnum
from .doc import ApiDoc, ClazzDoc, ModuleDoc, Doc, RepoDoc


# 变量定义
@dataclass
class FieldDef:
    name: str  # 变量名称
    signature: str  # 变量类型
    access: str = 'public'  # 访问权限，public/protected/private，作为函数参数时没有意义


# 函数定义
@dataclass
class FuncDef:
    signature: str  # 函数签名
    name: str  # 函数名称
    code: str  # 源代码
    filename: str  # 源代码文件名称
    visible: bool = True  # 可见性，指是否对三方软件可见
    access: str = 'public'  # 访问权限，public/protected/private
    params: List[FieldDef] = field(default_factory=list)  # 函数参数，默认为空

    # return_type: FieldDef = None # 函数返回值类型，默认为空

    def __hash__(self):
        return hash(self.signature)

    def __eq__(self, other):
        if not isinstance(other, FuncDef):
            return False
        return self.signature == other.signature


# 类定义
@dataclass
class ClazzDef:
    signature: str  # 类签名
    name: str  # 类名称
    code: str  # 源代码
    fields: List[FieldDef]  # 类属性
    functions: List[FuncDef]  # 类方法
    filename: str  # 源代码文件名称
    visible: bool = True  # 可见性，指是否对三方软件可见

    def __hash__(self):
        return hash(self.signature)

    def __eq__(self, other):
        if not isinstance(other, ClazzDef):
            return False
        return self.signature == other.signature


T = TypeVar('T', bound=Doc)


@dataclass
class EvaContext:
    repo: str  # 分析的软件名称
    doc_path: str  # 文档存储路径
    resource_path: str  # 源代码路径
    output_path: str  # 中间产物存储路径
    lang: LangEnum  # 语言类型

    def __init__(self, repo: str, lang: LangEnum, doc_path: str, resource_path: str, output_path: str):
        self.repo = repo
        self.doc_path = doc_path
        os.makedirs(self.doc_path, exist_ok=True)
        self.resource_path = resource_path
        os.makedirs(self.resource_path, exist_ok=True)
        self.output_path = output_path
        os.makedirs(self.output_path, exist_ok=True)
        self.lang = lang
        # 任务内的阶段问题记录：各度量环节将技术性失败写入此处，最终汇总到批量任务结果（避免只留日志）
        self.errors: List[Tuple[str, str]] = []
        # 任务内的文档索引缓存：signature/name -> Doc，避免按签名查询时反复读取解析整份 Markdown
        self._doc_index: Dict[Tuple[str, type], Dict[str, Any]] = {}
        self._doc_index_lock = threading.Lock()

    # 软件的函数调用图，用法：
    # - ctx.func_iter(), callgraph.nodes(data=True) 遍历软件内所有函数
    # - ctx.func('ex'), callgraph.nodes['ex']['attr'] 查找函数ex的FuncDef
    # - callgraph.nodes['ex'].successors() 查找函数ex调用的函数
    # - callgraph.nodes['ex'].predecessors() 查找调用函数ex的函数
    callgraph: nx.DiGraph = None
    # 软件的类调用图，用法：
    # - ctx.clazz_iter(), clazz_callgraph.nodes(data=True) 遍历软件内所有类
    # - ctx.clazz('ex'), clazz_callgraph.nodes['ex']['attr'] 查找类ex的ClazzDef
    # - clazz_callgraph.nodes['ex'].successors() 查找类ex引用的类
    # - clazz_callgraph.nodes['ex'].predecessors() 查找引用类ex的类
    clazz_callgraph: nx.DiGraph = None

    # 软件的文件结构，字符串表示，例如：
    # dir1
    #   file1
    #   file2
    # dir2
    #   dir3
    #     file3
    # TODO: 改为类
    structure: str = None

    # 通过函数名获取函数定义
    def func(self, signature: str) -> Optional[FuncDef]:
        if signature not in self.callgraph.nodes:
            return None
        return self.callgraph.nodes[signature]['attr']

    # 通过类名获取类定义
    def clazz(self, signature: str) -> Optional[ClazzDef]:
        if signature not in self.clazz_callgraph.nodes:
            return None
        return self.clazz_callgraph.nodes[signature]['attr']

    # 获取全部函数定义
    def func_iter(self) -> List[FuncDef]:
        return list(self.callgraph.nodes[signature]['attr'] for signature in self.callgraph.nodes())

    # 获取全部类定义
    def clazz_iter(self) -> List[ClazzDef]:
        return list(self.clazz_callgraph.nodes[signature]['attr'] for signature in self.clazz_callgraph.nodes())

    # 获取全部API名称，过滤掉不可见的函数
    def api_iter(self) -> List[str]:
        return list(map(lambda x: x.signature, filter(lambda x: x.visible, self.func_iter())))

    # 通用的文档写入方法，传入完整文件名和文档对象
    @staticmethod
    def save_doc(filename: str, doc: Doc):
        _dir = os.path.dirname(filename)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        with open(filename, 'a') as t:
            t.write(doc.markdown() + '\n')

    # 通用的文档读取方法，传入完整文件名和文档类型，返回文档中的所有文档对象
    @staticmethod
    def load_docs(filename: str, doc_type: Type[Doc]) -> List[T]:
        if not os.path.exists(filename):
            return []
        with open(filename, 'r') as t:
            docs = doc_type.from_doc(t.read())
            return docs

    # 通用的文档读取方法，传入符号名称、完整文件名和文档类型，返回文档中的指定文档对象
    @staticmethod
    def load_doc(signature: str, filename: str, doc: Type[Doc]) -> Optional[Doc]:
        docs = EvaContext.load_docs(filename, doc)
        for d in docs:
            if d.name == signature:
                return d
        return None

    def record_error(self, stage: str, message: str) -> None:
        """记录阶段性问题（技术性失败），最终会进入批量任务结果的 issues，而不是只留在日志里。"""
        logger.warning(f'[EvaContext] stage error ({stage}): {message}')
        self.errors.append((stage, message))

    def _load_doc_index(self, filename: str, doc_type: Type[Doc]) -> Dict[str, Any]:
        """懒加载（文件, 文档类型）→ {name: Doc} 索引；同一任务内每个文件只读取解析一次。"""
        key = (filename, doc_type)
        index = self._doc_index.get(key)
        if index is not None:
            return index
        with self._doc_index_lock:
            index = self._doc_index.get(key)
            if index is None:
                index = {}
                for doc in EvaContext.load_docs(filename, doc_type):
                    name = getattr(doc, 'name', None)
                    if name and name not in index:
                        index[name] = doc
                self._doc_index[key] = index
        return index

    def _update_doc_index(self, filename: str, doc_type: Type[Doc], doc: Doc) -> None:
        """文档写入时同步更新已建立的索引，保证后续查询能命中最新内容。"""
        name = getattr(doc, 'name', None)
        if not name:
            return
        key = (filename, doc_type)
        with self._doc_index_lock:
            index = self._doc_index.get(key)
            if index is not None:
                index[name] = doc

    # 通过函数名写入函数文档
    def save_function_doc(self, signature: str, doc: ApiDoc):
        func_def: FuncDef = self.func(signature)
        filename = os.path.join(self.doc_path, f'{func_def.filename}.{ApiDoc.doc_type()}.md')
        self.save_doc(filename, doc)
        self._update_doc_index(filename, ApiDoc, doc)

    # 通过函数名加载函数文档（命中任务内索引，不再重复读取解析整份 Markdown）
    def load_function_doc(self, signature: str) -> Optional[ApiDoc]:
        func_def: FuncDef = self.func(signature)
        if func_def is None:
            return None
        filename = os.path.join(self.doc_path, f'{func_def.filename}.{ApiDoc.doc_type()}.md')
        doc = self._load_doc_index(filename, ApiDoc).get(signature)
        return doc if isinstance(doc, ApiDoc) else None

    # 通过类名写入类文档
    def save_clazz_doc(self, signature: str, doc: ClazzDoc):
        clazz_def: ClazzDef = self.clazz(signature)
        filename = os.path.join(self.doc_path, f'{clazz_def.filename}.{ClazzDoc.doc_type()}.md')
        self.save_doc(filename, doc)
        self._update_doc_index(filename, ClazzDoc, doc)

    # 通过类名加载类文档（命中任务内索引，不再重复读取解析整份 Markdown）
    def load_clazz_doc(self, signature: str) -> Optional[ClazzDoc]:
        clazz_def: ClazzDef = self.clazz(signature)
        if clazz_def is None:
            return None
        filename = os.path.join(self.doc_path, f'{clazz_def.filename}.{ClazzDoc.doc_type()}.md')
        doc = self._load_doc_index(filename, ClazzDoc).get(signature)
        return doc if isinstance(doc, ClazzDoc) else None

    # 写入单个模块文档
    def save_module_doc(self, doc: ModuleDoc):
        self.save_doc(os.path.join(self.doc_path, 'modules.md'), doc)

    # 读取所有模块文档
    def load_module_docs(self) -> List[ModuleDoc]:
        return self.load_docs(os.path.join(self.doc_path, 'modules.md'), ModuleDoc)

    # 写入仓库文档
    def save_repo_doc(self, doc):
        self.save_doc(os.path.join(self.doc_path, 'repo.md'), doc)

    # 读取仓库文档
    def load_repo_doc(self) -> Optional[RepoDoc]:
        docs = self.load_docs(os.path.join(self.doc_path, 'repo.md'), RepoDoc)
        if len(docs) == 0:
            return None
        return docs[0]


# 度量指标的基类，接受EvaContext作为参数，将被其他指标依赖的度量结果写回EvaContext
class Metric(metaclass=ABCMeta):
    @abstractmethod
    def eva(self, ctx: EvaContext):
        pass
