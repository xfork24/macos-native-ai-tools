#!/usr/bin/env python3
"""
OCR (Optical Character Recognition) Script

Uses glm-ocr:bf16 model via Ollama to extract text from images.
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
        description='OCR recognition using glm-ocr model via Ollama',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic usage
  python src/ocr.py input/image.jpg

  # Save result to file
  python src/ocr.py input/image.jpg -o output/text.txt

  # Use specific model
  python src/ocr.py input/image.jpg -m glm-ocr:bf16

  # Verbose mode
  python src/ocr.py input/image.jpg -v
        '''
    )

    parser.add_argument(
        'input',
        help='Input image file path'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output text file path (default: print to terminal)'
    )

    parser.add_argument(
        '-m', '--model',
        default='glm-ocr:bf16',
        help='OCR model name (default: glm-ocr:bf16)'
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
    logger = setup_logger('ocr', args.verbose)

    # Resolve input path
    input_path = resolve_path(args.input)

    # Validate input file
    if not validate_input_file(input_path, logger):
        logger.error("Please provide a valid input image file")
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

        # Call Ollama for OCR
        logger.info(f"Calling OCR model: {args.model}")
        prompt = "Please extract all text from this image. Return only the text content without any additional explanation or formatting."

        result = client.generate(
            model=args.model,
            prompt=prompt,
            images=[base64_image]
        )

        # Output result
        if result:
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(result)
                logger.info(f"OCR result saved to: {args.output}")
                print(f"\nExtracted text:\n{result}")
            else:
                print(result)
        else:
            logger.warning("No text extracted from image")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"OCR failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
