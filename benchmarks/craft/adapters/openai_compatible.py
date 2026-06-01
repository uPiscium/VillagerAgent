from openai import OpenAI


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, api_key: str):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.last_response_info = {}

    def chat(self, messages, *, model, temperature=0.0, max_tokens=None, stop=None):
        attempts = []
        response = self._create_completion(
            model=model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )
        info = _response_info(response)
        attempts.append(info)
        content = info["content"]

        if not content and _should_retry_empty_content(info):
            retry_messages = list(messages) + [{
                "role": "user",
                "content": (
                    "Your previous response had no public message content. "
                    "Return only the final answer text now. Do not include analysis."
                ),
            }]
            retry_response = self._create_completion(
                model=model,
                messages=retry_messages,
                temperature=temperature,
                max_tokens=max(max_tokens or 0, 4096),
                stop=stop,
            )
            retry_info = _response_info(retry_response)
            retry_info["retry_reason"] = "empty_content"
            attempts.append(retry_info)
            content = retry_info["content"]

        self.last_response_info = {
            "model": model,
            "attempts": attempts,
            "content_empty": not bool(content),
        }
        return content or ""

    def _create_completion(self, *, model, messages, temperature, max_tokens, stop):
        kwargs = {}
        if "qwen3" in model.lower():
            kwargs["extra_body"] = {"enable_thinking": False}
        return self.client.chat.completions.create(
            model=model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            **kwargs,
        )


def _response_info(response) -> dict:
    choice = response.choices[0]
    message = choice.message
    content = message.content or ""
    reasoning = getattr(message, "reasoning", None) or ""
    return {
        "content": content,
        "content_chars": len(content),
        "reasoning_chars": len(reasoning),
        "finish_reason": getattr(choice, "finish_reason", None),
        "usage": _usage_dict(getattr(response, "usage", None)),
    }


def _usage_dict(usage) -> dict | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if isinstance(usage, dict):
        return usage
    return None


def _should_retry_empty_content(info: dict) -> bool:
    return not info["content"] and info.get("reasoning_chars", 0) > 0
