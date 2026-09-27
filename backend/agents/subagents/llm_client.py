"""Bounded model requests with actionable, credential-safe errors."""
import os
from openai import OpenAI, APIStatusError, APIConnectionError, APITimeoutError


class ModelRequestError(RuntimeError):
    """Provider failure that must not be hidden by classifier fallback."""


def generate(system_prompt: str, user_message: str, history: list[dict] | None = None) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ModelRequestError("OPENAI_API_KEY is missing in the backend environment. Set it and restart the backend.")

    model = os.environ.get("OPENAI_MODEL", "gpt-5.6-sol").strip()
    reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", "medium").strip().lower()
    messages = [
        {"role": "system", "content": system_prompt},
        *(history or []),
        {"role": "user", "content": user_message},
    ]

    request = {"model": model, "messages": messages}
    if model.startswith("gpt-5") or model.startswith("gpt-6"):
        request["reasoning_effort"] = reasoning_effort

    try:
        with OpenAI(api_key=api_key, timeout=60, max_retries=0) as client:
            response = client.chat.completions.create(**request)
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ModelRequestError("OpenAI returned an empty response. Please retry.")
        return content.strip()
    except APITimeoutError as e:
        raise ModelRequestError("OpenAI took too long to respond. Please retry with a smaller request.") from e
    except APIConnectionError as e:
        raise ModelRequestError("The backend could not connect to OpenAI. Check connectivity and retry.") from e
    except APIStatusError as e:
        body = e.body if isinstance(e.body, dict) else {}
        error = body.get("error", body)
        error = error if isinstance(error, dict) else {}
        code = error.get("code") or getattr(e, "code", None)
        quota_codes = {
            "insufficient_quota", "billing_hard_limit_reached",
            "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
            "organization_usage_limit_exceeded", "billing_not_active",
        }
        if code in quota_codes or error.get("type") == "insufficient_quota":
            message = "OpenAI API quota or billing limit reached. Check credits and usage limits for the API key's project."
        elif e.status_code == 401:
            message = "OpenAI rejected OPENAI_API_KEY. Check that the key is valid."
        elif e.status_code in (403, 404):
            message = "OpenAI denied access to the configured model. Check project permissions and OPENAI_MODEL."
        elif e.status_code == 429:
            message = "OpenAI request/token rate limit reached. Wait before retrying or reduce the repository context."
        elif e.status_code == 400:
            message = "OpenAI rejected the request. Check OPENAI_MODEL, OPENAI_REASONING_EFFORT, or reduce repository context."
        else:
            message = "OpenAI is temporarily unavailable. Please retry shortly."
        raise ModelRequestError(message) from e
