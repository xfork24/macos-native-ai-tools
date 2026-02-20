#!/usr/bin/env python3
"""
Translation Script

Uses translategemma:12b model via Ollama for Chinese-English translation.
Supports auto language detection.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    setup_logger,
    resolve_path,
    ensure_output_dir,
    OllamaClient,
    read_input_text,
    detect_chinese_text
)


def main():
    parser = argparse.ArgumentParser(
        description='Translation using translategemma model via Ollama',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Translate text (auto-detect target language)
  python src/translate.py "Hello, world!"

  # Translate file
  python src/translate.py input.txt -o output.txt

  # Specify target language
  python src/translate.py "Hello" --target zh

  # Translate Chinese to English
  python src/translate.py "你好，世界！" --target en
        '''
    )

    parser.add_argument(
        'input',
        help='Input text or file path'
    )

    parser.add_argument(
        '-o', '--output',
        type=Path,
        help='Output text file path (default: print to terminal)'
    )

    parser.add_argument(
        '-t', '--target',
        default='auto',
        choices=['auto', 'zh', 'en'],
        help='Target language: zh (Chinese), en (English), auto (default: auto)'
    )

    parser.add_argument(
        '-m', '--model',
        default='translategemma:12b',
        help='Translation model name (default: translategemma:12b)'
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
    logger = setup_logger('translate', args.verbose)

    # Check output directory if specified
    if args.output:
        if not ensure_output_dir(args.output, logger):
            logger.error("Failed to ensure output directory")
            sys.exit(1)

    try:
        # Read input (from file or as direct text)
        # Check if input is a file path first
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

        # Determine source and target languages
        has_chinese = detect_chinese_text(text)

        if args.target == 'auto':
            # Auto-detect: if Chinese detected, translate to English, otherwise to Chinese
            target_lang = 'en' if has_chinese else 'zh'
            logger.info(f"Auto-detected source language, translating to: {target_lang}")
        else:
            target_lang = args.target
            logger.info(f"Translating to: {target_lang}")

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

        # Initialize Ollama client
        client = OllamaClient(
            host=args.host,
            timeout=args.timeout,
            logger=logger
        )

        # Check model availability
        if not client.check_model(args.model):
            logger.warning(f"Model '{args.model}' may not be available. Attempting anyway...")

        # Perform translation
        logger.info(f"Calling translation model: {args.model}")
        result = client.generate(
            model=args.model,
            prompt=prompt
        )

        # Output result
        if result:
            if args.output:
                with open(args.output, 'w', encoding='utf-8') as f:
                    f.write(result.strip())
                logger.info(f"Translation saved to: {args.output}")
                print(f"\nTranslation:\n{result.strip()}")
            else:
                print(result.strip())
        else:
            logger.warning("Translation returned empty result")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
