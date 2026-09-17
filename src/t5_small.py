#!/usr/bin/env python3
"""
google-t5/t5-small English->German translation example.

English->German is one of the translation pairs t5-small was actually
trained on (alongside French and Romanian), so quality is usable for a 60M
model. The "translate English to German: " task prefix is required — it is
how T5 knows which task to perform.
"""

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL_ID = "google-t5/t5-small"
TASK_PREFIX = "translate English to German: "

TEST_CASES = [
    "Hello, how are you today?",
    "I like drinking coffee in the morning.",
    "Machine learning is changing the world.",
    "The meeting has been postponed to next Friday.",
    "Could you please pass me the salt?",
    "She has been living in Berlin for three years.",
]


def translate(text: str, model: AutoModelForSeq2SeqLM, tokenizer: AutoTokenizer,
              device: str, num_beams: int = 4) -> str:
    """Translate one English sentence to German."""
    inputs = tokenizer(TASK_PREFIX + text, return_tensors="pt").to(device)
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=64,
            num_beams=num_beams,
        )
    return tokenizer.decode(output_ids[0], skip_special_tokens=True)


def main() -> None:
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading {MODEL_ID} on {device} ...")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_ID).to(device)
    model.eval()

    for text in TEST_CASES:
        print(f"EN: {text}\nDE: {translate(text, model, tokenizer, device)}\n")


if __name__ == "__main__":
    main()
