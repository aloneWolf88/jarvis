import requests
import time

TELEGRAM_TOKEN = '8913086040:AAFz9-ucpUDfsxU6NdBCaOzo83m8kf6AWiI'
CHAT_ID = 7724773647
ALERT_MESSAGE = "[Dev_드론정보포털] 긴급확인요망"
NORMAL_MESSAGE = "이상무"

def send_telegram_message(message):
    encoded_message = requests.utils.quote(message)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={CHAT_ID}&text={encoded_message}"
    response = requests.get(url)
    if response.status_code == 200:
        print("메시지 전송 성공")
    else:
        print("메시지 전송 실패")

def monitor_site():
    while True:
        try:
            response = requests.get(
                'http://218.146.11.102:8080/',
                timeout=10
            )
            if response.status_code != 200:
                send_telegram_message(f"{ALERT_MESSAGE}\n서버 상태 코드: {response.status_code}")
                print(f"서버 상태 코드: {response.status_code}")
            else:
                send_telegram_message(NORMAL_MESSAGE)
                print("사이트 정상 작동")

        except requests.exceptions.RequestException as e:
            print(f"요청 예외: {e}")
            send_telegram_message(ALERT_MESSAGE)

        time.sleep(60)

if __name__ == "__main__":
    monitor_site()