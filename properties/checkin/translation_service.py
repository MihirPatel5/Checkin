from deep_translator import DeepL
from django.conf import settings
from .models import Translation

class TranslationService:
    @classmethod
    def translate_text(cls, text, target_lang, source_lang='auto'):
        try:
            return DeepL(settings.DEEPL_AUTH_KEY).translate(
                text=text, 
                target_lang=target_lang, 
                source_lang=source_lang
            )
        except Exception as e:
            return text

    @classmethod
    def translate_guest_fields(cls, guest, fields, target_lang, user=None):
        translations = {}
        for field in fields:
            source_text = getattr(guest, field)
            existing = Translation.objects.filter(
                object_id=guest.id,
                field_name=field,
                target_language=target_lang,
                source_text=source_text,
            ).first()
            if existing:
                translations[field] = existing.translated_text
                continue
            translated = cls.translate_text(source_text, target_lang)
            guest.save_translation(field, target_lang, translated, user)
            translations[field] = translated
        return translations