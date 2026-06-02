import os.path
import shutil

import click

from metrics import EvaContext
from api import eva
from utils import LangEnum


def response_with_gitbook(doc_path: str):
    def summary_sort(s: str):
        # 模块摘要优先级最高
        if 'modules.md' in s or 'cares.md' in s:
            return 1, s.lower()
        else:
            # 如果字符串不符合任何条件，给予最低优先级
            return 4, s.lower()

    summary = '# Summary\n'
    for root, _, files in os.walk(doc_path):
        root = root[len(doc_path) + 1:]
        if len(root) > 0:
            summary += f'{(len(root.split(os.sep)) - 1) * "  "}* [{root}]\n'
        for f in sorted(files, key=summary_sort):
            if len(root) > 0:
                summary += f'{len(root.split(os.sep)) * "  "}* [{f[:-3]}]({os.path.join(root, f)})\n'
            else:
                summary += f'* [{f[:-3]}]({f})\n'
    with open(os.path.join(doc_path, 'SUMMARY.md'), 'w') as f:
        f.write(summary)
    with open(os.path.join(doc_path, 'README.md'), 'w') as f:
        f.write(f'### {os.path.basename(doc_path)}\n')

@click.command()
@click.argument('path', type=click.Path(exists=True))
@click.option('--lang', default=LangEnum.cpp.cli,
              type=click.Choice([LangEnum.cpp.cli, LangEnum.javascript.cli, LangEnum.arkts.cli, LangEnum.rust.cli,LangEnum.typescript.cli], case_sensitive=False),
              help='编程语言')
def main(path, lang):
    path = click.format_filename(path).rstrip(os.sep)
    basename = os.path.basename(path)
    # 移动到工作路径
    if path != os.path.join('resource', basename):
        shutil.copytree(path, os.path.join('resource', basename), dirs_exist_ok=True)
    # 初始化上下文
    ctx = EvaContext(repo=basename, lang=LangEnum.from_cli(lang),
                     doc_path=os.path.join('docs', basename), resource_path=os.path.join('resource', basename),
                     output_path=os.path.join('output', basename))
    # 开始运行
    eva(ctx, LangEnum.from_cli(lang))
    # 生成gitbook输出
    response_with_gitbook(os.path.join('docs', basename))
    # 清扫工作路径
    shutil.rmtree(os.path.join('resource', basename))
    shutil.rmtree(os.path.join('output', basename))


if __name__ == '__main__':
    main()
