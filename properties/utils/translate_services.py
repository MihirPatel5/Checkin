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

    def translate(self, text: str, target_language: str = None) -> str:
        """
        Translates the given text into the target language.
        """
        if not target_language:
            target_language = "es"
        print(f"Translating to: {target_language}")
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



openai.api_key = settings.OPENAI_API_KEY

TARGET_LANGUAGES = ['fr', 'es', 'de', 'it', 'pt', 'en']

def translate_text(text, target_lang):
    print('target_lang: ', target_lang)
    prompt = f"Translate the following to {target_lang}:\n\n{text}"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    print('response[): ', response['choices'][0]['message']['content'].strip())
    return response['choices'][0]['message']['content'].strip()

def generate_translations(source_data: dict, source_lang: str) -> dict:
    translations = {source_lang: source_data}

    for lang in TARGET_LANGUAGES:
        if lang == source_lang:
            continue
        lang_fields = {}
        for key, value in source_data.items():
            try:
                lang_fields[key] = translate_text(value, lang)
            except Exception as e:
                print(f"Translation error for {key} to {lang}: {e}")
                lang_fields[key] = value  # fallback
        translations[lang] = lang_fields
    return translations
