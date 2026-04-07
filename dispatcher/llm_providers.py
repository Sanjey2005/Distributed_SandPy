"""
LLM Provider Abstraction Layer
Supports: OpenAI, Anthropic, Google Gemini, Groq, Ollama (local)
"""
import os
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any, AsyncIterator
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    GROQ = "groq"
    OLLAMA = "ollama"


@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    tokens_used: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class ModelConfig:
    provider: LLMProvider
    model_id: str
    display_name: str
    max_tokens: int = 4096
    temperature: float = 0.2
    supports_streaming: bool = True


# Available models registry
AVAILABLE_MODELS: Dict[str, ModelConfig] = {
    # OpenAI
    "gpt-4o": ModelConfig(LLMProvider.OPENAI, "gpt-4o", "GPT-4o", 4096),
    "gpt-4o-mini": ModelConfig(LLMProvider.OPENAI, "gpt-4o-mini", "GPT-4o Mini", 4096),
    "gpt-3.5-turbo": ModelConfig(LLMProvider.OPENAI, "gpt-3.5-turbo", "GPT-3.5 Turbo", 4096),
    # Anthropic
    "claude-3-5-sonnet": ModelConfig(LLMProvider.ANTHROPIC, "claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", 8192),
    "claude-3-haiku": ModelConfig(LLMProvider.ANTHROPIC, "claude-3-haiku-20240307", "Claude 3 Haiku", 4096),
    "claude-3-opus": ModelConfig(LLMProvider.ANTHROPIC, "claude-3-opus-20240229", "Claude 3 Opus", 4096),
    # Google Gemini
    "gemini-2.0-flash": ModelConfig(LLMProvider.GEMINI, "gemini-2.0-flash", "Gemini 2.0 Flash", 8192),
    "gemini-1.5-pro": ModelConfig(LLMProvider.GEMINI, "gemini-1.5-pro", "Gemini 1.5 Pro", 8192),
    # Groq (ultra-fast inference)
    "llama-3.3-70b": ModelConfig(LLMProvider.GROQ, "llama-3.3-70b-versatile", "Llama 3.3 70B (Groq)", 8192),
    "llama-3.1-8b": ModelConfig(LLMProvider.GROQ, "llama-3.1-8b-instant", "Llama 3.1 8B (Groq)", 8192),
    "mixtral-8x7b": ModelConfig(LLMProvider.GROQ, "mixtral-8x7b-32768", "Mixtral 8x7B (Groq)", 32768),
    "deepseek-r1-groq": ModelConfig(LLMProvider.GROQ, "deepseek-r1-distill-llama-70b", "DeepSeek R1 (Groq)", 8192),
    # Ollama (local models)
    "codellama": ModelConfig(LLMProvider.OLLAMA, "codellama", "CodeLlama (Local)", 4096),
    "deepseek-coder": ModelConfig(LLMProvider.OLLAMA, "deepseek-coder", "DeepSeek Coder (Local)", 4096),
    "llama3": ModelConfig(LLMProvider.OLLAMA, "llama3", "Llama 3 (Local)", 4096),
    "mistral": ModelConfig(LLMProvider.OLLAMA, "mistral", "Mistral (Local)", 4096),
}

CODE_GENERATION_SYSTEM_PROMPT = """You are an expert Python programmer working inside a distributed Jupyter sandbox environment.
Your role is to generate clean, efficient, executable Python code based on user requests.

Rules:
1. Return ONLY the Python code — no markdown fences, no explanations
2. The code runs in a Jupyter kernel where pandas, numpy, matplotlib are pre-imported
3. Use print() statements to display results
4. For plots, use plt.savefig('/mnt/data/output.png') AND plt.show()
5. Handle errors gracefully with try/except
6. Write production-quality, readable code with comments
"""

ERROR_EXPLANATION_SYSTEM_PROMPT = """You are an expert Python debugger and teacher.
Your role is to explain Python errors clearly and suggest fixes.

Return a JSON object with exactly these fields:
{
  "root_cause": "Brief explanation of why this error occurred",
  "explanation": "Detailed explanation of the error",
  "fix": "The corrected Python code",
  "tips": ["tip1", "tip2"]
}
"""

CODE_REVIEW_SYSTEM_PROMPT = """You are a senior Python security and code quality expert.
Review the provided Python code for issues.

Return a JSON object with exactly these fields:
{
  "safety_score": 0-100,
  "quality_score": 0-100,
  "security_issues": ["issue1", "issue2"],
  "quality_issues": ["issue1", "issue2"],
  "optimizations": ["suggestion1", "suggestion2"],
  "verdict": "SAFE" | "WARNING" | "DANGEROUS",
  "summary": "One-line summary"
}
"""


