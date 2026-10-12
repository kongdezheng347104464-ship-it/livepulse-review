"""OCR service wrapper - uses Aliyun OCR API for text recognition.

Extract danmaku text from OBS frame captures via Aliyun cloud OCR API.
No local GPU/DL model required.
"""
from app.services.aliyun_ocr_service import extract_danmaku

__all__ = ["extract_danmaku"]
