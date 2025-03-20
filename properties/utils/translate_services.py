import openai
from django.conf import settings


class TranslateService:
    """
    A Translation Service for handling text translations dynamically.
    It uses OpenAI's API for real-time translation.
    """

    def __init__(self, target_language="es"):
        self.api_key = settings.OPENAI_API_KEY
        self.target_language = target_language

    def translate(self, text: str) -> str:
        """
        Translates the given text into the target language.
        """
        if not text:
            return ""

        try:
            openai.api_key = self.api_key
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a translator."},
                    {
                        "role": "user",
                        "content": f"Translate this to {self.target_language}: {text}",
                    },
                ],
            )
            return response["choices"][0]["message"]["content"]

        except Exception as e:
            print(f"Translation Error: {e}")
            return text