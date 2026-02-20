"""
Common utilities module for MLX AI Tools.

Provides logging configuration, path handling, and Ollama client wrapper.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
import base64
from io import BytesIO

import ollama
from PIL import Image


def setup_logger(name: str, verbose: bool = False) -> logging.Logger:
    """
    Configure and return a logger instance.

    Args:
        name: Logger name (usually __name__)
        verbose: Enable DEBUG level logging

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    if verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logger.level)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Add handler if not already present
    if not logger.handlers:
        logger.addHandler(handler)

    return logger


def resolve_path(path_str: str) -> Path:
    """
    Resolve a path string to a Path object.
    Expands user directory and converts to absolute path.

    Args:
        path_str: Path string

    Returns:
        Resolved Path object
    """
    return Path(path_str).expanduser().resolve()


def validate_input_file(file_path: Path, logger: Optional[logging.Logger] = None) -> bool:
    """
    Validate that an input file exists and is readable.

    Args:
        file_path: Path to validate
        logger: Optional logger for error messages

    Returns:
        True if valid, False otherwise
    """
    if not file_path.exists():
        if logger:
            logger.error(f"File not found: {file_path}")
        return False

    if not file_path.is_file():
        if logger:
            logger.error(f"Path is not a file: {file_path}")
        return False

    return True


def ensure_output_dir(output_path: Path, logger: Optional[logging.Logger] = None) -> bool:
    """
    Ensure the output directory exists, creating it if necessary.

    Args:
        output_path: Path to output file or directory
        logger: Optional logger for info messages

    Returns:
        True if directory exists or was created successfully
    """
    # If path exists and is a directory, use it directly
    # Otherwise, extract the parent directory (for file paths or non-existent paths)
    if output_path.exists() and output_path.is_dir():
        dir_path = output_path
    else:
        # Extract parent directory for file paths or non-existent paths
        dir_path = output_path.parent

    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        if logger:
            logger.debug(f"Output directory ensured: {dir_path}")
        return True
    except Exception as e:
        if logger:
            logger.error(f"Failed to create output directory {dir_path}: {e}")
        return False


def image_to_base64(image_path: Path, logger: Optional[logging.Logger] = None) -> Optional[str]:
    """
    Convert an image file to base64 encoded string.

    Args:
        image_path: Path to image file
        logger: Optional logger for error messages

    Returns:
        Base64 encoded string or None if error
    """
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (handles RGBA, grayscale, etc.)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # Save to bytes buffer
            buffer = BytesIO()
            img.save(buffer, format='JPEG')
            buffer.seek(0)

            # Encode to base64
            base64_str = base64.b64encode(buffer.read()).decode('utf-8')

            if logger:
                logger.debug(f"Image converted to base64: {image_path} ({len(base64_str)} chars)")

            return base64_str

    except Exception as e:
        if logger:
            logger.error(f"Failed to process image {image_path}: {e}")
        return None


def detect_chinese_text(text: str) -> bool:
    """
    Detect if text contains Chinese characters.

    Args:
        text: Text to analyze

    Returns:
        True if Chinese characters are detected
    """
    import re
    # CJK Unified Ideographs block - covers most common Chinese characters
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
    return bool(chinese_pattern.search(text))


class OllamaClient:
    """
    Wrapper class for Ollama Python client.
    Provides methods for generate and chat operations.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        timeout: int = 120,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize Ollama client.

        Args:
            host: Ollama service host URL
            timeout: Request timeout in seconds
            logger: Optional logger instance
        """
        self.host = host
        self.timeout = timeout
        self.logger = logger or setup_logger('ollama_client')
        self.client = ollama.Client(host=host)

    def generate(
        self,
        model: str,
        prompt: str,
        images: Optional[List[str]] = None,
        stream: bool = False
    ) -> str:
        """
        Generate text using Ollama's generate endpoint.

        Args:
            model: Model name
            prompt: Text prompt
            images: Optional list of base64 encoded images
            stream: Whether to stream response (not yet supported)

        Returns:
            Generated text
        """
        try:
            self.logger.info(f"Calling Ollama generate with model: {model}")

            options = {
                'model': model,
                'prompt': prompt,
                'think': False,  # Disable thinking mode for Qwen3 models
            }

            if images:
                options['images'] = images

            response = self.client.generate(**options)

            return response.get('response', '')

        except Exception as e:
            self.logger.error(f"Ollama generate failed: {e}")
            raise

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        stream: bool = False
    ) -> str:
        """
        Chat using Ollama's chat endpoint.

        Args:
            model: Model name
            messages: List of message dicts with 'role' and 'content'
            stream: Whether to stream response (not yet supported)

        Returns:
            Generated response text
        """
        try:
            self.logger.info(f"Calling Ollama chat with model: {model}")

            response = self.client.chat(
                model=model,
                messages=messages,
                think=False  # Disable thinking mode for Qwen3 models
            )

            return response.get('message', {}).get('content', '')

        except Exception as e:
            self.logger.error(f"Ollama chat failed: {e}")
            raise

    def check_model(self, model: str) -> bool:
        """
        Check if a model is available in Ollama.

        Args:
            model: Model name to check

        Returns:
            True if model is available
        """
        try:
            models_response = self.client.list()
            # models_response is a ListResponse object with a 'models' attribute
            # Each model has a 'model' attribute containing the full model name
            model_names = [m.model.split(':')[0] for m in models_response.models]
            base_model = model.split(':')[0]
            is_available = base_model in model_names

            if not is_available and self.logger:
                self.logger.debug(f"Model '{model}' (base: '{base_model}') not found in available models: {model_names}")

            return is_available
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Failed to check model availability: {e}")
            return True  # Assume available to avoid blocking


def read_input_text(input_arg: Path, logger: Optional[logging.Logger] = None) -> str:
    """
    Read input from a file or return the string directly.

    Args:
        input_arg: Path to file or text string
        logger: Optional logger

    Returns:
        Text content
    """
    input_path = resolve_path(input_arg)

    if input_path.is_file():
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if logger:
                logger.info(f"Read {len(content)} characters from {input_path}")
            return content
        except Exception as e:
            if logger:
                logger.error(f"Failed to read file {input_path}: {e}")
            raise
    else:
        # Return as-is if it's not a file
        return str(input_arg)
