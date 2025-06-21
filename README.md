## RepoHCL

借助LLM理解软件，为项目中的每个源代码文件生成文档

### 工作流程

- 为软件生成AST
- 基于AST解析源代码文件中包含的类与函数，并生成Function/Class CallGraph
- 按Function CallGraph的逆拓扑排序，为各个函数生成文档
- 按Class CallGraph的逆拓扑排序，为各个类生成文档
- 基于函数文档生成模块文档
- 基于模块文档生成仓库文档
- 对比不同软件的文档

### 项目结构

```
├── docker                      # docker封装的项目demo
│    ├── base.dockerfile        # 项目环境基础镜像
│    ├── cmd.dockerfile         # main.py的docker运行环境，命令行执行工具
│    └── service.dockerfile     # service.py的docker运行环境，启动服务端
├── test                        # docker封装的项目测试用例
│    ├── cpp                    # C/C++项目测试用例
│    ├── run.sh                 # 将测试用例复制到本地的脚本   
│    └── run.bat                # 将测试用例复制到本地的脚本(Windows)                 
├── 'resource'                  # 待分析项目源代码目录(运行时生成)
├── 'docs'                      # 生成的文档目录(运行时生成)
├── 'output'                    # 项目分析中间产物目录(运行时生成)
├── 'logs'                      # 日志目录(运行时生成)
├── api                         # Web服务API实现
│    ├── compare.py             # 软件对比API
│    ├── eva.py                 # 软件理解API
│    └──  vo.py                 # 请求/响应对象
├── metrics                     # 各个理解指标实现
│    ├── metric.py              # 理解基类及上下文
│    ├── doc.py                 # 理解结果文档对象
│    ├── parser.py              # C/C++软件解析
│    ├── js_parser.py           # JavaScript软件解析
│    ├── structure.py           # 目录结构理解
│    ├── function.py            # 函数级别理解（V1，单轮生成）
│    ├── function_v2.py         # 函数级别理解（V2，两轮生成）
│    ├── clazz.py               # 类级别理解
│    ├── module.py              # 模块级别理解（V1，LLM聚类）
│    ├── module_v2.py           # 模块级别理解（V2，本地聚类+LLM合并）
│    ├── module_v3.py           # 模块级别理解（V3，本地社区发现+LLM合并）
│    ├── module_v4.py           # 模块级别理解（V4，按文件分类）
│    ├── repo.py                # 仓库级别理解（V1，Agent Tools回答问题）
│    └── repo_v2.py             # 仓库级别理解（V2，RAG回答问题）
├── utils
│    ├── common.py              # 公共工具类库
│    ├── file_helper.py         # 压缩文件工具类库
│    ├── llm_helper.py          # LLM工具类库
│    ├── multi_task_dispatch.py # 多线程任务分发器
│    ├── rag_helper.py          # RAG工具类库
│    └── settings.py            # 配置类
├── .env                        # 配置文件/环境变量
├── main.py                     # 命令行入口
├── service.py                  # web服务入口
├── requirements.txt            # Python依赖管理
└── README.md                  
```

### 使用说明

- 项目基于OpenAI协议调用LLM，需在.env中设置调用的LLM服务的域名`OPENAI_BASE_URL`、模型`MODEL`、温度`MODEL_TEMPERATURE`、输出语言
  `MODEL_LANGUAGE`，并配置`OPENAI_API_KEY`作为密钥。默认采用阿里百炼的qwen-plus。
- 本地运行环境可参考`docker/base.dockerfile`。
- RepoMetricV2使用到HuggingFace拉取远端模型，若网络不佳，可在.env中设置`HF_ENDPOINT=https://hf-mirror.com`。
- 在.env中设置`LOG_LEVEL`可以控制日志的输出级别，默认`INFO`级别。
- 在.env中设置`THREADS`可以控制多线程的数量，默认`32`。
- Windows下运行，建议使用UTF-8模式，例如：`python3 -X utf-8`

### TODO

