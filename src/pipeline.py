#!/usr/bin/env python3
"""
AI Pipeline Script - OCR + Translation + TTS

A complete pipeline that:
1. Extracts text from an image using OCR
2. Translates the text to target language
3. Generates audio using voice cloning

Usage:
    python src/pipeline.py input/image.png --ref-audio input/voice.wav --ref-text input/voice-ref.wav --output output/audio.wav
"""

import argparse
import sys
import tempfile
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    setup_logger,
    resolve_path,
    validate_input_file,
    ensure_output_dir,
    image_to_base64,
    OllamaClient,
    read_input_text,
    detect_chinese_text
)


def ocr_extract_text(image_path: Path, ocr_model: str, host: str, timeout: int, logger) -> str:
    """Extract text from image using OCR."""
    from src.ocr import main as ocr_main
    import io
    import contextlib

    logger.info(f"Step 1/3: OCR - Extracting text from {image_path}")

    # Import required modules for OCR
    from src.utils import OllamaClient

    client = OllamaClient(host=host, timeout=timeout, logger=logger)
    base64_image = image_to_base64(image_path, logger)

    if not base64_image:
        raise ValueError("Failed to process image")

    prompt = "Please extract all text from this image. Return only the text content without any additional explanation or formatting."

    result = client.generate(
        model=ocr_model,
        prompt=prompt,
        images=[base64_image]
    )

    if not result:
        raise ValueError("OCR returned empty result")

    logger.info(f"✅ OCR extracted {len(result)} characters")
    return result


def translate_text(text: str, target_lang: str, translate_model: str, host: str, timeout: int, logger) -> str:
    """Translate text to target language."""
    logger.info(f"Step 2/3: Translation - Translating to {target_lang}")

    client = OllamaClient(host=host, timeout=timeout, logger=logger)

    # Build translation prompt
    if target_lang == 'zh':
        # English to Chinese
        prompt = f"""You are a professional English (en) to Chinese (zh-Hans) translator. Your goal is to accurately convey the meaning and nuances of the original English text while adhering to Chinese grammar, vocabulary, and cultural sensitivities.
Produce only the Chinese translation, without any additional explanations or commentary. Please translate the following English text into Chinese:

{text}"""
    else:
        # Chinese to English
        prompt = f"""You are a professional Chinese (zh-Hans) to English (en) translator. Your goal is to accurately convey the meaning and nuances of the original Chinese text while adhering to English grammar, vocabulary, and cultural sensitivities.
Produce only the English translation, without any additional explanations or commentary. Please translate the following Chinese text into English:

{text}"""

    result = client.generate(
        model=translate_model,
        prompt=prompt
    )

    if not result:
        raise ValueError("Translation returned empty result")

    logger.info(f"✅ Translation completed ({len(result)} characters)")
    return result


def generate_tts_audio(text: str, ref_audio: Path, ref_text: str, output_file: Path,
                       voice: str, lang_code: str, speed: float, model: str, logger):
    """Generate audio using TTS with voice cloning."""
    from src.tts import main as tts_main

    logger.info(f"Step 3/3: TTS - Generating audio with voice cloning")

    # Import MLX Audio modules
    from mlx_audio.tts.utils import load_model
    from mlx_audio.tts.generate import generate_audio

    # Determine model
    if model:
        model_name = model
    else:
        model_name = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"

    # Load model
    logger.info(f"Loading TTS model: {model_name}")
    tts_model = load_model(model_name)

    # Prepare output path
    output_dir = output_file.parent
    file_prefix = output_file.stem

    # Ensure output directory exists
    if not ensure_output_dir(output_dir, logger):
        raise RuntimeError(f"Failed to ensure output directory: {output_dir}")

    logger.info(f"Reference audio: {ref_audio}")
    logger.info(f"Output will be saved to: {output_dir}/{file_prefix}.wav")

    # Generate audio with voice cloning
    generate_audio(
        model=tts_model,
        text=text,
        ref_audio=str(ref_audio),
        ref_text=ref_text,
        output_path=str(output_dir),
        file_prefix=file_prefix,
        speed=speed,
        join_audio=True,
        verbose=logger.isEnabledFor(10)  # DEBUG level
    )

    actual_output_file = output_dir / f"{file_prefix}.wav"
    logger.info(f"✅ Audio generated successfully: {actual_output_file}")


