from mlx_audio.tts.utils import load_model
from mlx_audio.tts.generate import generate_audio

model = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit")
generate_audio(
    model=model,
    text="""Oh, by the way, brother, I just remembered something. When I saw the pictures you sent me, one thing mentioned was "intuition." It said that true innovation is often disruptive and, well, intuitive. I've also seen explanations that suggest that many artists and others derive their intuition from somewhere – perhaps from significant breakthroughs in science. Some people argue that it doesn't come from the existing information or data sets, but rather from a kind of infinite consciousness, similar to what we were talking about earlier. It's like the background image,representing the vast universe, and this underlying consciousness directly enters a person's awareness, manifesting as data or a system. That's why it can sometimes seem to come from nowhere.""",
    ref_audio="./ref.wav",
    ref_text="嗯，是可能中间发展的阶段会挺乱的，但是确实好像最终的趋势好像是这样子，毕竟没有差异了。",
    file_prefix="test_audio",
    lang_code='en',
    instruct='A young female voice',
    speed=1.3,
    verbose=True
)

