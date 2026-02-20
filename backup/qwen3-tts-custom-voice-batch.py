from mlx_audio.tts.utils import load_model
from mlx_audio.tts.generate import generate_audio
import linecache

model = load_model("mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit")

lines = linecache.getlines('./固定搭配_processed.md')
idx = 0
for line in lines:
    line = line.strip()
    if line == '':
        continue
    idx += 1
    if line[:3] == 'cn/':
        generate_audio(
            model=model,
            text=line[3:],
            file_prefix=f"{idx}_",
            voice='vivian',
            lang_code='cn',
            output_path='./output',
            instruct='Clear and steady professional narration, focus on accuracy',
            speed=1.4,
            verbose=True
        )
    else:
        generate_audio(
            model=model,
            text=line[3:],
            file_prefix=f"{idx}_",
            voice='aiden',
            lang_code='en',
            output_path='./output',
            instruct='Clear and steady professional narration, focus on accuracy',
            speed=1.4,
            verbose=True
        )

