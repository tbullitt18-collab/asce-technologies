import os
import requests
from ibm_watsonx_client import WatsonxClient

class RedHatAIClient(WatsonxClient):
    """
    Client for Red Hat AI Inference (InstructLab) on IBM Cloud.
    Inherits IAM authentication from WatsonxClient but routes to the rhai endpoint.
    """
    def __init__(self, api_key: str = None, rhai_project_id: str = None, model_id: str = "instructlab/granite-7b-lab"):
        # We still need the API key for IAM auth
        super().__init__(api_key=api_key, project_id="dummy-uuid-not-used-for-rhai-00000000", model_id=model_id)
        
        # Red Hat AI uses a specific Project ID
        self.rhai_project_id = rhai_project_id or os.environ.get("RHAI_PROJECT_ID", "a1be3250-350b-48ad-986d-463aea6bf876")
        
        # The RHAI endpoint
        self.CHAT_ENDPOINT = f"https://us-east.rhai.ibm.com/v1/projects/{self.rhai_project_id}/inference/chat/completions"

    def chat(self, user_prompt: str, system_prompt: str = None, max_tokens: int = 600, temperature: float = 0.0) -> dict:
        token = self.generate_iam_token()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        # RHAI uses standard OpenAI schema for /chat/completions
        payload = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        response = requests.post(
            self.CHAT_ENDPOINT,
            headers={
                "Accept": "application/json", 
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            },
            json=payload,
            timeout=60,
        )
        
        if response.status_code == 200:
            body = response.json()
            choice = (body.get("choices") or [{}])[0]
            usage = body.get("usage", {})
            return {
                "status": "success",
                "model_id": body.get("model", self.model_id),
                "generated_text": (choice.get("message", {}).get("content") or "").strip(),
                "input_token_count": usage.get("prompt_tokens", 0),
                "generated_token_count": usage.get("completion_tokens", 0),
            }
            
        err = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"raw": response.text}
        return {"status": "error", "http_code": response.status_code, "error": err}
