import os.path
from typing import List

from loguru import logger

from utils import SimpleLLM, prefix_with, ChatCompletionSettings, TaskDispatcher, ProjectSettings, Ta
from .doc import ModuleDoc, ApiDoc
from .metric import Metric

modules_summarize_prompt = '''
You are an expert in software architecture analysis. 
Your task is to review function descriptions from a code repository and organize them into multiple functional modules based on their purpose and interrelations. 
You need to output a structured summary for each identified functional module.

The standard format is in the Markdown reference paragraph below, and you shouldn't write the reference symbols `>` when you output:

> ### Module Name
> #### Description
> A concise paragraph summarizing the module's purpose, how it contributes to solving specific problems, and which functions work together within the module.
> #### Functions
> - Function1
> - Function2

You'd better consider the following workflow:
1. Identify Core Functionality. Start by reading through all function descriptions to get a broad understanding of the available functionalities and think about the core tasks or operations that these functions enable.
2. Define Functional Modules. Based on the core functionalities, define initial functional modules that encapsulate related tasks or operations. Use your expertise to determine logical groupings of functions that contribute to similar outcomes or processes.
3. Analyze Function Interdependencies. Examine how functions interact with one another. Consider whether they are called in sequence, share data, or serve complementary purposes. Recognize that a single function can be part of multiple modules if it serves different roles or contexts.
4. Refine Module Definitions. Review and adjust the boundaries of the modules as needed to ensure they accurately reflect the relationships between functions. Ensure that each module's summary clearly articulates its unique value and contribution to the overall system.
5. Generate Documentation. Write a summary for each module using the provided format you finally decide and put them together. Don't include any other content in the output. 

Please Note:
- #### Functions is a list of function included in this module. The ordinal number is the ID of the function. Please use the ordinal number to refer to the function like 2, 4, 7, ..., not the function signature.
- Try to put every function into at least one module unless the function is really useless.
- The Level 4 headings in the format like `#### xxx` are fixed, don't change or translate them. Don't add new Level 3 or Level 4 headings. Do not write anything outside the format

Now a list of function descriptions are numbered and provided as follows, you can start working.
{api_docs}
'''

modules_enhance_prompt = '''
You are an expert in software architecture analysis. 
Your task is to read the module documentation of a project and improve it.
There are two key points for improvement:
1. This module contains some functions, but these functions may not be appropriate, and you need to remove these functions.
2. Design a usage scenario for this module. You need to mock a use case that combines as many functions as possible from the module to solve a specific problem in this scenario. The use case should be realistic and demonstrate the functionality of the module.'

The improved README should keep the same format as before. To ensure the same format, you should follow the standard format in the Markdown reference paragraph below. You do not need to write the reference symbols `>` when you output:

> ### Module Name
> #### Description
> A concise paragraph summarizing the module's purpose, how it contributes to solving specific problems, and which functions work together within the module.
> #### Functions
> - Function1
> - Function2
> #### Use Case
> ```{lang}
> Mock possible usage examples of the Module with codes.
> ```

You'd better consider the following workflow:
1. Understand the Module. Read through the function descriptions and the module summary to gain a comprehensive understanding of the module's purpose and functionality. Identify the key functions that contribute to the module's core operations and remove any irrelevant functions.
2. Define the Use Case. Based on the functions available in the module, define a specific problem or scenario that the module can address. Consider how multiple functions can work together to achieve a desired outcome or solve a particular issue.
3. Rewrite the Module Summary. Update the module summary to reflect the refined list of functions and the use case you have designed. 

Please Note:
- If all functions are related to the module tightly, you don't need to remove any functions.
- #### Functions is a list of function included in this module. The ordinal number is the ID of the function. Please use the ordinal number to refer to the function like 2, 4, 7, ..., not the function signature. Only keep the relevant functions' ordinal numbers.
- You can revise the content in #### Description if it's not consistent with the answers to the questions.
- The Level 4 headings in the format like `#### xxx` are fixed, don't change or translate them. 
- Don't write new Level 3 or Level 4 headings. Don't write anything outside the format. 
- Do not output descriptions of improvements.


Here is the documentation of the module you need to enhance:

{module_doc}

Here is the documentation of the functions referenced in the module:

{functions_doc}
'''


def get_simpler_api_markdown(d: ApiDoc):
    md = f'### {d.name}\n#### Description\n{d.description}'
    if d.parameters is not None:
        md += f'#### Parameters\n{d.parameters}\n\n'
    if d.detail is not None:
        md += f'#### Code Details\n{d.detail}\n\n'
    return md.strip()


