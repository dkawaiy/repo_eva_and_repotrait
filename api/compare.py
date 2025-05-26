from typing import List

from loguru import logger

from metrics import RepoDoc, ModuleDoc, ApiDoc
from utils import post, ChatCompletionSettings, SimpleLLM, prefix_with
from .vo import RAStatus, CompReq, CompResult, EvaResult

compare_prompt = '''
You are an expert in software development.
Below are multiple software with similar functions. 
They may have different solutions to the same problem, for example, zip and tar are different methods for decompression.
They may be old and latest versions of the same software and therefore differ in capabilities.

Your task is to distinguish the similarities and differences between the functions of the software and tell me which should be used in which scenarios.

{software_docs}

Please compare the software and write a comparison report with the help of a table
The standard format is in the Markdown reference paragraph below, and you do not need to write the reference symbols `>` when you output:

> | FEATURE POINTS |{names}|
> | ---- | ---- | ---- | ... |
> | point1 | How {s1} perform on point1 | How {s2} 2 perform on point1 | ... |
> | point2 | **How {s1} perform on point2** | How {s2} perform on point2 | ... |
> | point3 | How {s1} perform on point3 | X  | ... |
> | point4 | X | How {s2} perform on point4 | ... |
> | ... | ... | ... | ... |
>
> Summary
> - {s1} is better because ...
> - {s2} should be used in XXX scenarios because ...
> ...

You'd better consider the following workflow:
1. Understand the Software. Read through the documents of each software to understand their core functionalities.
2. Summarize the Feature Points. Identify the key feature points of each software from the modules documentation.
3. Find the same Feature Points. Compare the performance of the same Feature Points in the two software. 
4. Find the different Feature Points. Identify the Feature Points that only one software has.
5. Write a Summary. Summarize the comparison and give your suggestions.

Please Note:
- For a feature point, if one software performs better, use **bold** to highlight its performance.
- For a feature point, if one of the software doesn't show the feature point, output X.
- Give a summary of the comparison behind the table showing which software is better and why. 
- Don’t mention all the functions the software have, summarize the most important ones.
'''


def compare(req: CompReq):
    results = list(map(lambda f: EvaResult.model_validate_json(f), req.results))
    try:
        if len(req.names) == 2:
            apis1 = set(map(lambda f: ApiDoc.from_dict(f).name, results[0].functions))
            apis2 = set(map(lambda f: ApiDoc.from_dict(f).name, results[1].functions))
            if len(apis1.intersection(apis2)) / min(len(apis1), len(apis2)) > 0.5:
                logger.info(
                    f'[Compare] {req.names[0]} and {req.names[1]} holds many same APIs, speculating they are different versions of the same software')
                # TODO，这块代码写的很简陋，需要重构
                res = compare_releases(req.names, results)
                post(url=req.callback, content=CompResult(requestId=req.requestId, result=res, message='ok',
                                                          status=RAStatus.success.value).model_dump_json())
                return
        s = ''
        for i, name in enumerate(req.names):
            s += f'## {name}\n'
            r: EvaResult = results[i]
            s += RepoDoc.from_dict(r.repo[0]).markdown()
            s += '\n'.join(list(map(lambda m: ModuleDoc.from_dict(m).markdown(), r.modules)))
            s += '---'
        p = compare_prompt.replace('{s1}', req.names[0]).replace('{s2}', req.names[1])
        p = p.format(software_docs=prefix_with(s, '> '), names=' | '.join(req.names))
        llm = SimpleLLM(ChatCompletionSettings())
        res = llm.add_user_msg(p).ask()
        post(url=req.callback, content=CompResult(requestId=req.requestId, result=res, message='ok',
                                                  status=RAStatus.success.value).model_dump_json())
    except Exception as e:
        logger.error(f'fail to compare doc for {req.requestId}, err={e}')
        post(req.callback, content=CompResult(requestId=req.requestId, result=None, message=str(e),
                                              status=RAStatus.fail.value).model_dump_json())


compare_release_prompt = '''
You are an expert in software development.
Below are two releases of the same software named {s1} and {s2}. Specifically, {s1} is the old version and {s2} is the new version.
Your task is to distinguish the similarities and differences between the functions of the two software and tell me how {s2} improves over {s1}.

Here is the summary of the two releases:
{repo_docs}

Here is the new API introduced by {s2}:
{new_api_docs}

Here is the deprecated API holds by {s1} and removed in {s2}:
{deprecated_api_docs}

Please compare the software and write a comparison report with the help of a table
The standard format is in the Markdown reference paragraph below, and you do not need to write the reference symbols `>` when you output:

> | FEATURE POINTS | {s1} | {s2} |
> | ---- | ---- | ---- |
> | point1 | How {s1} perform on point1 | How {s2} 2 perform on point1 |
> | point2 | **How {s1} perform on point2** | How {s2} perform on point2 |
> | point3 | How {s1} perform on point3 | X  |
> | point4 | X | How {s2} perform on point4 | 
> | ... | ... | ... | ... |
>
> Summary
> - {s1} is better because ...
> - {s2} should be used in XXX scenarios because ...
> ...

You'd better consider the following workflow:
1. Understand the Software. Read through the summaries of the two releases to understand the core functionalities of the software.
2. Indentify the improvements. Identify the key improvements of the new version from the new APIs and deprecated APIs.
3. Indentify the trade-offs. Identify the trade-offs of the new version from the deprecated APIs.
4. Write a Summary. Summarize the comparison and give your suggestions.

Please Note:
- For a feature point, if one software performs better, use **bold** to highlight its performance.
- For a feature point, if one of the software doesn't show the feature point, output X.
- Give a summary of the comparison behind the table showing which software is better and why. 
- Don’t mention all the functions the software have, summarize the most important ones.
'''


def compare_releases(names: List[str], results: List[EvaResult]) -> str:
    apis_size = list(map(lambda r: len(r.functions), results))
    # 假设API多的是新版本
    s = list(sorted(zip(names, results, apis_size), key=lambda x: x[2]))
    new_apis = list(map(lambda f: ApiDoc.from_dict(f), s[1][1].functions))
    old_apis = list(map(lambda f: ApiDoc.from_dict(f), s[0][1].functions))
    new_apis_names = set(map(lambda f: f.name, new_apis))
    old_apis_names = set(map(lambda f: f.name, old_apis))
    new_api_docs = '\n'.join(map(lambda f: f'### {f.name}\n{f.description}',
                                 filter(lambda f: f.name not in old_apis_names, new_apis)))
    logger.info(f'[Compare] {s[1][0]} holds {len(new_apis_names.difference(old_apis_names))} new APIs')
    deprecated_api_docs = '\n'.join(
        map(lambda f: f'### {f.name}\n{f.description}', filter(lambda f: f.name not in new_apis_names, old_apis)))
    logger.info(f'[Compare] {s[0][0]} holds {len(old_apis_names.difference(new_apis_names))} deprecated APIs')
    repo_docs = f'## {s[0][0]}' + RepoDoc.from_dict(s[0][1].repo[0]).markdown() + f'\n## {s[1][0]}' + RepoDoc.from_dict(
        s[1][1].repo[0]).markdown()
    prompt = compare_release_prompt.replace('{s1}', s[0][0]).replace('{s2}', s[1][0])
    prompt = prompt.format(repo_docs=prefix_with(repo_docs, '> '), new_api_docs=prefix_with(new_api_docs, '> '),
                           deprecated_api_docs=prefix_with(deprecated_api_docs, '> '))
    res = SimpleLLM(ChatCompletionSettings()).add_user_msg(prompt).ask()
    return res
