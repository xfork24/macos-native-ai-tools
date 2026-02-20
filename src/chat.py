#!/usr/bin/env python3
"""
LLM Chat Script

Uses Qwen3:8b model via Ollama for conversational AI.
Supports both single query and interactive chat modes.
"""

import argparse
import sys
import json
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    setup_logger,
    ensure_output_dir,
    OllamaClient
)


def load_context(context_path: Path, logger) -> list:
    """Load conversation history from JSON file."""
    try:
        with open(context_path, 'r', encoding='utf-8') as f:
            context = json.load(f)
        logger.info(f"Loaded conversation context from {context_path}")
        return context
    except Exception as e:
        logger.error(f"Failed to load context file: {e}")
        return []


def save_context(context_path: Path, messages: list, logger):
    """Save conversation history to JSON file."""
    try:
        # Ensure output directory exists
        if not ensure_output_dir(context_path, logger):
            logger.error("Failed to ensure output directory for context file")
            return False

        with open(context_path, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved conversation context to {context_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save context file: {e}")
        return False


def print_message(role: str, content: str):
    """Print a chat message with formatting."""
    if role == 'user':
        print(f"\n{'='*60}")
        print(f"👤 User: {content}")
    elif role == 'assistant':
        print(f"\n{'='*60}")
        print(f"🤖 Assistant: {content}")
    else:
        print(f"\n[{role}]: {content}")


def interactive_mode(client, model: str, initial_context: list, save_context_path: Path, logger):
    """Run interactive chat session."""
    messages = initial_context.copy() if initial_context else []

    print("\n" + "="*60)
    print("🤖 Interactive Chat Mode")
    print("="*60)
    print("Type your message and press Enter to send.")
    print("Commands:")
    print("  /quit  - Exit chat")
    print("  /clear - Clear conversation history")
    print("  /history - Show conversation history")
    print("="*60 + "\n")

    # Print existing context if any
    for msg in messages:
        print_message(msg['role'], msg['content'])

    try:
        while True:
            try:
                user_input = input("\n💬 You: ").strip()

                if not user_input:
                    continue

                # Handle commands
                if user_input.lower() in ['/quit', '/exit', '/q']:
                    print("\n👋 Goodbye!")
                    break

                if user_input.lower() == '/clear':
                    messages = []
                    print("\n✅ Conversation history cleared")
                    continue

                if user_input.lower() == '/history':
                    print("\n📜 Conversation History:")
                    for i, msg in enumerate(messages):
                        print(f"  [{i+1}] {msg['role']}: {msg['content'][:50]}...")
                    continue

                # Add user message
                messages.append({'role': 'user', 'content': user_input})
                print_message('user', user_input)

                # Get assistant response
                logger.info(f"Sending message to model: {model}")
                response = client.chat(model=model, messages=messages)

                if response:
                    messages.append({'role': 'assistant', 'content': response})
                    print_message('assistant', response)

                    # Auto-save context if path provided
                    if save_context_path:
                        save_context(save_context_path, messages, logger)
                else:
                    print("\n⚠️  Assistant returned empty response")

            except EOFError:
                print("\n\n👋 Goodbye!")
                break

    except KeyboardInterrupt:
        print("\n\n👋 Chat interrupted. Goodbye!")

    # Final save
    if save_context_path and messages:
        save_context(save_context_path, messages, logger)


def main():
    parser = argparse.ArgumentParser(
        description='LLM chat using Qwen3 model via Ollama',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Single query
  python src/chat.py "解释什么是机器学习"

  # Interactive mode
  python src/chat.py -i

  # Interactive with initial prompt
  python src/chat.py -i "你好，请介绍一下你自己"

  # Use conversation history
  python src/chat.py "继续刚才的话题" -c history.json -s history.json

  # Interactive with auto-save
  python src/chat.py -i -s chat_history.json
        '''
    )

    parser.add_argument(
        'prompt',
        nargs='?',
        help='Prompt for single query or initial message in interactive mode (optional with -i)'
    )

    parser.add_argument(
        '-i', '--interactive',
        action='store_true',
        help='Interactive chat mode'
    )

    parser.add_argument(
        '-m', '--model',
        default='Qwen3:8b',
        help='Model name (default: Qwen3:8b)'
    )

    parser.add_argument(
        '-c', '--context',
        type=Path,
        help='Load conversation history from JSON file'
    )

    parser.add_argument(
        '-s', '--save-context',
        type=Path,
        metavar='PATH',
        help='Save conversation history to JSON file'
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
    logger = setup_logger('chat', args.verbose)

    # Validate: if no prompt and not interactive, show help
    if not args.prompt and not args.interactive:
        parser.print_help()
        print("\n⚠️  Error: Please provide a prompt or use -i for interactive mode")
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

        # Load context if provided
        messages = []
        if args.context:
            messages = load_context(args.context, logger)

        # Interactive mode
        if args.interactive:
            # Add initial prompt if provided
            if args.prompt:
                messages.append({'role': 'user', 'content': args.prompt})
                print(f"\n👤 Initial prompt: {args.prompt}")

                # Get initial response
                logger.info(f"Calling model: {args.model}")
                response = client.chat(model=args.model, messages=messages)

                if response:
                    messages.append({'role': 'assistant', 'content': response})
                    print(f"\n🤖 Assistant: {response}")
                else:
                    logger.warning("Model returned empty response")

            # Start interactive session
            interactive_mode(client, args.model, messages, args.save_context, logger)

        # Single query mode
        else:
            # Add user message
            messages.append({'role': 'user', 'content': args.prompt})

            # Get response
            logger.info(f"Calling model: {args.model}")
            response = client.chat(model=args.model, messages=messages)

            if response:
                print(f"\n🤖 Response:\n{response}")

                # Update messages with response
                messages.append({'role': 'assistant', 'content': response})

                # Save context if requested
                if args.save_context:
                    save_context(args.save_context, messages, logger)
            else:
                logger.warning("Model returned empty response")
                sys.exit(1)

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