class LLMClient:
    def __init__(self):
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.groq_key = os.getenv("GROQ_API_KEY")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

    def get_available_providers(self) -> List[Dict]:
        """Returns list of available providers based on configured API keys."""
        available = []
        if self.openai_key:
            available.append({"provider": "openai", "models": ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]})
        if self.anthropic_key:
            available.append({"provider": "anthropic", "models": ["claude-3-5-sonnet", "claude-3-haiku", "claude-3-opus"]})
        if self.gemini_key:
            available.append({"provider": "gemini", "models": ["gemini-2.0-flash", "gemini-1.5-pro"]})
        if self.groq_key:
            available.append({"provider": "groq", "models": ["llama-3.3-70b", "llama-3.1-8b", "mixtral-8x7b", "deepseek-r1-groq"]})
        # Ollama is always available (local)
        available.append({"provider": "ollama", "models": ["codellama", "deepseek-coder", "llama3", "mistral"]})
        return available

    async def generate(
        self,
        prompt: str,
        model_key: str = "llama-3.3-70b",
        system_prompt: str = CODE_GENERATION_SYSTEM_PROMPT,
        context: Optional[str] = None,
    ) -> LLMResponse:
        """Generate a response from the specified model."""
        import time
        start = time.time()

        if model_key not in AVAILABLE_MODELS:
            return LLMResponse(content="", provider="unknown", model=model_key,
                             error=f"Unknown model: {model_key}")
        
        config = AVAILABLE_MODELS[model_key]
        
        # Build context-aware prompt
        full_prompt = prompt
        if context:
            full_prompt = f"Previous execution context:\n```\n{context}\n```\n\nNew request: {prompt}"

        try:
            if config.provider == LLMProvider.OPENAI:
                result = await self._call_openai(full_prompt, system_prompt, config)
            elif config.provider == LLMProvider.ANTHROPIC:
                result = await self._call_anthropic(full_prompt, system_prompt, config)
            elif config.provider == LLMProvider.GEMINI:
                result = await self._call_gemini(full_prompt, system_prompt, config)
            elif config.provider == LLMProvider.GROQ:
                result = await self._call_groq(full_prompt, system_prompt, config)
            elif config.provider == LLMProvider.OLLAMA:
                result = await self._call_ollama(full_prompt, system_prompt, config)
            else:
                result = LLMResponse(content="", provider=config.provider, model=model_key, error="Provider not implemented")
            
            result.latency_ms = (time.time() - start) * 1000
            return result
        except Exception as e:
            logger.error(f"LLM call failed for {model_key}: {e}")
            return LLMResponse(content="", provider=config.provider.value, model=model_key,
                             error=str(e), latency_ms=(time.time() - start) * 1000)

    async def race(
        self,
        prompt: str,
        model_keys: List[str],
        system_prompt: str = CODE_GENERATION_SYSTEM_PROMPT,
    ) -> Dict[str, LLMResponse]:
        """Race multiple models simultaneously, return all results."""
        tasks = {
            model_key: asyncio.create_task(self.generate(prompt, model_key, system_prompt))
            for model_key in model_keys
            if model_key in AVAILABLE_MODELS
        }
        results = {}
        completed = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for model_key, result in zip(tasks.keys(), completed):
            if isinstance(result, Exception):
                results[model_key] = LLMResponse(content="", provider="", model=model_key, error=str(result))
            else:
                results[model_key] = result
        return results

    async def _call_openai(self, prompt: str, system_prompt: str, config: ModelConfig) -> LLMResponse:
        if not self.openai_key:
            raise ValueError("OPENAI_API_KEY not configured")
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"},
                json={
                    "model": config.model_id,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": config.max_tokens,
                    "temperature": config.temperature,
                }
            )
            response.raise_for_status()
            data = response.json()
            return LLMResponse(
                content=data["choices"][0]["message"]["content"],
                provider="openai",
                model=config.model_id,
                tokens_used=data.get("usage", {}).get("total_tokens"),
            )

    async def _call_anthropic(self, prompt: str, system_prompt: str, config: ModelConfig) -> LLMResponse:
        if not self.anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config.model_id,
                    "max_tokens": config.max_tokens,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": prompt}],
                }
            )
            response.raise_for_status()
            data = response.json()
            return LLMResponse(
                content=data["content"][0]["text"],
                provider="anthropic",
                model=config.model_id,
                tokens_used=data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0),
            )

    async def _call_gemini(self, prompt: str, system_prompt: str, config: ModelConfig) -> LLMResponse:
        if not self.gemini_key:
            raise ValueError("GEMINI_API_KEY not configured")
        import httpx
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.model_id}:generateContent?key={self.gemini_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                json={
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": config.max_tokens, "temperature": config.temperature},
                }
            )
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return LLMResponse(content=text, provider="gemini", model=config.model_id)

    async def _call_groq(self, prompt: str, system_prompt: str, config: ModelConfig) -> LLMResponse:
        if not self.groq_key:
            raise ValueError("GROQ_API_KEY not configured")
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
                json={
                    "model": config.model_id,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": config.max_tokens,
                    "temperature": config.temperature,
                }
            )
            response.raise_for_status()
            data = response.json()
            return LLMResponse(
                content=data["choices"][0]["message"]["content"],
                provider="groq",
                model=config.model_id,
                tokens_used=data.get("usage", {}).get("total_tokens"),
            )

    async def _call_ollama(self, prompt: str, system_prompt: str, config: ModelConfig) -> LLMResponse:
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": config.model_id,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"temperature": config.temperature, "num_predict": config.max_tokens},
                }
            )
            response.raise_for_status()
            data = response.json()
            return LLMResponse(
                content=data["message"]["content"],
                provider="ollama",
                model=config.model_id,
            )


# Singleton instance
llm_client = LLMClient()
