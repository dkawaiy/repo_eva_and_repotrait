import os
from typing import List

import numpy as np
from loguru import logger

from utils import SimpleLLM, prefix_with, ChatCompletionSettings, SimpleRAG, RagSettings, TaskDispatcher, Task, \
    ProjectSettings,reformat_markdown_headers
from .doc import ModuleDoc
from .metric import EvaContext
from .module import ModuleMetric, number_to_api

modules_prompt = '''
You are an expert in software architecture analysis. 
Your task is to review function descriptions from a code repository and organize them into one or more functional module based on their purpose and interrelations. 

The following will provide you with the documentation of each function in the software in turn:
{api_doc}

Please organize these functions into one or more functional module and output each module documentation in the following format. 
You shouldn't write the reference symbols `>` when you output.

> ### Module Name
> #### Description
> A concise paragraph summarizing the module's purpose, how it contributes to solving specific problems, and which functions work together within the module.
> #### Functions
> - Function Signature 1
> - Function Signature 2

You'd better consider the following workflow:
1. Identify Core Functionality. Start by reading through all function descriptions to get a broad understanding of the available functionalities and think about the core tasks or operations that these functions enable. 
2. Filter Functions. Based on the core functionalities, filter out the functions that are not appropriate for the module. Consider whether they are called in sequence, share data, or serve complementary purposes. 
3. Name the Module in the required language. Review the core functionalities and the use case to come up with a suitable name for the module to replace the placeholder "Module Name" in the template. Remember that this is just a module of the software. Don't make it too broad.

Please Note:
- #### Functions is a list of function signatures included in this module. Please output the exact function signature as provided in the context.
- The Level 4 headings in the format like `#### Description` are fixed, don't change or translate them. Don't add new Level 3 or Level 4 headings. Do not write anything outside the format.
- Don't add divider lines like `---` between multiple modules.
- Assign each provided function to exactly one module and never repeat a function across modules.
- Keep each description concise and stop after the final function list.
'''

modules_merge_prompt = '''
You are an expert in software architecture analysis.
You have reviewed the function descriptions from a code repository and organized them into functional modules based on their purpose and interrelations.
Now you need to merge the modules to make the documentation more concise and clear.

The following are the documentation of the modules you have organized:
{module_doc}

Please merge modules with similar functions and output the merged module documentation in the same format.
The correct format of each module documentation is as follows and you shouldn't write the reference symbols `>` when you output:

> ### Module Name
> #### Description
> A concise paragraph summarizing the module's purpose, how it contributes to solving specific problems, and which functions work together within the module.
> #### Functions
> - Function Signature 1
> - Function Signature 2

You'd better consider the following workflow:
1. Review Module Descriptions. Read through the descriptions of each module to understand the core functionalities of the software.
2. Merge Modules. Identify modules with similar functions or that can be combined to form a more comprehensive module. Consider how the functions in each module can work together to solve a specific problem or address a particular use case.
3. Remove useless modules. If there are modules that are not related to the software's core functionalities, consider removing them from the documentation to make it more concise.
4. Name the Merged Module in the required language. Based on the functions and use cases of the merged modules, come up with a suitable name for the merged module to replace the placeholder "Module Name" in the template. Remember that this is just a module of the software. Don't make it too broad.

Please Note:
- #### Functions is a list of function signatures included in this module. Please output the exact function signature.
- The Level 4 headings in the format like `#### Description` are fixed, don't change or translate them. Don't add new Level 3 or Level 4 headings. Do not write anything outside the format.
- Don't omit any modules related to the software's core functionalities. If they cannot be merged, keep them.
- Don't add divider lines like `---` between multiple modules.

'''


def partition_module_docs(docs: List[ModuleDoc], local_apis: List[str]) -> List[ModuleDoc]:
    """Validate an LLM-produced partition without duplicating cluster APIs."""
    allowed_apis = set(local_apis)
    assigned_apis = set()
    valid_docs = []

    for doc in docs:
        selected_apis = []
        for api in (doc.functions or []):
            if api in allowed_apis and api not in assigned_apis:
                selected_apis.append(api)
                assigned_apis.add(api)
        if selected_apis:
            doc.functions = selected_apis
            valid_docs.append(doc)

    if not valid_docs and docs:
        # Parsing may occasionally lose the function list.  Fall back to one
        # module for the cluster instead of duplicating all APIs across every
        # returned module.
        docs[0].functions = list(local_apis)
        valid_docs = [docs[0]]
        assigned_apis = set(local_apis)

    missing_apis = [api for api in local_apis if api not in assigned_apis]
    if missing_apis and valid_docs:
        # Preserve complete coverage without duplication.  Put unparsed
        # signatures on the smallest resulting module.
        target_doc = min(valid_docs, key=lambda item: len(item.functions or []))
        target_doc.functions.extend(missing_apis)

    return valid_docs



