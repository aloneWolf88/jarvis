"""
OpenClaw exec 진입점
command-arg-mode: raw로 넘어온 자연어를 처리하여 stdout으로 결과 출력
"""
import sys
import os

# program.py import
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from program import process


def main():
    """
    OpenClaw exec 호출:
      exec: python route_cli.py 오늘 요약
    
    sys.argv[1:] = ["오늘", "요약"]
    text = "오늘 요약"
    
    process(text) → ResearchBot 메서드 실행 → stdout 출력
    """
    if len(sys.argv) < 2:
        text = ""
    else:
        text = " ".join(sys.argv[1:])
    
    # program.process() 호출 (router + ResearchBot까지 한 번에)
    result = process(text)
    
    # stdout으로 출력 (OpenClaw가 수집해서 텔레그램으로 전송)
    if result:
        print(result)
    else:
        print("처리 결과가 없습니다.")


if __name__ == "__main__":
    main()