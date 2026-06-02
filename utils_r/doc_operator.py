""" from core.model import Repository, Module_doc
from typing import List, Optional, Dict, Any

def parse_single_repository_file(file_path) -> Repository:
    """
'''
    将单个 Markdown 文件解析成一个 Repository 对象。
    - 文件中的每个 '### 模块名' 被视为一个模块的开始。
    - '#### Description' 下的内容被视为模块描述。
'''
"""
    repo_name = file_path.stem
    modules: List[Module_doc] = []
    
    content = file_path.read_text(encoding='utf-8')
    
    # 仍然使用 '### ' 作为分隔符来切分不同的模块
    raw_modules = content.split('\n### ')
    
    for raw_module_text in raw_modules[1:]:
        if not raw_module_text.strip():
            continue
        
        # 使用 '#### Description' 作为新的分隔符来分离模块名和描述
        # 使用 split('\n#### Description', 1) 来确保只切分一次
        parts = raw_module_text.split('\n#### Description', 1)
        
        if len(parts) == 2:
            module_name = parts[0].strip()
            # .strip() 可以去除可能存在的前导/尾随空行
            module_description = parts[1].strip()
            
            # 如果Description标题下没有内容，也视为有效，只是描述为空
            if module_name:
                module = Module_doc(
                    name=module_name,
                    description=module_description,
                    example="",
                    functions=[]
                )
                modules.append(module)
        else:
            # 如果一个模块块里没有 '#### Description'，可以选择忽略或记录
            module_name_only = raw_module_text.splitlines()[0].strip()
            print(f"  [!] 警告: 在模块 '{module_name_only}' 中未找到 '#### Description' 标题，已跳过。")
            
    return Repository(repo_name=repo_name, modules=modules) """