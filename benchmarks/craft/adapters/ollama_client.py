import requests


class OllamaNativeClient:
    def __init__(self, *, base_url: str, think: bool = False):
        self.base_url = base_url.rstrip("/")
        self.think = think
        self.last_response_info = {}

    def chat(self, messages, *, model, temperature=0.0, max_tokens=None, stop=None):
        payload = {
            "model": model,
            "messages": list(messages),
            "stream": False,
            "think": self.think,
            "options": {
                "temperature": temperature,
            },
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if stop is not None:
            payload["options"]["stop"] = list(stop)

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "") or ""
        thinking = message.get("thinking", "") or ""
        self.last_response_info = {
            "model": model,
            "provider": "ollama_native",
            "content_empty": not bool(content),
            "attempts": [{
                "content": content,
                "content_chars": len(content),
                "reasoning_chars": len(thinking),
                "finish_reason": data.get("done_reason"),
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count"),
                    "completion_tokens": data.get("eval_count"),
                    "total_tokens": _sum_optional(data.get("prompt_eval_count"), data.get("eval_count")),
                },
            }],
        }
        return content


def _sum_optional(left, right):
    if left is None or right is None:
        return None
    return left + right