class ModuleV5Metric(ModuleMetric):

    @classmethod
    def get_v5_draft_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'modules.v5.draft.md')

    def eva(self, ctx , merge_op=False, merge_with_llm=True):
        try:
            # 入口短路：若最终模块文档已存在，则跳过全部步骤
            existed_modules = ctx.load_module_docs()
            if len(existed_modules):
                logger.info(f'[ModuleV5Metric] modules.md exists, skip generation: {len(existed_modules)}')
                return
            # 使用聚类算法初步划分模块，并由大模型总结模块文档
            drafts = self._draft_v5(ctx)
            # 通过草稿文档 embedding 聚类合并模块，并可选使用 LLM 重命名
            if merge_op:
                drafts = self.merge(ctx, drafts, merge_with_llm=merge_with_llm)
            # 由大模型增强模块文档
            self._enhance(ctx, drafts)
        except AssertionError as e:
            logger.error(f'[ModuleV5Metric] fail to gen doc for module, err: {e}')
            raise e

    # 将API分组，对每组API生成模块初稿
    @classmethod
    def _draft_v5(cls, ctx: EvaContext) -> List[ModuleDoc]:
        existed_draft_doc = ctx.load_docs(cls.get_v5_draft_filename(ctx), ModuleDoc)
        if len(existed_draft_doc):
            logger.info(f'[ModuleV5Metric] load module drafts, modules count: {len(existed_draft_doc)}')
            return existed_draft_doc
        # 提取所有用户可见的函数
        apis: List[str] = ctx.api_iter()
        # 如果没有API，报错
        assert len(apis) > 0, 'no api found'
        # 预建 signature -> index 字典：邻居索引查找从 O(V) 线性扫描降为 O(1)
        api_index = {api: idx for idx, api in enumerate(apis)}
        rag = SimpleRAG(RagSettings())
        logger.info('[ModuleV5Metric] Building neighbor-aggregated embeddings...')
        
        # 1. 先生成所有函数的自身语义向量
        self_docs = []
        for api in apis:
            api_doc = ctx.load_function_doc(api)
            self_docs.append(f"{api}: {api_doc.description}")
        
        logger.info('[ModuleV5Metric] Encoding self embeddings...')
        self_embeddings = rag._encode_in_batches(self_docs)
        
        # 计算全局平均向量（用于没有邻居的函数）
        global_mean_embedding = np.mean(self_embeddings, axis=0)
        
        # 2. 为每个函数生成邻居向量
        logger.info('[ModuleV5Metric] Building neighbor embeddings...')
        neighbor_embeddings = []
        
        for i, api in enumerate(apis):
            neighbors = []
            try:
                # 获取调用关系
                if api in ctx.callgraph:
                    # 被调用者（successors）
                    successors = list(ctx.callgraph.successors(api))
                    # 调用者（predecessors）
                    predecessors = list(ctx.callgraph.predecessors(api))
                    
                    # 使用所有邻居
                    all_neighbors = successors + predecessors
                    
                    # 收集邻居的索引（预建 signature -> index 字典，避免对每个邻居线性扫描 apis）
                    neighbor_indices = [api_index[neighbor] for neighbor in all_neighbors if neighbor in api_index]
                    
                    # 如果有邻居，取邻居向量的平均
                    if neighbor_indices:
                        neighbor_vecs = [self_embeddings[idx] for idx in neighbor_indices]
                        neighbor_embedding = np.mean(neighbor_vecs, axis=0)
                    else:
                        # 没有邻居，使用全局平均
                        neighbor_embedding = global_mean_embedding
                else:
                    # 不在调用图中，使用全局平均
                    neighbor_embedding = global_mean_embedding
            except Exception as e:
                # 出错，使用全局平均
                logger.debug(f'[ModuleV5Metric] neighbor embedding fallback for {api}: {e}')
                neighbor_embedding = global_mean_embedding
            
            neighbor_embeddings.append(neighbor_embedding)
        
        neighbor_embeddings = np.array(neighbor_embeddings)
        
        # 3. 拼接自身向量和邻居向量
        logger.info('[ModuleV5Metric] Concatenating self and neighbor embeddings...')
        combined_embeddings = np.concatenate([self_embeddings, neighbor_embeddings], axis=1)
        
        # 4. 使用密度聚类（直接传入已经编码好的向量）
        logger.info('[ModuleV5Metric] Clustering with UMAP + HDBSCAN...')
        # 需要修改 density_cluster 来接受预编码的向量，或者我们直接在这里调用降维+聚类
        
        # 为了复用 density_cluster，我们需要让它支持直接传入向量
        # 暂时我们在这里直接实现降维+聚类
        from sklearn.manifold import TSNE
        
        n_samples = len(combined_embeddings)
        # TSNE 的 barnes_hut 算法要求 n_components < 4，通常用于降维到 2 或 3 维
        n_components = min(3, n_samples - 1)
        
        logger.info(f'[ModuleV5Metric] Reducing dimensions from {combined_embeddings.shape[1]} to {n_components}...')
        
        if n_components < 1:
            reduced_embeddings = np.zeros((n_samples, 2))
        else:
            # perplexity 应该小于 n_samples
            perplexity = min(30, n_samples - 1)
            reducer = TSNE(n_components=n_components, random_state=42, n_jobs=-1, perplexity=perplexity)
            reduced_embeddings = reducer.fit_transform(combined_embeddings)
        
        logger.info('[ModuleV5Metric] Clustering with HDBSCAN...')

        from sklearn.cluster import HDBSCAN
        clusterer = HDBSCAN(min_cluster_size=min(5, n_components), min_samples=1)
        labels = clusterer.fit_predict(reduced_embeddings)
    
        
        # 整理聚类结果
        from collections import defaultdict
        clusters_dict = defaultdict(list)
        noise_points = []
        
        for i, label in enumerate(labels):
            if label == -1:
                noise_points.append(i)
            else:
                clusters_dict[label].append(i)
        
        cluster = list(clusters_dict.values())
        if noise_points:
            cluster.append(noise_points)
        
        logger.info(f'[ModuleV5Metric] Clustered into {len(cluster)} groups')

        def gen(g: List[int]):
            local_apis = list(map(lambda x: apis[x], g))  # 获取当前组的API
            
            try:
                # 使用函数描述组织上下文
                # 不再使用编号，直接使用函数签名
                api_docs = ''.join(
                    map(lambda a: f'- {a}\n > {ctx.load_function_doc(a).description}\n\n',
                        local_apis))
                api_docs = api_docs[:100000]
                prompt2 = modules_prompt.format(api_doc=prefix_with(api_docs, '> '))
                
                # 调用LLM生成模块文档
                res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt2).ask(
                    lambda x: x.replace('---', '').strip()
                )
                
                # 尝试解析LLM响应
                docs = None
                try:
                    docs = ModuleDoc.from_doc(res)
                except Exception as e:
                    logger.warning(
                        f'[ModuleV5Metric] First parse failed for API group (size={len(local_apis)}), '
                        f'trying to reformat headers: {e}'
                    )
                    try:
                        res = reformat_markdown_headers(res, ['Description', 'Functions'])
                        docs = ModuleDoc.from_doc(res)
                    except Exception as e2:
                        message = (
                            f'Failed to parse LLM response after reformatting for API group '
                            f'(size={len(local_apis)}): {type(e2).__name__}: {e2}'
                        )
                        logger.error(
                            f'[ModuleV5Metric] {message}\n'
                            f'Original error: {e}\n'
                            f'LLM response preview:\n{res[:500]}...'
                        )
                        ctx.record_error('module_draft', message)
                        return  # 跳过这个组
                
                
                # Keep the partition returned by the model.  The previous
                # implementation assigned every API in the cluster to every
                # returned module, multiplying prompt size and producing many
                # near-identical enhancement requests.
                valid_docs = partition_module_docs(docs, local_apis)

                for doc in valid_docs:
                    try:
                        # 保存模块文档
                        ctx.save_doc(cls.get_v5_draft_filename(ctx), doc)
                        logger.info(
                            f'[ModuleV5Metric] Generated draft for module "{doc.name}" '
                            f'with {len(doc.functions)} functions'
                        )
                        
                    except Exception as e:
                        message = f'Failed to save module draft "{doc.name}": {type(e).__name__}: {e}'
                        logger.error(f'[ModuleV5Metric] {message}', exc_info=True)
                        ctx.record_error('module_draft', message)
                        
            except Exception as e:
                message = (
                    f'Failed to generate module for API group (size={len(local_apis)}): '
                    f'{type(e).__name__}: {e}'
                )
                logger.error(f'[Module5Metric] {message}', exc_info=True)
                ctx.record_error('module_draft', message)

        TaskDispatcher(ProjectSettings.llm_thread_pool).adds(
            list(map(lambda args: Task(f=gen, args=(args,)), cluster))).run()
        return ctx.load_docs(cls.get_v5_draft_filename(ctx), ModuleDoc)



    # 合并模块初稿
    @classmethod
    def merge(cls, ctx: EvaContext, drafts: List[ModuleDoc], merge_with_llm: bool = True) -> List[ModuleDoc]:
        if not drafts:
            return []

        if len(drafts) == 1:
            return drafts

        # 构造用于编码的文本（使用 markdown 作为语义输入）
        docs_text = []
        for d in drafts:
            preview_functions = '\n'.join([f'- {x}' for x in (d.functions or [])[:20]])
            docs_text.append(
                f'### {d.name}\n'
                f'#### Description\n{d.description}\n\n'
                f'#### Functions\n{preview_functions}'
            )

        rag = SimpleRAG(RagSettings())
        try:
            # 使用 density_cluster 基于向量聚类草稿文档
            n_components = min(3, max(2, len(docs_text) - 1))
            min_cluster_size = 2
            clusters = rag.density_cluster(
                docs_text,
                use_umap=False,
                n_components=n_components,
                min_cluster_size=min_cluster_size
            )
        except Exception as e:
            message = f'module clustering failed, fallback to singletons: {type(e).__name__}: {e}'
            logger.warning(f'[ModuleV5Metric] {message}')
            ctx.record_error('module_merge', message)
            clusters = [[i] for i in range(len(drafts))]

        merged_docs: List[ModuleDoc] = []
        for ci, group in enumerate(clusters):
            # 合并函数列表，保持顺序且去重
            funcs = []
            for idx in group:
                for f in (drafts[idx].functions or []):
                    if f not in funcs:
                        funcs.append(f)

            # 选择代表性的名称与描述（倾向于包含最多函数的草稿）
            rep_idx = group[0]
            try:
                rep_idx = max(group, key=lambda i: len(drafts[i].functions or []))
            except Exception:
                rep_idx = group[0]

            name = drafts[rep_idx].name or f'Module_{ci + 1}'
            # 合并描述，过滤掉占位符式的空描述
            descs = [drafts[i].description for i in group if drafts[i].description and not drafts[i].description.strip().startswith('[')]
            description = '\n\n'.join(descs) if descs else drafts[rep_idx].description or ''

            if merge_with_llm:
                try:
                    cluster_doc = '\n\n'.join([drafts[i].markdown() for i in group])
                    merge_prompt = f'''You are an expert in software architecture analysis.
Merge the following module drafts into ONE concise module.

Output strictly in this format:
### Module Name
#### Description
<one concise description paragraph>
#### Functions
- function_signature_1

Rules:
- Keep heading names exactly as shown.
- Do not add extra headings or separators.
- Keep language consistent with input.

Drafts:
{cluster_doc}
'''
                    res = SimpleLLM(ChatCompletionSettings()).add_user_msg(merge_prompt).ask(
                        lambda x: x.replace('---', '').strip()
                    )
                    res = reformat_markdown_headers(res, ['Description', 'Functions'])
                    parsed = ModuleDoc.from_doc(res)
                    if len(parsed) > 0:
                        name = parsed[0].name or name
                        if parsed[0].description and not parsed[0].description.strip().startswith('['):
                            description = parsed[0].description
                except Exception as e:
                    message = f'llm rename/summary failed for cluster {ci}: {type(e).__name__}: {e}'
                    logger.warning(f'[ModuleV5Metric] {message}')
                    ctx.record_error('module_merge', message)

            doc = ModuleDoc(name=name, description=description, functions=funcs)
            merged_docs.append(doc)

        logger.info(f'[ModuleV5Metric] merge modules: {len(drafts)} -> {len(merged_docs)}')
        return merged_docs
