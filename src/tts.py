#!/usr/bin/env python3
"""
TTS (Text-to-Speech) Script

Uses MLX Qwen3-TTS models for speech synthesis.
Supports both preset voices (CustomVoice) and voice cloning (Base model).
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import setup_logger, resolve_path, ensure_output_dir, read_input_text


def main():
    parser = argparse.ArgumentParser(
        description='Text-to-speech using MLX Qwen3-TTS model',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Preset voice mode (default)
  python src/tts.py "你好，世界！" -v Vivian -l cn

  # English with preset voice
  python src/tts.py "Hello, world!" -v Aiden -l en -s 1.2

  # From file
  python src/tts.py input.txt -v Vivian -o my_output

  # Voice cloning mode
  python src/tts.py "这是语音克隆测试" --clone --ref-audio ref.wav --ref-text "参考文本"

  # Voice cloning from file
  python src/tts.py input.txt --clone --ref-audio ref.wav --ref-text "参考文本" -o clone_output
        '''
    )

    parser.add_argument(
        'input',
        help='Input text or file path'
    )

    # Preset voice mode options
    parser.add_argument(
        '-v', '--voice',
        default='Vivian',
        help='Preset voice name (default: Vivian). Options: Vivian, Aiden, etc.'
    )

    parser.add_argument(
        '-l', '--lang-code',
        default='auto',
        choices=['auto', 'cn', 'en'],
        help='Language code: cn, en, or auto (default: auto)'
    )

    parser.add_argument(
        '-s', '--speed',
        type=float,
        default=1.3,
        help='Speech speed from 0.5 to 3.0 (default: 1.3)'
    )

    parser.add_argument(
        '--instruct',
        default='Clear and steady professional narration, focus on accuracy',
        help='Synthesis instruction for preset voice mode'
    )

    # Voice cloning mode options
    parser.add_argument(
        '--clone',
        action='store_true',
        help='Enable voice cloning mode (uses Base model instead of CustomVoice)'
    )

    parser.add_argument(
        '--ref-audio',
        type=Path,
        help='Reference audio file path (required for voice cloning mode)'
    )

    parser.add_argument(
        '--ref-text',
        help='Reference text corresponding to reference audio (required for voice cloning mode)'
    )

    # Common options
    parser.add_argument(
        '-o', '--output',
        default='tts_output',
        help='Output file prefix (default: tts_output)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('./output'),
        help='Output directory for audio files (default: ./output)'
    )

    parser.add_argument(
        '-m', '--model',
        help='TTS model name (auto-selected by mode if not specified)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup logger
    logger = setup_logger('tts', args.verbose)

    # Validate voice cloning requirements
    if args.clone:
        if not args.ref_audio:
            logger.error("Voice cloning mode requires --ref-audio")
            sys.exit(1)
        if not args.ref_text:
            logger.error("Voice cloning mode requires --ref-text")
            sys.exit(1)

    # Validate speed
    if not 0.5 <= args.speed <= 3.0:
        logger.error("Speed must be between 0.5 and 3.0")
        sys.exit(1)

    # Parse output argument to extract directory and file prefix
    # The output argument can be:
    # 1. A simple prefix: "my_audio" -> output_dir/my_audio.wav
    # 2. A file with path: "./output/my_audio.wav" -> ./output/my_audio.wav
    # 3. A file with extension: "output.wav" -> output_dir/output.wav
    output_path_obj = Path(args.output)

    if output_path_obj.parent == Path('.') or str(output_path_obj.parent) == '.':
        # Simple prefix like "my_audio" or "output.wav"
        file_prefix = output_path_obj.stem  # Remove extension if present
        output_dir = args.output_dir
    else:
        # Has directory path like "./output/my_audio.wav"
        file_prefix = output_path_obj.stem  # Remove extension if present
        output_dir = output_path_obj.parent

    # Ensure output directory exists
    if not ensure_output_dir(output_dir, logger):
        logger.error("Failed to ensure output directory")
        sys.exit(1)

    logger.info(f"Output will be saved to: {output_dir}/{file_prefix}.wav")

    # Read input text
    input_path = resolve_path(args.input)
    if input_path.is_file():
        logger.info(f"Reading from file: {input_path}")
        text = read_input_text(input_path, logger)
    else:
        # Use input as direct text
        text = str(args.input)

    if not text or not text.strip():
        logger.error("Input text is empty")
        sys.exit(1)

    # Determine model
    if args.model:
        model_name = args.model
    elif args.clone:
        model_name = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
    else:
        model_name = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"

    try:
        # Import MLX Audio modules
        from mlx_audio.tts.utils import load_model
        from mlx_audio.tts.generate import generate_audio

        # Load model
        logger.info(f"Loading TTS model: {model_name}")
        model = load_model(model_name)

        # Validate reference audio for voice cloning
        if args.clone:
            ref_audio_path = resolve_path(str(args.ref_audio))
            if not ref_audio_path.exists():
                logger.error(f"Reference audio file not found: {ref_audio_path}")
                sys.exit(1)

        # Prepare generation parameters
        common_params = {
            'model': model,
            'text': text,
            'output_path': str(output_dir),
            'file_prefix': file_prefix,
            'speed': args.speed,  # Apply speed to both modes
            'join_audio': True,  # Merge all audio segments into one file (no _000 suffix)
            'verbose': args.verbose
        }

        if args.clone:
            # Voice cloning mode
            logger.info("Using voice cloning mode")
            logger.info(f"Reference audio: {args.ref_audio}")
            logger.info(f"Reference text: {args.ref_text[:50]}..." if len(args.ref_text) > 50 else f"Reference text: {args.ref_text}")
            logger.info(f"Speed: {args.speed}")

            common_params.update({
                'ref_audio': str(ref_audio_path),
                'ref_text': args.ref_text
            })
        else:
            # Preset voice mode
            logger.info("Using preset voice mode")
            logger.info(f"Voice: {args.voice}")
            logger.info(f"Language: {args.lang_code}")
            logger.info(f"Speed: {args.speed}")

            common_params.update({
                'voice': args.voice,
                'lang_code': args.lang_code,
                'instruct': args.instruct
            })

        # Generate audio
        logger.info(f"Generating speech from text ({len(text)} characters)")
        generate_audio(**common_params)

        actual_output_file = output_dir / f"{file_prefix}.wav"
        logger.info(f"Audio generated successfully: {actual_output_file}")
        print(f"\n✅ TTS complete! Output saved to: {actual_output_file}")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except ImportError as e:
        logger.error(f"Failed to import MLX Audio modules: {e}")
        logger.error("Please ensure mlx-audio is installed: pip install mlx-audio==0.3.1")
        sys.exit(1)
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
