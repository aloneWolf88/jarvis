# bots/voice/stt.py
import os
import yaml
from faster_whisper import WhisperModel

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(BASE_DIR, "config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)

VOICE_CFG = config["voice"]

_model = None  # 모듈 전역 — 1회 로딩 후 재사용


def _get_model():
    """지연 초기화: 최초 호출 시에만 모델 로딩 (summarizer._get_client 패턴과 동일)"""
    global _model
    if _model is None:
        _model = WhisperModel(
            VOICE_CFG["model"],
            device=VOICE_CFG["device"],
            compute_type=VOICE_CFG["compute_type"],
        )
    return _model


def transcribe(audio_path: str, txt_path: str) -> str:
    """m4a → 텍스트 변환 후 txt 저장. 전체 텍스트 반환."""
    model = _get_model()
    segments, info = model.transcribe(audio_path, language=VOICE_CFG["language"])

    lines = [seg.text for seg in segments]
    text = "\n".join(lines)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    return text
