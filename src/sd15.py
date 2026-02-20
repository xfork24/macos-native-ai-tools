#!/usr/bin/env python3
"""
Stable Diffusion 1.5 Image Generation Script

Uses Stable Diffusion 1.5 model for text-to-image generation.
Supports MPS (Apple Silicon) acceleration.
"""

import argparse
import random
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import setup_logger, resolve_path, ensure_output_dir


def main():
    parser = argparse.ArgumentParser(
        description='Image generation using Stable Diffusion 1.5',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic generation
  python src/sd15.py "a serene mountain landscape, high resolution, digital art"

  # Specify output file
  python src/sd15.py "a cat sitting on a table" -o my_image.png

  # With negative prompt and custom steps
  python src/sd15.py "a beautiful sunset" --negative "blurry, low quality" -s 30

  # Custom size and guidance scale
  python src/sd15.py "portrait of a woman" --width 768 --height 768 --guidance 8.5

  # Fixed seed for reproducibility
  python src/sd15.py "a futuristic city" --seed 42

  # Multiple images
  python src/sd15.py "a red rose" -n 4
        '''
    )

    parser.add_argument(
        'prompt',
        help='Text prompt for image generation'
    )

    parser.add_argument(
        '-n', '--negative',
        default='',
        help='Negative prompt (what to avoid in the image)'
    )

    parser.add_argument(
        '-o', '--output',
        default='sd15_output',
        help='Output file prefix (default: sd15_output)'
    )

    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path('./output'),
        help='Output directory for images (default: ./output)'
    )

    parser.add_argument(
        '-m', '--model',
        type=Path,
        default=Path('models/v1-5-pruned-emaonly-fp16.safetensors'),
        help='Path to SD1.5 model file (default: models/v1-5-pruned-emaonly-fp16.safetensors)'
    )

    parser.add_argument(
        '-s', '--steps',
        type=int,
        default=20,
        help='Number of denoising steps (default: 20)'
    )

    parser.add_argument(
        '--width',
        type=int,
        default=512,
        help='Image width in pixels (default: 512, must be multiple of 64)'
    )

    parser.add_argument(
        '--height',
        type=int,
        default=512,
        help='Image height in pixels (default: 512, must be multiple of 64)'
    )

    parser.add_argument(
        '-g', '--guidance',
        type=float,
        default=7.5,
        help='Guidance scale for prompt adherence (default: 7.5)'
    )

    parser.add_argument(
        '--seed',
        type=int,
        help='Random seed for reproducibility (default: random)'
    )

    parser.add_argument(
        '--num-images',
        type=int,
        default=1,
        help='Number of images to generate (default: 1)'
    )

    parser.add_argument(
        '--dtype',
        default='float16',
        choices=['float32', 'float16'],
        help='Data type for model (default: float16)'
    )

    parser.add_argument(
        '--safety',
        action='store_true',
        help='Enable safety checker (disabled by default)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Setup logger
    logger = setup_logger('sd15', args.verbose)

    # Validate dimensions
    if args.width % 64 != 0 or args.height % 64 != 0:
        logger.error("Width and height must be multiples of 64")
        sys.exit(1)

    # Validate steps
    if args.steps < 1 or args.steps > 150:
        logger.error("Steps must be between 1 and 150")
        sys.exit(1)

    # Validate guidance scale
    if args.guidance < 0:
        logger.error("Guidance scale must be non-negative")
        sys.exit(1)

    # Validate number of images
    if args.num_images < 1:
        logger.error("Number of images must be at least 1")
        sys.exit(1)

    # Parse output argument
    output_path_obj = Path(args.output)
    if output_path_obj.parent == Path('.') or str(output_path_obj.parent) == '.':
        file_prefix = output_path_obj.stem
        output_dir = args.output_dir
    else:
        file_prefix = output_path_obj.stem
        output_dir = output_path_obj.parent

    # Ensure output directory exists
    if not ensure_output_dir(output_dir, logger):
        logger.error("Failed to ensure output directory")
        sys.exit(1)

    # Resolve model path
    model_path = resolve_path(str(args.model))
    if not model_path.exists():
        logger.error(f"Model file not found: {model_path}")
        logger.error("Please download the model and place it in the correct location")
        sys.exit(1)

    # Set random seed
    if args.seed is not None:
        random.seed(args.seed)
        import torch
        torch.manual_seed(args.seed)

    try:
        import torch
        from diffusers import StableDiffusionPipeline

        # Set dtype
        torch_dtype = torch.float16 if args.dtype == 'float16' else torch.float32

        # Load model (safety checker disabled by default)
        logger.info(f"Loading SD1.5 model from: {model_path}")
        pipe = StableDiffusionPipeline.from_single_file(
            str(model_path),
            torch_dtype=torch_dtype,
            load_safety_checker=False,
            safety_checker=None,
            feature_extractor=None,
            requires_safety_checker=False,
        )

        # Explicitly disable all safety checking
        pipe.safety_checker = None
        pipe.requires_safety_checker = False

        logger.info("Safety checker disabled")

        # For MPS, use CPU offloading to work around MPS limitations
        logger.info("Setting up MPS acceleration with CPU offloading")
        pipe.enable_model_cpu_offload()

        # Prepare generation parameters
        logger.info(f"Prompt: {args.prompt[:100]}{'...' if len(args.prompt) > 100 else ''}")
        if args.negative:
            logger.info(f"Negative prompt: {args.negative}")
        logger.info(f"Steps: {args.steps}, Size: {args.width}x{args.height}, Guidance: {args.guidance}")
        if args.seed is not None:
            logger.info(f"Seed: {args.seed}")

        # Generate images
        for i in range(args.num_images):
            if args.num_images > 1:
                logger.info(f"Generating image {i + 1}/{args.num_images}")

            # Set seed for each image if provided (use CPU generator for CPU offloading)
            if args.seed is not None:
                generator = torch.Generator("cpu").manual_seed(args.seed + i)
            else:
                generator = None

            # Generate
            result = pipe(
                prompt=args.prompt,
                negative_prompt=args.negative if args.negative and args.negative.strip() else None,
                num_inference_steps=args.steps,
                width=args.width,
                height=args.height,
                guidance_scale=args.guidance,
                generator=generator,
            )

            # Save image
            if args.num_images == 1:
                output_file = output_dir / f"{file_prefix}.png"
            else:
                output_file = output_dir / f"{file_prefix}_{i:03d}.png"

            result.images[0].save(output_file)
            logger.info(f"Image saved: {output_file}")

        print(f"\n{'='*60}")
        print(f"✅ SD1.5 generation complete!")
        print(f"Generated {args.num_images} image(s)")
        print(f"Output directory: {output_dir}")
        print(f"{'='*60}")

    except KeyboardInterrupt:
        logger.info("\nOperation cancelled by user")
        sys.exit(0)
    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error("Please install torch and diffusers:")
        logger.error("  pip install torch diffusers")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Image generation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()