- 功能优化
  - 提高文档质量，增加对生成结果准确性的评估
  - 增加对其他语言的支持：RUST、Java、JavaScript
  - 补充测试用例
    - 测试test目录下所有软件的中间文件的生成
    - 以md5为例，测试整个理解流程是否正常运行
- 性能优化
  - 优化对大规模库的处理速度，可以考虑删除软件中使用的词嵌入模型，使用单独服务部署
  - 优化对大规模库的内存占用，目前对大规模库(>1w)函数，joern解析内存溢出问题，且软件占用内存可达3GB
  - 优化镜像大小，目前镜像过大，达7GB
- BUG修复
  - 增加未检测到API时的兜底策略
  - 生成模块文档时，LLM返回函数编号没有按行分隔，导致解析错误
  - 生成函数/类文档时，引用的函数文档过多，造成上下文超长
  - 总结模块文档时，模块内API过多，API文档全部纳入上下文造成上下文超长
  - windows下joern的脚本参数传递不兼容
  - LLM生成文档时，小概率将Markdown标题翻译为中文，导致Markdown解析错误，需要做Schema校验

### Docker运行详细步骤

#### 环境准备

```bash
docker pull applerodite/repohcl-base
# 或手工构建基础镜像
docker build -f docker/base.dockerfile -t applerodite/repohcl-base .
```

#### 命令行运行

```bash
docker pull applerodite/repohcl-cmd
# 或手工构建命令行镜像
docker build -f docker/cmd.dockerfile -t applerodite/repohcl-cmd .

# （可选）获得测试目录下的软件源代码
test/run.sh test/cpp/md5.dockerfile

# 分析本地的项目源代码（默认C/C++语言，--lang参数可选：cpp、js），需配置OPENAI_API_KEY环境变量，生成文档至docs目录，
docker run --rm -v $(pwd)/resource/md5:/app -v $(pwd)/docs:/root/docs -e OPENAI_API_KEY=xxx applerodite/repohcl-cmd /app --lang cpp
```

#### 服务端运行

```bash
docker pull applerodite/repohcl
# 或手工构建服务端镜像
docker build -f docker/service.dockerfile -t applerodite/repohcl .

# （可选）获得测试目录下的软件源代码
test/run.sh test/cpp/md5.dockerfile
zip -r resource/md5.zip resource/md5

# 启动服务端，需配置OPENAI_API_KEY环境变量
docker run -p 31000:31000 -d -v $(pwd)/resource/md5.zip:/app.zip -e OPENAI_API_KEY=xxx applerodite/repohcl

# 测试服务端
curl -X POST http://127.0.0.1:31000/tools/hcl \
-H "Content-Type: application/json" \
-d '{
  "id": "1",
  "name": "md5",
  "repo": "file:///app.zip",
  "callback": "http://127.0.0.1:31000/tools/callback",
  "language": "C/C++"
}'

```

**API列表**

| API         | 类型   | 说明   | 入参                                                                                                              | 出参                                                                             |
|-------------|------|------|-----------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| /tools/hcl  | POST | 软件理解 | id,str,请求ID<br/>name,str,软件名称<br/>repo,str,源代码路径<br/>callback,str,回调链接<br/>language,str,软件语言(C/C++, JavaScript) | id,str,原样回传请求ID<br/>status,int,理解状态<br/>message,str,理解报错信息<br/>result,str,理解结果 |
| /tools/comp | POST | 软件对比 | requestId,str,请求ID<br/>names,List[str],参与对比的软件名称<br/>results,List[str],参与对比的软件理解结果<br/>callback,str             | id,str,原样回传请求ID<br/>status,int,对比状态<br/>message,str,对比报错信息<br/>result,str,对比结果 |                                                                    |

**请求示例**

```json lines

{
  "id": "2",
  "name": "test",
  "repo": "https://path_to_source_code.zip",
  "callback": "http://127.0.0.1:31000/tools/callback",
  "language": "JavaScript"
}

{
  "id": "3",
  "names": [
    "repo1",
    "repo2"
  ],
  "results": [
    "result1",
    "result2"
  ]
}

```