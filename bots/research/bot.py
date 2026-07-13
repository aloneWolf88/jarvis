import os
import subprocess  

class ResearchBot:

    def _run(self, *args):
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        cmd = [
            "python",
            os.path.join(BASE_DIR, "bots", "research", "main.py"),  # 수정: research_bot → bots/research
            *args
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8"
        )

        if result.returncode != 0:
            return result.stderr

        return result.stdout

    def summaries(self, name=None):               # 추가: name 받기
        print("[BOT] Detected research command::::", name)
        if name:
            return self._run("--summaries", name)  # 추가: 키워드 전달
        return self._run("--summaries")

    def stats(self):
        return self._run("--stats")

    def today(self):
        return self._run("--today")

    def yesterday(self):
        return self._run("--yesterday")

    def list_tickers(self):
        return self._run("--list-tickers")

    def list_reports(self):
        return self._run("--list-reports")

    def new(self):
        return self._run("--new")

    def ticker(self, ticker):
        return self._run("--ticker", ticker)

    def query(self, query):
        return self._run("--query", query)

    def batch(self):
        return self._run("--batch")    

    def lookup(self, query):
        return self._run("--lookup", query)    
