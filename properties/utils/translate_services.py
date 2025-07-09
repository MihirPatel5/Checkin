import openai, requests
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
    prompt = f"Translate the following to {target_lang}:\n\n{text}"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response['choices'][0]['message']['content'].strip()

def generate_translations(source_data: dict, source_lang: str) -> dict:
    translations = {source_lang: source_data}

    for lang in TARGET_LANGUAGES:
        print('lang: ', lang)
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


DEEPL_API_URL = 'https://api.deepl.com/v2/translate'

def translate_text(text, target_lang):
    """
    Translate text to the specified target language using DeepL API.
    
    Args:
        text (str): The text to translate.
        target_lang (str): The target language code (e.g., 'EN', 'FR', 'ES').
    
    Returns:
        str: Translated text, or original text if translation fails.
    """
    api_key = getattr(settings, 'DEEPL_API_KEY', None)
    if not api_key:
        raise ValueError("DEEPL_API_KEY is not set in settings.")

    if not isinstance(text, str) or not text.strip():
        return text

    params = {
        'auth_key': api_key,
        'text': text,
        'target_lang': target_lang.upper(),
    }

    try:
        response = requests.post(DEEPL_API_URL, data=params)
        response.raise_for_status()
        return response.json()['translations'][0]['text']
    except requests.exceptions.RequestException as e:
        print(f"Error translating text: {e}")
        return text

def translate_dict(data, target_lang):
    """
    Recursively translate all string values in a dictionary.
    
    Args:
        data: The data structure (dict, list, or primitive) to translate.
        target_lang (str): The target language code.
    
    Returns:
        The data structure with all strings translated.
    """
    if isinstance(data, dict):
        return {
            key: translate_dict(value, target_lang)
            for key, value in data.items()
        }
    elif isinstance(data, list):
        return [translate_dict(item, target_lang) for item in data]
    elif isinstance(data, str):
        return translate_text(data, target_lang)
    return data