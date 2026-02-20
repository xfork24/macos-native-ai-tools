import torch
from diffusers import StableDiffusionPipeline

# 1. 指定你本地 1.5GB 精简版文件的绝对路径
local_model_path = "./v1-5-pruned-emaonly-fp16.safetensors"

# 2. 使用 from_single_file 方法加载
# load_safety_checker=False: 进一步节省显存，不加载安全检查器（图片审查模型）
pipe = StableDiffusionPipeline.from_single_file(
    local_model_path,
    torch_dtype=torch.float16,
    load_safety_checker=False,
    safety_checker=None,
    feature_extractor=None,
    requires_safety_checker=False,
)

# 显式移除（如果依然自动生成了该属性）
pipe.safety_checker = None

# 3. 移动到 Mac M1 的 GPU (MPS)
pipe.to("mps")

# 4. 生成图片测试
prompt = "a serene mountain landscape, high resolution, digital art"
image = pipe(prompt, num_inference_steps=20).images[0]

# 5. 保存
image.save("./local_model_test.png")
print("加载本地精简版模型生成成功！")
