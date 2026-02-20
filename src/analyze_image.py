#!/usr/bin/env python3
"""
Image Analysis Script

Uses qwen3-vl:8b model via Ollama for visual understanding and analysis.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    setup_logger,
    resolve_path,
    validate_input_file,
    ensure_output_dir,
    image_to_base64,
    OllamaClient
)


def main():
    parser = argparse.ArgumentParser(
        description='Image analysis using qwen3-vl model via Ollama',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic description
  python src/analyze_image.py input/image.jpg -p "描述这张图片"

  # Ask specific questions
  python src/analyze_image.py input/image.jpg -p "图片中有几个人？"

  # Save result to file
  python src/analyze_image.py input/image.jpg -p "详细分析这张图片的内容" -o result.txt

  # Use English prompt
  python src/analyze_image.py input/image.jpg -p "Describe what you see in this image"
        '''
    )

    parser.add_argument(
        'input',
        help='Input image file path'
    )

    parser.add_argument(
        '-p', '--prompt',
        required=True,
        help='Analysis prompt (required)'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output result file path (default: print to terminal)'
    )

    parser.add_argument(
        '-m', '--model',
        default='qwen3-vl:8b',
        help='Vision model name (default: qwen3-vl:8b)'
    )

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
    logger = setup_logger('analyze_image', args.verbose)

    # Resolve input path
    input_path = resolve_path(args.input)

    # Validate input file
    if not validate_input_file(input_path, logger):
        logger.error("Please provide a valid input image file")
        sys.exit(1)

    # Validate prompt
    if not args.prompt or not args.prompt.strip():
        logger.error("Please provide a valid prompt using -p/--prompt")
        sys.exit(1)

    # Check output directory if specified
    if args.output:
        if not ensure_output_dir(args.output, logger):
            logger.error("Failed to ensure output directory")
            sys.exit(1)

    try:
        # Initialize Ollama client
        client = OllamaClient(
            host=args.host,
            timeout=args.timeout,
            logger=logger
        )

        # Check model availability
        if not client.check_model(args.model):
            logger.warning(f"Model '{args.model}' may not be available. Attempting anyway...")

        # Convert image to base64
        logger.info(f"Processing image: {input_path}")
        base64_image = image_to_base64(input_path, logger)

        if not base64_image:
            logger.error("Failed to process image")
            sys.exit(1)

        # Call Ollama for image analysis
        logger.info(f"Calling vision model: {args.model}")
        logger.info(f"Prompt: {args.prompt}")

        result = client.generate(
            model=args.model,
            prompt=args.prompt,
            images=[base64_image]
        )

        # Output result
        if result:
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(result)
                logger.info(f"Analysis result saved to: {args.output}")
                print(f"\nAnalysis:\n{result}")
            else:
                print(result)
        else:
            logger.warning("Analysis returned empty result")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
