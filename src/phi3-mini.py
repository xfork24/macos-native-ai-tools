from typing import Any, cast
from transformers import AutoModelForCausalLM, AutoTokenizer, PreTrainedTokenizerFast, pipeline

# 1. 加载分词器（双层 cast 彻底屏蔽 PyCharm 的 None 推断虚警）
tokenizer = cast(
    PreTrainedTokenizerFast,
    cast(Any, AutoTokenizer.from_pretrained("microsoft/Phi-3-mini-4k-instruct"))
)

# 2. 加载模型
model = AutoModelForCausalLM.from_pretrained(
    "microsoft/Phi-3-mini-4k-instruct",
    device_map="cpu",
    torch_dtype="auto",
    attn_implementation="eager",
    trust_remote_code=False,
)

# 3. 创建 Pipeline（不混入生成参数）
generator = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    return_full_text=False,
)

prompt = "Write an email apologizing to Sarah for the tragic gardening mishap. Explain how it happened."

# 4. 执行生成（在此处传入 generation_config 与后处理标志）
output = generator(
    prompt,
    max_new_tokens=50,
    generation_config=None,
    clean_up_tokenization_spaces=False,  # 此处传入可彻底屏蔽 BPE Space Warning
)

print(output[0]["generated_text"])