from mlx_audio.tts.utils import load_model
from mlx_audio.tts.generate import generate_audio

model = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit")
generate_audio(
    model=model,
    text="""NVIDIA Isaac 生态不仅仅是一套软件，它是具身智能时代的基础设施。
对于开发者： 它是最高效的工具链，能缩短 50% 以上的研发周期。
对于投资者： 它是观察机器人行业技术风向的晴雨表。凡是深度绑定 Isaac 生态的企业，通常意味着选择了高性能、高成本、快迭代的技术路线。
对于行业： 它正在定义具身智能的标准。未来，机器人可能不再区分“品牌”，而是区分“是否兼容 Isaac"。
投资建议： 在评估具身智能企业时，询问其技术栈是否基于 Isaac 是一个有效的尽职调查问题。如果是，说明其技术上限高，但需关注其成本控制能力；如果不是，需评估其自研软件栈的可持续性和兼容性。""",
    file_prefix="test_audio_v5",
    voice='Vivian',
    lang_code='auto',
    instruct='Clear and steady professional narration, focus on accuracy',
    speed=1.5,
    verbose=True
)
