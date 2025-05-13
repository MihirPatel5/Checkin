from django.contrib import admin
from .models import Guest, Translation
from django.contrib.contenttypes.admin import GenericTabularInline

class TranslationInline(GenericTabularInline):
    model = Translation
    extra = 0
    readonly_fields = ('source_text', 'content_type', 'object_id')
    fields = ('field_name', 'target_language', 'translated_text')

@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    inlines = [TranslationInline]
    list_display = ('full_name', 'get_translations')
    
    def get_translations(self, obj):
        return ', '.join(obj.translations.keys())
    get_translations.short_description = 'Translations'

@admin.register(Translation)
class TranslationAdmin(admin.ModelAdmin):
    list_display = ('field_name', 'target_language', 'content_type')
    actions = ['approve_translations']
    
    def approve_translations(self, request, queryset):
        queryset.update()