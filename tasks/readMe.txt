#모니터링 감시시스템 
 ㅇ 10분간격으로 모니터링중 
   1)monitor_site.py 
       - 역사박물관, 우리소리,보호나라 -> 등록 
   2) 모니터링 시간 변경은  taks.yaml 에서 변경  
 ㅇ 시스템 속성 > 환경변수에 등록 
    1) happyrich_bot 
   	-  TELEGRAM_CHAT_ID : 7724773647
   	-  TELEGRAM_TOKEN : 8913086040:AAFUef1Hs201BoBKg7knJVHaofqDyw9W7VI
     2)nara1_bot
        -  TELEGRAM_CHAT_ID : 7724773647
   	-  TELEGRAM_TOKEN : 8797994256:AAFgo92W206-CTKPx1K7EII55Axk-tAl7DA

ㅇ 그룹초대를 했으나,문자 전송이 안될때 >  BotFather에서 Privacy Mode OFF
  1) 텔레그램에서 @BotFather 검색
  2) /setprivacy 입력
  3) 봇 선택 (nara1_bot)
  4) Disable 선택
-----------------------------------------------------------------------------


1. 텔레그램 봇을 초대후 
   1)  https://api.telegram.org/bot<Token>/getUpdates
   2) my_chat_member":{"chat":{"id":-5231024855,"title":"\uc5ed\uc0ac\ubc15\ubb3c\uad00 \ubaa8\ub2c8\ud13  0        \ub9c1","type":"group","all_members_are_administrators":false,"accepted_gift_types":{"unlimited_gifts":false,"limited_gifts":false,"unique_gifts":false,"premium_subscripti  on":false,"gifts_from_channels":false}},"from":{"id":7724773647,"is_bot":false,"
     마이너스(-) id 값을 TELEGRAM_CHAT_ID  시스템 환경변수에 넣는다 
   3) 텔레그램 > /start@nara

2. 스케쥴정지 
 >  python manage.py disable site-monitor

3. 스케쥴등록
  > python manage.py enable site-monitor
