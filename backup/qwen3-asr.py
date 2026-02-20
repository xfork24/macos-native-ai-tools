from mlx_audio.stt.utils import load_model
from mlx_audio.stt.generate import generate_transcription

model = load_model("mlx-community/Qwen3-ASR-1.7B-8bit")
transcription = generate_transcription(
    model=model,
    audio_path="input.wav",
    output_path="output",
    format="txt",
    verbose=True,
)
print(transcription.text)

