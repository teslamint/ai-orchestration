"""API-based LLM tool implementations.

This module provides API-based alternatives to CLI tools,
allowing direct HTTP API calls without external CLI dependencies.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass
class APIResponse:
    """Response from API-based LLM call."""

    content: str
    finish_reason: str
    usage: dict  # {"prompt_tokens": N, "completion_tokens": M}


class APIBasedTool(ABC):
    """Abstract base for API-based LLM tools."""

    model: str = ""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        """Synchronous generation."""
        pass

    @abstractmethod
    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        """Streaming generation yielding content chunks."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if API key is configured."""
        pass


class OpenAITool(APIBasedTool):
    """OpenAI API tool (gpt-4o)."""

    model = "gpt-4o"

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        from openai import OpenAI

        client = OpenAI()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = client.chat.completions.create(model=self.model, messages=messages)
        return APIResponse(
            content=response.choices[0].message.content or "",
            finish_reason=response.choices[0].finish_reason or "stop",
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": (
                    response.usage.completion_tokens if response.usage else 0
                ),
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        from openai import OpenAI

        client = OpenAI()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        stream = client.chat.completions.create(
            model=self.model, messages=messages, stream=True
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicTool(APIBasedTool):
    """Anthropic API tool (Claude Sonnet)."""

    model = "claude-sonnet-4-20250514"

    def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        import anthropic

        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        response = client.messages.create(**kwargs)
        return APIResponse(
            content=response.content[0].text if response.content else "",
            finish_reason=response.stop_reason or "end_turn",
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        import anthropic

        client = anthropic.Anthropic()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        with client.messages.stream(**kwargs) as stream:
            for text in stream.text_stream:
                yield text


class GoogleAITool(APIBasedTool):
    """Google AI API tool (Gemini)."""

    model = "gemini-2.0-flash"

    def is_available(self) -> bool:
        return bool(os.environ.get("GOOGLE_AI_API_KEY"))

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> APIResponse:
        import google.generativeai as genai

        genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
        model = genai.GenerativeModel(self.model, system_instruction=system_prompt)
        response = model.generate_content(prompt)
        return APIResponse(
            content=response.text,
            finish_reason="stop",
            usage={
                "prompt_tokens": (
                    response.usage_metadata.prompt_token_count
                    if response.usage_metadata
                    else 0
                ),
                "completion_tokens": (
                    response.usage_metadata.candidates_token_count
                    if response.usage_metadata
                    else 0
                ),
            },
        )

    def generate_stream(
        self, prompt: str, system_prompt: Optional[str] = None
    ) -> Iterator[str]:
        import google.generativeai as genai

        genai.configure(api_key=os.environ.get("GOOGLE_AI_API_KEY"))
        model = genai.GenerativeModel(self.model, system_instruction=system_prompt)
        response = model.generate_content(prompt, stream=True)
        for chunk in response:
            if chunk.text:
                yield chunk.text