def main():
    parser = argparse.ArgumentParser(
        description='AI Pipeline: OCR + Translation + TTS',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Complete pipeline with voice cloning
  python src/pipeline.py input/gemini31.png \\
    --ref-audio input/myvoice.wav \\
    --ref-text input/myvoice-ref.txt \\
    --output output/gemini31.wav

  # Translate to English instead
  python src/pipeline.py input/image.png \\
    --ref-audio input/voice.wav \\
    --ref-text input/voice-ref.txt \\
    --output output/audio.wav \\
    --target en

  # Use preset voice instead of cloning
  python src/pipeline.py input/image.png \\
    --voice Vivian \\
    --output output/audio.wav \\
    --skip-ocr
        '''
    )

    parser.add_argument(
        'input',
        type=Path,
        help='Input image file path'
    )

    parser.add_argument(
        '--ref-audio',
        type=Path,
        help='Reference audio file for voice cloning (required for clone mode)'
    )

    parser.add_argument(
        '--ref-text',
        help='Reference text for voice cloning (path to file or direct text)'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        required=True,
        help='Output audio file path'
    )

    parser.add_argument(
        '--target',
        default='zh',
        choices=['zh', 'en'],
        help='Target translation language (default: zh)'
    )

    parser.add_argument(
        '--skip-ocr',
        action='store_true',
        help='Skip OCR, use input as text file instead'
    )

    parser.add_argument(
        '--skip-translate',
        action='store_true',
        help='Skip translation, use original text'
    )

    # OCR options
    parser.add_argument(
        '--ocr-model',
        default='glm-ocr:bf16',
        help='OCR model name (default: glm-ocr:bf16)'
    )

    # Translation options
    parser.add_argument(
        '--translate-model',
        default='translategemma:12b',
        help='Translation model name (default: translategemma:12b)'
    )

    # TTS options
    parser.add_argument(
        '--voice',
        help='Use preset voice instead of cloning (e.g., Vivian, Aiden)'
    )

    parser.add_argument(
        '--lang-code',
        default='auto',
        choices=['auto', 'cn', 'en'],
        help='TTS language code (default: auto)'
    )

    parser.add_argument(
        '-s', '--speed',
        type=float,
        default=1.3,
        help='TTS speech speed from 0.5 to 3.0 (default: 1.3)'
    )

    parser.add_argument(
        '--tts-model',
        help='TTS model name (auto-selected by mode if not specified)'
    )

    # Common options
    parser.add_argument(
        '--host',
        default='http://localhost:11434',
        help='Ollama service host (default: http://localhost:11434)'
    )

    parser.add_argument(
        '--timeout',
        type=int,
        default=120,
        help='Request timeout in seconds (default: 120)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup logger
    logger = setup_logger('pipeline', args.verbose)

    print("\n" + "=" * 70)
    print("🚀 AI Pipeline: OCR + Translation + TTS")
    print("=" * 70)

    try:
        # Step 1: OCR (optional)
        if args.skip_ocr:
            logger.info("Skipping OCR, reading input as text file")
            input_path = resolve_path(str(args.input))
            if not input_path.exists():
                logger.error(f"Input file not found: {input_path}")
                sys.exit(1)
            text = read_input_text(input_path, logger)
        else:
            input_path = resolve_path(str(args.input))
            if not validate_input_file(input_path, logger):
                logger.error("Please provide a valid input image file")
                sys.exit(1)
            text = ocr_extract_text(
                input_path,
                args.ocr_model,
                args.host,
                args.timeout,
                logger
            )

        print(f"\n📝 Extracted Text:")
        print("-" * 70)
        print(text)
        print("-" * 70)

        # Step 2: Translation (optional)
        if args.skip_translate:
            logger.info("Skipping translation, using original text")
            translated_text = text
        else:
            translated_text = translate_text(
                text,
                args.target,
                args.translate_model,
                args.host,
                args.timeout,
                logger
            )

        print(f"\n🌐 Translated Text ({args.target}):")
        print("-" * 70)
        print(translated_text)
        print("-" * 70)

        # Step 3: TTS
        if args.voice:
            # Use preset voice
            logger.info(f"Using preset voice mode: {args.voice}")

            from mlx_audio.tts.utils import load_model
            from mlx_audio.tts.generate import generate_audio

            model_name = args.tts_model if args.tts_model else "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"

            logger.info(f"Loading TTS model: {model_name}")
            tts_model = load_model(model_name)

            output_dir = args.output.parent
            file_prefix = args.output.stem

            if not ensure_output_dir(output_dir, logger):
                raise RuntimeError(f"Failed to ensure output directory: {output_dir}")

            logger.info(f"Output will be saved to: {output_dir}/{file_prefix}.wav")

            generate_audio(
                model=tts_model,
                text=translated_text,
                voice=args.voice,
                lang_code=args.lang_code,
                speed=args.speed,
                output_path=str(output_dir),
                file_prefix=file_prefix,
                join_audio=True,
                verbose=args.verbose
            )

            actual_output_file = output_dir / f"{file_prefix}.wav"
        else:
            # Use voice cloning
            if not args.ref_audio:
                logger.error("--ref-audio is required for voice cloning mode")
                sys.exit(1)
            if not args.ref_text:
                logger.error("--ref-text is required for voice cloning mode")
                sys.exit(1)

            # Resolve ref_text
            ref_text_path = resolve_path(args.ref_text)
            if ref_text_path.is_file():
                ref_text_content = read_input_text(ref_text_path, logger)
            else:
                ref_text_content = args.ref_text

            ref_audio_path = resolve_path(str(args.ref_audio))
            if not ref_audio_path.exists():
                logger.error(f"Reference audio file not found: {ref_audio_path}")
                sys.exit(1)

            generate_tts_audio(
                translated_text,
                ref_audio_path,
                ref_text_content,
                args.output,
                args.voice if args.voice else 'Vivian',
                args.lang_code,
                args.speed,
                args.tts_model if args.tts_model else None,
                logger
            )
            actual_output_file = args.output

        print(f"\n✅ Pipeline completed successfully!")
        print(f"📁 Output file: {actual_output_file}")
        print("=" * 70)

    except KeyboardInterrupt:
        logger.info("\nPipeline cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
