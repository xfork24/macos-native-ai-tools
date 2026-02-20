#!/usr/bin/env python3
"""
ASR (Automatic Speech Recognition) Script

Uses MLX Qwen3-ASR-1.7B-8bit model for speech-to-text conversion.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import setup_logger, resolve_path, validate_input_file, ensure_output_dir


def main():
    parser = argparse.ArgumentParser(
        description='Speech-to-text using MLX Qwen3-ASR model',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic transcription
  python src/asr.py input/audio.wav

  # Save result to file
  python src/asr.py input/audio.wav -o output/transcript.txt

  # Specify output directory
  python src/asr.py input/audio.wav --output-dir ./output

  # Use custom model
  python src/asr.py input/audio.wav -m mlx-community/Qwen3-ASR-1.7B-8bit

  # Verbose mode
  python src/asr.py input/audio.wav -v
        '''
    )

    parser.add_argument(
        'input',
        help='Input audio file path'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output text file path (default: print to terminal)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('./output'),
        help='Output directory for transcription files (default: ./output)'
    )

    parser.add_argument(
        '-m', '--model',
        default='mlx-community/Qwen3-ASR-1.7B-8bit',
        help='ASR model name (default: mlx-community/Qwen3-ASR-1.7B-8bit)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup logger
    logger = setup_logger('asr', args.verbose)

    # Resolve input path
    input_path = resolve_path(args.input)

    # Validate input file
    if not validate_input_file(input_path, logger):
        logger.error("Please provide a valid input audio file")
        sys.exit(1)

    # Check output directory
    output_dir = args.output_dir
    if not ensure_output_dir(output_dir, logger):
        logger.error("Failed to ensure output directory")
        sys.exit(1)

    try:
        # Import MLX Audio modules
        from mlx_audio.stt.utils import load_model
        from mlx_audio.stt.generate import generate_transcription

        # Load model
        logger.info(f"Loading ASR model: {args.model}")
        model = load_model(args.model)

        # Perform transcription
        logger.info(f"Transcribing audio: {input_path}")

        # Prepare output path
        # generate_transcription will save to output_path.format (e.g., transcript.txt)
        if args.output:
            # If user specified output file, use its stem (filename without extension)
            output_file = str(args.output.parent / args.output.stem)
            # Ensure the directory exists
            ensure_output_dir(args.output, logger)
        else:
            # Use default output directory
            output_file = str(output_dir / "transcript")

        logger.info(f"Output will be saved to: {output_file}.txt")

        transcription = generate_transcription(
            model=model,
            audio=str(input_path),
            output_path=output_file,
            format="txt",
            verbose=args.verbose
        )

        # Get transcription text from result
        if hasattr(transcription, 'text'):
            result_text = transcription.text
        elif isinstance(transcription, str):
            result_text = transcription
        else:
            result_text = str(transcription)

        # Output result to console
        if result_text:
            actual_output_file = f"{output_file}.txt"
            logger.info(f"Transcription saved to: {actual_output_file}")

            # Always print to console
            print(f"\nTranscription:\n{result_text}")

            # If user specified a different output location, copy there
            if args.output and str(args.output) != actual_output_file:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(result_text)
                logger.info(f"Also saved to: {args.output}")
        else:
            logger.warning("Transcription returned empty result")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except ImportError as e:
        logger.error(f"Failed to import MLX Audio modules: {e}")
        logger.error("Please ensure mlx-audio is installed: pip install mlx-audio==0.3.1")
        sys.exit(1)
    except Exception as e:
        logger.error(f"ASR failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