def number_to_api(m: ModuleDoc, apis: List[str]) -> ModuleDoc:
    functions = m.functions
    m.functions = []
    # 将函数编号还原为函数签名
    for f in functions:
        i = f.find('.')
        if i > 0:
            f = int(f[:i].strip('. '))
        else:
            f = int(f.strip('. '))
        if f <= 0 or f > len(apis):
            logger.warning(f'[ModuleMetric] llm provide api out of range, {f}')
            continue
        m.functions.append(apis[f - 1])
    return m


# 为模块生成文档
class ModuleMetric(Metric):

    @classmethod
    def get_draft_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'modules.v1.draft.md')

    @classmethod
    def _draft(cls, ctx) -> List[ModuleDoc]:
        existed_draft_doc = ctx.load_docs(cls.get_draft_filename(ctx), ModuleDoc)
        if len(existed_draft_doc):
            logger.info(f'[ModuleMetric] load drafts, modules count: {len(existed_draft_doc)}')
            return existed_draft_doc
        # 提取所有用户可见的函数
        apis: List[str] = ctx.api_iter()
        # 如果没有API，报错
        assert len(apis) > 0, 'no api found'
        # 使用函数描述组织上下文
        # 对函数编号，要求LLM输出函数编号，而非函数签名。LLM可能会修正函数签名，导致函数签名不一致，无法识别
        api_docs = ''.join(map(lambda a: f'{a[0]}. {a[1]}\n > {ctx.load_function_doc(a[1]).description}\n\n',
                               enumerate(apis, start=1)))
        prompt = modules_summarize_prompt.format(api_docs=api_docs)
        # 生成模块文档
        res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt).ask()
        modules = ModuleDoc.from_doc(res)
        # 将模块API编号还原为函数签名
        modules = list(map(lambda x: number_to_api(x, apis), modules))
        # 保存模块文档初稿，若模块中只有一个函数，则舍弃
        modules = list(filter(lambda x: len(modules) == 1 or len(x.functions) > 1, modules))
        for m in modules:
            ctx.save_doc(cls.get_draft_filename(ctx), m)
        logger.info(f'[ModuleMetric] gen drafts for modules, modules count: {len(modules)}')
        return modules

    @classmethod
    def _enhance(cls, ctx, drafts: List[ModuleDoc]):
        existed_modules_doc = ctx.load_module_docs()
        if len(existed_modules_doc):
            logger.info(f'[ModuleMetric] load docs, modules count: {len(existed_modules_doc)}')
            return

        # 优化模块文档
        def gen(i: int, m: ModuleDoc):
            # 使用简化的函数文档组织上下文
            functions_doc = []
            for f in m.functions:
                doc = ctx.load_function_doc(f)
                if doc is None:
                    logger.warning(f'[ModuleMetric] function {f} not found')
                    continue
                functions_doc.append(get_simpler_api_markdown(doc))
            if len(functions_doc) == 0:
                logger.warning(f'[ModuleMetric] module {m.name} contains no function')
                return
            local_apis = m.functions
            # 对模块函数进行编号
            m.functions = list(map(lambda x: f'{x[0]}. {x[1]}', enumerate(m.functions, start=1)))
            functions_doc = prefix_with('\n---\n'.join(functions_doc), '> ')
            # 使用原模块文档组织上下文
            module_doc = prefix_with(m.markdown(), '> ')
            prompt2 = modules_enhance_prompt.format(module_doc=module_doc, functions_doc=functions_doc,
                                                    lang=ctx.lang.markdown)
            # 生成模块文档
            res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt2).ask()
            doc = ModuleDoc.from_chapter(res)
            doc = number_to_api(doc, local_apis)
            # 保存模块文档
            ctx.save_module_doc(doc)
            logger.info(f'[ModuleMetric] gen doc for module {i + 1}/{len(drafts)}: {m.name}')

        TaskDispatcher(ProjectSettings.llm_thread_pool).adds(
            list(map(lambda args: Task(f=gen, args=args), enumerate(drafts)))).run()

    def eva(self, ctx):
        try:
            drafts = self._draft(ctx)
            self._enhance(ctx, drafts)
        except AssertionError as e:
            logger.error(f'[ModuleMetric] fail to gen doc for module, err: {e}')
            raise e
