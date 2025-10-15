import os

from loguru import logger


from .metric import Metric
from utils import SimpleLLM, ChatCompletionSettings

# 生成软件文件结构
class StructureMetric(Metric):
    def eva(self, ctx):
        structure = self._traverse(ctx.resource_path)
        logger.info('[StructureMetric] origin structure of {} is \n{}'.format(ctx.resource_path, '\n'.join(structure)))
        structure = self._trim(structure)
        logger.info(f'[StructureMetric] trim structure of {ctx.resource_path} is \n{structure}')
        ctx.structure = structure

    # 递归遍历目录结构
    def _traverse(self, root, prefix="") -> [str]:
        children = []
        new_prefix = prefix + ' ' * 2
        for name in sorted(os.listdir(root)):
            if name.startswith('.'):  # 忽略隐藏文件和目录
                continue
            path = os.path.join(root, name)
            if os.path.isdir(path):
                children.extend(self._traverse(path, new_prefix))
            elif os.path.isfile(path) and (
                    path.endswith('.c') or
                    path.endswith('.cpp') or
                    path.endswith('.h') or
                    path.endswith('.hpp') or
                    path.endswith('.rs') ):
                children.append(new_prefix + name)
        if len(children) > 0:
            children = [prefix + os.path.basename(root), *children]
        return children

    # 委托LLM精简目录结构，排除文档、测试类型的代码文件
    @staticmethod
    def _trim(structure: str) -> str:
        llm = SimpleLLM(ChatCompletionSettings())
        return llm.add_system_msg(structure_trimmer).add_user_msg('\n'.join(structure)).ask().strip()


structure_trimmer = '''
You are a professional code base organization assistant. 
Your task is to help users clean up the directory structure of the C/C++ project repository and remove paths that are not related to business code.
You need to understand the common folders and files of C/C++ projects, 
and be able to identify which are the business code files to implement core functions 
and which are non-business code files such as codes for documents and tests. 
According to the project structure provided by the user, you retain the necessary paths and return. 
Please keep the input and output formats consistent and return the modified results directly without explaining the reasons.
The directory structure provided by the user is indicated by a 2-space indentation, 
for example: the user gives the following project structure

my_cpp_project
  doc 
    doc.cpp        
  include
    my_cpp_project
      my_class.h
  src
    main.cpp
    my_class.cpp
    test_main.cpp
  test
    test_main.cpp
    test_my_class.cpp
    
You should think like this:
(1) The .c files under doc and test seem to be used for documentation and testing, not business code, so they should be excluded. 
(2) Although test_main.cpp is in the src directory, it is also used for testing based on its name, so it should be excluded.

Note:
- Never output anything except the directory structure.

You should output as follows:

my_cpp_project         
  include
    my_cpp_project
    my_class.h
  src
    main.cpp
    my_class.cpp
'''
