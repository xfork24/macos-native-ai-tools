#!/usr/bin/env python3
"""
Whisper Speech Translation Script (Chinese -> English)

Uses mlx-whisper (MLX port of OpenAI Whisper) with task="translate",
which translates the spoken language directly into English text.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import setup_logger, resolve_path, validate_input_file, ensure_output_dir


def main():
    parser = argparse.ArgumentParser(
        description='Speech translation (Chinese -> English) using mlx-whisper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic translation (prints English text to terminal)
  python src/whisper_translate.py input/audio.wav

  # Save translation to file
  python src/whisper_translate.py input/audio.wav -o output/translation.txt

  # Use a larger model for better quality (downloads on first use)
  python src/whisper_translate.py input/audio.wav -m mlx-community/whisper-turbo

  # Verbose mode (shows segment-level progress)
  python src/whisper_translate.py input/audio.wav -v
        '''
    )

    parser.add_argument(
        'input',
        help='Input audio file path (Chinese speech)'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output text file path (default: print to terminal)'
    )

    parser.add_argument(
        '-m', '--model',
        default='mlx-community/whisper-small-mlx',
        help='Whisper MLX model name or local path (default: mlx-community/whisper-small-mlx)'
    )

    parser.add_argument(
        '-l', '--language',
        default='zh',
        help='Source spoken language code (default: zh)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup logger
    logger = setup_logger('whisper_translate', args.verbose)

    # Resolve and validate input path
    input_path = resolve_path(args.input)
    if not validate_input_file(input_path, logger):
        logger.error("Please provide a valid input audio file")
        sys.exit(1)

    # Ensure output directory exists when an output file is specified
    if args.output and not ensure_output_dir(args.output.parent, logger):
        logger.error("Failed to ensure output directory")
        sys.exit(1)

    try:
        import mlx_whisper

        logger.info(f"Loading Whisper model: {args.model}")
        logger.info(f"Translating audio ({args.language} -> en): {input_path}")

        result = mlx_whisper.transcribe(
            str(input_path),
            path_or_hf_repo=args.model,
            language=args.language,
            task='translate',  # Whisper's translate task always targets English
            verbose=args.verbose,
        )

        # mlx-whisper returns a dict: {"text": ..., "segments": [...], "language": ...}
        translated_text = (result.get('text') or '').strip()
        if not translated_text:
            logger.warning("Translation returned empty result")
            sys.exit(1)

        # Print segment-level detail (timestamps) in verbose mode
        if args.verbose:
            for segment in result.get('segments', []):
                print(f"[{segment['start']:7.2f}s -> {segment['end']:7.2f}s] {segment['text'].strip()}")

        print(f"\nEnglish translation:\n{translated_text}")

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(translated_text)
            logger.info(f"Translation saved to: {args.output}")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except ImportError as e:
        logger.error(f"Failed to import mlx_whisper: {e}")
        logger.error("Please ensure mlx-whisper is installed: pip install mlx-whisper")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
