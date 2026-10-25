from __future__ import annotations

import os
from pathlib import Path

from deckgen.providers.base import ProviderUnavailable, RateLimited, RawResult


class Adapter:
    name = "gemini"

    def __init__(self):
        self._client = None

    def _c(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        return self._client

    def complete(self, *, model, system, prompt, images, temperature, seed, max_tokens, schema_json):
        from google.genai import errors, types

        parts: list = [prompt]
        for img in images or []:
            parts.append(types.Part.from_bytes(data=Path(img).read_bytes(), mime_type="image/jpeg"))
        cfg = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                          temperature=temperature, seed=seed, max_output_tokens=max_tokens)
        try:
            r = self._c().models.generate_content(model=model, contents=parts, config=cfg)
        except errors.ClientError as e:
            if getattr(e, "code", 0) == 429:
                raise RateLimited(30.0, daily="PerDay" in str(e), message=str(e)[:200]) from e
            raise
        except errors.ServerError as e:
            raise ProviderUnavailable(str(e)[:200]) from e
        um = r.usage_metadata
        return RawResult(text=r.text or "", tokens_in=getattr(um, "prompt_token_count", 0) or 0,
                         tokens_out=getattr(um, "candidates_token_count", 0) or 0, model=model)

    def list_models(self) -> list[str]:
        return [m.name.removeprefix("models/") for m in self._c().models.list()]
