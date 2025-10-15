import os.path
import re
from typing import List

from loguru import logger


from utils import SimpleLLM, prefix_with, ChatCompletionSettings, ToolsLLM, TaskDispatcher, Task, ProjectSettings,reformat_markdown_headers
from . import EvaContext
from .doc import RepoDoc
from .metric import Metric

repo_summarize_prompt = '''
You are a senior software engineer. You have developed a {lang} software. 
Now you need to write a Github README.md for it to help users understand your software.

The standard format is in the Markdown reference paragraph below, and you do not need to write the reference symbols `>` when you output:

> ### README
> #### Description
> A concise paragraph summarizing the software.
> #### Features
> - Feature1...
> - Feature2...
> #### Standards
> - Standard1
> - Standard2

Please Note:
- #### Features is a list of main features that the software providing to users. Each feature should be described in one sentences. Don't mention specific function names. Don't make up unverified features. 
- #### Standards is the names of standards that the software designed for. For example, most of the APIs in `cJSON` are designed for JSON serialization, we can speculate it support JSON 1.0. This example does not mean that the software implements JSON 1.0, so don't write it in the README unless you are sure about it.
- The Level 4 headings in the format like `#### xxx` are fixed, don't change or translate them. 
- Don't add new Level 3 or Level 4 headings. Do not write anything outside the format. Don't output descriptions out of the format.

Here is the documentation of main modules of your software. You should refer it to write the README.md:

{modules_doc}
'''

questions_prompt = '''
You are a senior software engineer.

Your aim is to improve a {lang} software you developed.
In order to find room for improvement in the software, you decide to refer to the focus of excellent software and design some questions to help you improve the software.

The summary of your software is as follows:

{repo_doc}

You'd better consider the following workflow:
1. Refer excellent software. You should find and analyze some widely-used software which provide similar functions to yours as benchmarks. 
2. Check out the concerns. If you're working on XML serialization software, you might find different software supports different protocols like XML 1.0, XPath, and DOM/SAX. Similarly, when focusing on software designed for serialization and deserialization tasks, the format in which data is produced becomes a significant consideration. Protobuf, for example, excels in compressing objects into a binary format for superior compression ratios, whereas JSON offers a human-readable representation with greater ease of manipulation.
3. Ask some questions. You should check that whether your software meets the above concerns so ask some questions to your software first. You may question 'What format is the software product in?'.
4. Check the questions. You should check the questions again and make sure they are meaningful. Specifically, you should provide a basis for these questions by answering how excellent software you have found solve these questions.

The standard format is in the Markdown reference paragraph below, and you do not need to write the reference symbols `>` when you output:

> - Q1: question?
> - A1: how software X solve the question
> - Q2: question?
> - A2: ...
> - ...

Please Note:
- Strictly follow the above format for output. Don't output any information outside the QA list. 
- Questions should only focus on the functionality of the software, not on performance, documentations, community maintenance, etc. that cannot be obtained from the software source code and documentation.
- Questions are meant to guide you in improving your software, so don’t get away from the field your software work for.
- You do not need to write the reference symbols `>` when you output.

'''

qa_prompt = '''
You are an expert on software engineering. 
You have found a software that meets your requirements. But you still have some questions about the software.
Luckily, the documentation of the software is available with a document query tool.

The software you found is summarized as follows:

{repo_doc}

The software have the following core modules:

{modules_doc}

Please answer the questions based on the documentation of the software.

Please Note:
- When answering a question, you should first draw your conclusion in one sentence.
- Each question should be answered in only one paragraph.
'''

repo_enhance_prompt = '''
You are an expert in software. 
Your task is to read the README documentation of a {lang} project and improve it.

The README is as follows:

{repo_doc}

You suspect that there is fraud in this README. 
In order to learn more about the software, you asked the software developer some questions and got answers. 
The questions are as follows.

{qa}

Now you have the confidence to improve this README based on the above QA.
Especially, you should check the following points:
1. The description should accurately and clearly describe the main functions of the software.
2. Ensure that each feature is implemented in software and reflects its advantages over competing products.
3. The standards should be verified and reflect the core functionality of the software.

The improved README should keep the same format as before with a new section "#### Scenarios".
To ensure the same format, you should follow the standard format in the Markdown reference paragraph below. You do not need to write the reference symbols `>` when you output:

> ### README
> #### Description
> A concise paragraph summarizing the software.
> #### Features
> - Feature1...
> - Feature2...
> #### Standards
> - Standard1
> - Standard2
> #### Scenarios
> - Scenario1...
> - Scenario2...

Please Note:
- #### Features is a list of main features that the software providing to users. Each feature should be described in one sentences. Don't mention specific function names. Don't make up unverified features. 
- #### Standards is the names of standards that the software designed for. For example, most of the APIs in `cJSON` are designed for JSON serialization, we can speculate it support JSON 1.0. This example does not mean that the software implements JSON 1.0, so don't write it in the README unless you are sure about it.
- #### Scenarios is a list of scenarios that the software can be used in. For example, JSON is more suitable for serialization scenarios that require fast iteration and are easy for humans to read; while Protobuf is suitable for data exchange between systems that have high requirements for performance and cross-language communication.
- The Level 4 headings in the format like `#### xxx` are fixed, don't change or translate them. 
- You can revise the content in #### Description, #### Features and #### Standards sections if they are not consistent with the answers to the questions.
- Don't add new Level 3 or Level 4 headings. Do not write anything outside the format. Do not output descriptions of improvements or summary in the end.
- You do not need to write the reference symbols `>` when you output.
'''


