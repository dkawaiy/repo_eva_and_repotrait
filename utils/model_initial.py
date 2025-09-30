from sentence_transformers import SentenceTransformer

# 缓存模型对象
models = {}

def get_model(model_name: str):
    if model_name not in models:
        models[model_name] = SentenceTransformer(model_name)
    return models[model_name]

# 加载两个模型并缓存
mini_lm_model = get_model("all-MiniLM-L6-v2")
multilingual_model = get_model("paraphrase-multilingual-MiniLM-L12-v2")
