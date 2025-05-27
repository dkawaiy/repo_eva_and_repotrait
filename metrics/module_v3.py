import os
from typing import List

from loguru import logger
from networkx.algorithms.community import louvain_communities

from utils import SimpleLLM, prefix_with, ChatCompletionSettings, TaskDispatcher, Task, ProjectSettings
from .doc import ModuleDoc
from .metric import EvaContext
from .module_v2 import ModuleV2Metric, modules_prompt


# 为模块生成文档V3
# 使用有向图的社区发现算法对API进行聚类
class ModuleV3Metric(ModuleV2Metric):

    @classmethod
    def get_v3_draft_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'modules.v3.draft.md')

    def eva(self, ctx: EvaContext):
        try:
            # 使用聚类算法初步划分模块，并由大模型总结模块文档
            drafts = self._draft_v3(ctx)
            # 由大模型合并模块文档
            drafts = self.merge(ctx, drafts)
            # 由大模型增强模块文档
            self._enhance(ctx, drafts)
        except AssertionError as e:
            logger.error(f'[ModuleV3Metric] fail to gen doc for module, err: {e}')
            raise e

    @classmethod
    def _draft_v3(cls, ctx: EvaContext) -> List[ModuleDoc]:
        existed_draft_doc = ctx.load_docs(cls.get_v3_draft_filename(ctx), ModuleDoc)
        if len(existed_draft_doc):
            logger.info(f'[ModuleV3Metric] load module drafts, modules count: {len(existed_draft_doc)}')
            return existed_draft_doc
        # 提取所有用户可见的函数
        apis = set(ctx.api_iter())
        # 如果没有API，报错
        assert len(apis) > 0, 'no api found'
        logger.info('[ModuleV3Metric] clustering...')
        callgraph = ctx.callgraph
        cluster = louvain_communities(callgraph)
        # 只保留包含API的组，删除非API函数
        cluster = list(filter(lambda g: len(g) > 0, map(lambda x: list(filter(lambda y: y in apis, x)), cluster)))
        logger.info(f'[ModuleV3Metric] cluster to {len(cluster)} groups')

        def gen(g: List[str]):
            # 使用函数描述组织上下文
            api_docs = ''.join(map(lambda x: f'- {x}\n > {ctx.load_function_doc(x).description}\n\n', g))
            prompt2 = modules_prompt.format(api_doc=prefix_with(api_docs, '>'), api_example=g[0])
            # 生成模块文档
            res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt2).ask(lambda x: x.replace('---', '').strip())
            docs = ModuleDoc.from_doc(res)
            # 保存模块文档
            for doc in docs:
                ctx.save_doc(cls.get_v3_draft_filename(ctx), doc)
                logger.info(f'[ModuleV3Metric] gen draft for module {doc.name}')

        TaskDispatcher(ProjectSettings.llm_thread_pool).adds(
            list(map(lambda args: Task(f=gen, args=(args,)), cluster))).run()
        return ctx.load_docs(cls.get_v3_draft_filename(ctx), ModuleDoc)