# 为仓库生成文档，使用大模型的tool调用能力回答问题，验证文档的准确性
class RepoMetric(Metric):

    @classmethod
    def get_draft_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'repo-draft.md')

    @classmethod
    def get_qa_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'repo-q.md')

    @classmethod
    def get_qa_answer_filename(cls, ctx):
        return os.path.join(ctx.doc_path, 'repo-a.md')

    # 生成仓库文档初稿
    @classmethod
    def _draft(cls, ctx: EvaContext) -> RepoDoc:
        # 读取存在的仓库文档初稿
        existed_draft_doc = ctx.load_docs(cls.get_draft_filename(ctx), RepoDoc)
        if len(existed_draft_doc):
            logger.info(f'[RepoMetric] load repo draft')
            return existed_draft_doc[0]
        # 使用模块文档组织上下文
        modules = ctx.load_module_docs()
        # 若模块文档为空，报错
        assert len(modules) > 0, 'no module found'
        logger.info(f'[RepoMetric] gen doc for repo, modules count: {len(modules)}')
        modules_doc = '\n\n---\n\n'.join(map(lambda m: m.markdown(), modules))
        modules_doc = modules_doc[:min(100000,len(modules_doc)-5)]
        prompt = repo_summarize_prompt.format(modules_doc=prefix_with(modules_doc, '> '), lang=ctx.lang.markdown)
        res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt).ask()
        res = reformat_markdown_headers(res, ['Description', 'Features', 'Standards'])
        doc = RepoDoc.from_chapter(res)
        # 保存仓库文档初稿
        ctx.save_doc(cls.get_draft_filename(ctx), doc)
        logger.info(f'[RepoMetric] gen doc for repo, draft inited')
        return doc

    # 基于仓库文档初稿，生成问题列表
    @classmethod
    def _questions(cls, ctx: EvaContext, draft: RepoDoc) -> List[str]:
        # 读取存在的问题列表
        if os.path.exists(cls.get_qa_filename(ctx)):
            logger.info(f'[RepoMetric] load repo questions')
            with open(cls.get_qa_filename(ctx), 'r') as f:
                questions = f.readlines()
                return list(map(lambda x: x.strip(), questions))
        prompt = questions_prompt.format(repo_doc=draft.markdown(), lang=ctx.lang.markdown)
        questions_doc = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt).ask()
        question_pattern = re.compile(r'- Q\d+: (.*?)\n- A\d+: (.*?)(?=\n|\Z)', re.DOTALL)
        questions = list(
            map(lambda x: f'{x.group(1)}({x.group(2)})'.strip(), question_pattern.finditer(questions_doc)))
        # 保存仓库文档QA
        with open(cls.get_qa_filename(ctx), 'w') as f:
            f.write('\n'.join(questions))

        logger.info(f'[RepoMetric] gen doc for repo, questions inited')
        return questions

    # 回答问题
    @classmethod
    def _answer(cls, ctx: EvaContext, draft: RepoDoc, questions: List[str]) -> List[str]:
        # 读取存在的回答
        if os.path.exists(cls.get_qa_answer_filename(ctx)):
            logger.info(f'[RepoMetric] load repo questions answer')
            with open(cls.get_qa_answer_filename(ctx), 'r') as f:
                answers = f.readlines()
                return list(map(lambda x: x.strip(), answers))

        def read_functions_md(name: str):
            return ctx.load_function_doc(name).markdown()

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_functions_md",
                    "description": "Useful when you need to know the details of a function.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "whole name of the Function with arguments and return type",
                            }
                        },
                        "required": ["name"]
                    }
                }
            }
        ]
        tools_map = {'read_functions_md': read_functions_md}
        modules = ctx.load_module_docs()
        modules_doc = '\n\n---\n\n'.join(map(lambda m: m.markdown(), modules))
        # 回答每个问题，LLM结果直接写入答案数组的对应位置，避免顺序错乱
        answers = [''] * len(questions)

        def answer_qa(i: int, q: str):
            toolLLM = ToolsLLM(ChatCompletionSettings(), tools, tools_map).add_system_msg(
                qa_prompt.format(repo_doc=prefix_with(draft.markdown(), '> '),
                                 modules_doc=prefix_with(modules_doc, '> ')))
            answers[i] = toolLLM.add_user_msg(q).ask().strip()
            logger.info(f'[RepoMetric] answer question {i + 1}')

        TaskDispatcher(ProjectSettings.llm_thread_pool).adds(
            list(map(lambda args: Task(f=answer_qa, args=args), enumerate(questions)))).run()
        # 保存仓库文档QA-Answer
        with open(cls.get_qa_answer_filename(ctx), 'w') as f:
            f.write('\n'.join(answers))
        return answers


    # 修正仓库文档
    @classmethod
    def _revise(cls, ctx: EvaContext, draft: RepoDoc, questions: List[str], answers: List[str]):
        if ctx.load_repo_doc():
            logger.info(f'[RepoMetric] load docs, repo doc exist')
            return
        qa_doc = '\n'.join(map(lambda i: f'- Q{i}: {questions[i]}\n > A{i}: {answers[i]}', range(len(questions))))
        prompt = repo_enhance_prompt.format(repo_doc=prefix_with(draft.markdown(), '> '), qa=prefix_with(qa_doc, '> '), lang=ctx.lang.markdown)
        res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt).ask()
        res = reformat_markdown_headers(res, ['Description', 'Features', 'Standards', 'Scenarios'])
        doc = RepoDoc.from_chapter(res)
        ctx.save_repo_doc(doc)
        logger.info(f'[RepoMetric] gen doc for repo, doc saved')
        return doc


    def eva(self, ctx):
        try:
            draft = self._draft(ctx)
            questions = self._questions(ctx, draft)
            answers = self._answer(ctx, draft, questions)
            self._revise(ctx, draft, questions, answers)
        except ValueError as e:
            logger.error(f'[RepoMetric] fail to gen doc for repo, err={e}')