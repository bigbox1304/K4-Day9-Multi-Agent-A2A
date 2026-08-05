import json
import time
from openai import OpenAI

MODEL_NAME = "gpt-4o-mini"

class LLMClient:
    def __init__(self, api_key: str):
        # Mặc định của thư viện openai sẽ gọi thẳng đến api.openai.com
        self.client = OpenAI(
            api_key=api_key
        )
        self.last_call_time = 0
        self.min_interval = 0.5  # Tốc độ gọi khá nhanh (khoảng 120 RPM)

    def call(self, system_prompt: str, user_prompt: str, max_retries: int = 3) -> dict:
        """Call LLM with rate limiting and retry. Returns a parsed JSON dict."""
        
        for attempt in range(max_retries):
            # Rate limiting
            elapsed = time.time() - self.last_call_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
                
            try:
                # Bắt buộc phải có chữ "JSON" trong system_prompt khi dùng JSON mode của OpenAI
                modified_system_prompt = system_prompt + "\n\nCRITICAL: Output must be a valid JSON object."
                
                response = self.client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[
                        {"role": "system", "content": modified_system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                self.last_call_time = time.time()
                content = response.choices[0].message.content
                return json.loads(content)
            except Exception as e:
                wait = 2 ** (attempt + 1) * 3  
                print(f"[LLM Error] {e} - Sleeping for {wait}s... (Attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
        raise RuntimeError("Max retries exceeded for LLM call")
