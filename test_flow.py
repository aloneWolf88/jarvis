from faster_whisper import WhisperModel

print("모델 로딩 중...")

model = WhisperModel(
    "large-v3",
    device="cuda",
    compute_type="float16"
)

print("음성 분석 시작...")

segments, info = model.transcribe(
    r"D:\90.temp\20260703공공데이터 등록.m4a",
    language="ko"
)

print("텍스트 생성 중...")

with open(r"D:\90.temp\result.txt", "w", encoding="utf-8") as f:
    for segment in segments:
        print(segment.text)
        f.write(segment.text + "\n")

print("완료")