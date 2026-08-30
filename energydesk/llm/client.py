"""Chat client for the Gemini API.

Google exposes an OpenAI-compatible endpoint, so one call shape
(system + user message -> text) over a plain `requests` session is all
the monitor needs. The client walks a small list of model ids and moves
to the next one on quota errors, retired models or transient failures;
if every refusal was quota-shaped it says so, because that means "come
back after midnight UTC", not "something is broken".
"""

import time

import requests

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class LLMError(RuntimeError):
    """Raised when no configured model could produce a completion."""


class ModelUnavailable(LLMError):
    """Rate limited or out of quota: skip straight to the next model."""


class QuotaExceeded(LLMError):
    """Every configured model refused with a rate or quota error.

    With free tiers this usually means the daily allowance is spent
    rather than a provider outage.
    """


class UpstreamBusy(LLMError):
    """Transient provider error: worth one quick retry before moving on."""


RETRYABLE_STATUS = (500, 502, 503, 504)
RETRY_DELAY_SECS = 3.0


class GeminiClient:
    def __init__(self, api_key: str, models: list[str], timeout: int = 180):
        if not api_key:
            raise LLMError("missing GEMINI_API_KEY")
        if not models:
            raise LLMError("no model configured")
        self.models = models
        self.timeout = timeout
        self.chat_url = GOOGLE_BASE_URL.rstrip("/") + "/chat/completions"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_settings(cls, settings) -> "GeminiClient":
        return cls(settings.gemini_api_key, settings.gemini_models)

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        """One chat completion, walking down the fallback list on failures.

        Rate limits, retired models and transient upstream errors move to
        the next model; transient ones get one short retry first. Any
        failure stays confined to its model. When every refusal was a rate
        or quota error the specific QuotaExceeded is raised, so callers can
        tell an exhausted key from a broken one.
        """
        errors = []
        only_quota = True

        def record(model: str, exc: Exception) -> None:
            nonlocal only_quota
            errors.append(f"{model}: {exc}")
            only_quota = only_quota and isinstance(exc, ModelUnavailable)

        transient = (UpstreamBusy, requests.Timeout, requests.ConnectionError)
        for model in self.models:
            try:
                return self._call(model, system, user, max_tokens)
            except transient as exc:
                print(f"[llm] {model} busy ({exc}); retrying once")
                time.sleep(RETRY_DELAY_SECS)
                try:
                    return self._call(model, system, user, max_tokens)
                except Exception as retry_error:
                    print(f"[llm] {model} failed ({retry_error}); "
                          f"trying next model")
                    record(model, retry_error)
            except Exception as exc:
                print(f"[llm] {model} failed ({exc}); trying next model")
                record(model, exc)

        if only_quota:
            raise QuotaExceeded(
                "every model answered rate limited or out of quota:\n"
                + "\n".join(errors)
            )
        raise LLMError("all configured models failed:\n" + "\n".join(errors))

    def _call(self, model: str, system: str, user: str, max_tokens: int) -> str:
        # Hidden reasoning tokens count against this ceiling too, so the
        # caller passes generous values.
        payload = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        response = self.session.post(self.chat_url, json=payload,
                                     timeout=self.timeout)

        if response.status_code in (429, 402):
            raise ModelUnavailable(
                f"rate limited or out of quota (HTTP {response.status_code})")
        if response.status_code == 404:
            # Providers retire model ids without notice; treat it like any
            # other reason to rotate down the list.
            hint = ""
            try:
                hint = str(response.json().get("error", {}).get("message", ""))[:160]
            except ValueError:
                pass
            raise ModelUnavailable(f"model unavailable (HTTP 404) {hint}")
        if response.status_code in RETRYABLE_STATUS:
            raise UpstreamBusy(f"provider overloaded (HTTP {response.status_code})")
        if response.status_code >= 400:
            # Surface the provider's own reason (invalid key, bad field,
            # model access...) instead of a bare status code.
            raise LLMError(
                f"HTTP {response.status_code}: {response.text[:200]}")
        response.raise_for_status()

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"unexpected response shape: {str(data)[:300]}")
        if not content:
            raise LLMError("model returned an empty completion")
        return content
