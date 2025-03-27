import openai
from django.conf import settings
from contextlib import contextmanager
from django.utils import translation


class TranslateService:
    """
    A Translation Service for handling text translations dynamically.
    It uses OpenAI's API for real-time translation.
    """

    def __init__(
        self,
    ):
        self.api_key = settings.OPENAI_API_KEY

    def translate(self, text: str, target_language: str = "es") -> str:
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
                        "content": f"Translate this to {target_language}: {text}",
                    },
                ],
            )
            return response["choices"][0]["message"]["content"]

        except Exception as e:
            print(f"Translation Error: {e}")
            return text


@contextmanager
def switch_language(instance, language_code):
    """Context manager to switch language for a translatable model instance."""
    current_language = translation.get_language()
    try:
        translation.activate(language_code)
        instance.set_current_language(language_code)
        yield instance
    finally:
        translation.activate(current_language)
