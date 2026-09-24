"""Model loading and English prompts for the direct and structured systems."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


class TextGenerator(Protocol):
    def generate(self, prompt: str) -> str:
        """Generate one deterministic completion."""


@dataclass
class GenerationSettings:
    max_new_tokens: int = 512
    load_in_4bit: bool = True


class HuggingFaceGenerator:
    """Small wrapper shared by the baseline and Deliverable 2 solution."""

    def __init__(self, model_id: str, settings: GenerationSettings | None = None):
        settings = settings or GenerationSettings()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_id = model_id
        self.settings = settings
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        kwargs: dict[str, Any] = {"device_map": "auto"}
        if settings.load_in_4bit and torch.cuda.is_available():
            from transformers import BitsAndBytesConfig

            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)

    def generate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        ).to(self.model.device)
        output = self.model.generate(
            **inputs,
            max_new_tokens=self.settings.max_new_tokens,
            do_sample=False,
        )
        generated = output[0][inputs["input_ids"].shape[1] :]
        return self.tokenizer.decode(generated, skip_special_tokens=True).strip()


def direct_prompt(schema: str, question: str) -> str:
    return f"""You are a business analyst translating a manager's question into SQLite SQL.

Use only the following database schema:
{schema}

Manager question:
{question}

Return only the read-only SELECT statement or statements needed to answer the question.
Use SQLite syntax, the exact table and column names, and the dates and labels present in the question.
If multiple statements are needed, separate them with semicolons. Do not explain anything and do not use markdown."""


def plan_prompt(
    schema: str,
    question: str,
    previous_error: str | None = None,
    expected_operation: str | None = None,
) -> str:
    retry = ""
    if previous_error:
        retry = f"\nThe previous attempt failed with this error. Correct it: {previous_error}\n"
    operation_hint = ""
    if expected_operation:
        operation_hint = (
            f"\nAn intent guard identified the required composition operation as "
            f"{expected_operation}. Use that operation.\n"
        )
    return f"""You are the planning component of a business analytics assistant.

The user is a manager and does not know SQL. Convert the question into a minimal, executable plan.
Use only SQLite read-only SELECT or WITH queries and only the schema below.
Retrieve no irrelevant tables. A combined question must use multiple steps when its answer depends
on multiple values. Never calculate percentages mentally: declare the composition operation.

Relevant schema:
{schema}

Question:
{question}
{retry}
{operation_hint}
Return exactly one JSON object with this shape and no markdown:
{{
  "question_type": "puntual" or "combinada",
  "steps": [
    {{"id": "step_1", "purpose": "short English description", "sql": "one SQLite SELECT"}}
  ],
  "composition": {{"operation": "direct|growth_pct|compare_equal|share_pct|filter_then_rank"}},
  "report_fields": ["fields that the final answer must mention"]
}}

For direct questions use exactly one step and operation direct; never return alternative or duplicate
queries. Select only the columns needed for the manager's answer. Every step is executed in a fresh
SQLite call, so a CTE defined in one step does not exist in another step; repeat the CTE definition
or use a self-contained query. SQLite does not support strftime('%Q'); use explicit date ranges.
Use products.category for category filters and products.name for product names. For an average
revenue per sale, use AVG(sales.amount), not a division by total quantity. For growth_pct, order
steps from earlier to later. For share_pct, return exactly two steps: total units first and subset
units second. For compare_equal, return exactly two self-contained steps, one for each period, and
compare their top names. For filter_then_rank, return exactly two steps: first select product_id
values below the reorder point, then return product_id and product name ordered by
SUM(sales.amount) DESC. Never use an unordered ranking query.
"""


def report_prompt(question: str, answer: Any) -> str:
    encoded = json.dumps(answer, ensure_ascii=False, indent=2)
    return f"""You are the reporting component of a business analytics assistant.

Write a concise answer in English for a non-technical manager. The answer must be in English even
when the question is in Spanish.
Question: {question}

The verified result is:
{encoded}

Do not invent or alter any number, product name, comparison, direction, or percentage.
Return exactly one JSON object with this shape and no markdown:
{{"answer": "one or two concise sentences", "claims": ["each factual claim in the answer"]}}
"""


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object even when a model accidentally adds a short fence."""

    cleaned = re.sub(r"```(?:json)?|```", "", text, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("The model did not return a JSON object.")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("The model JSON must be an object.")
    return value
