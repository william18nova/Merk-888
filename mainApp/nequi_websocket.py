import websocket
import json
import threading
import re
import time

ACCESS_TOKEN = 'o.XS4ym3Mb40d9T6xi3W2C4KX3FL6gKYQT'

global flag, total
flag = False

def on_message(ws, message):
    global flag, total
    data = json.loads(message)
    if 'type' in data and data['type'] == 'push':
        push = data['push']
        application_name = push.get('application_name')
        print(application_name)
        if application_name == 'Nequi Colombia':
            body = push.get('body')
            match = re.search(r'te envió (\d+)', body)
            if match:
                x = int(match.group(1))
                if x >= total:
                    flag = True
                    ws.close()

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws):
    print("WebSocket closed")

def on_open(ws):
    print("WebSocket connection opened")

def verificacionPago(total_amount):
    global flag, total
    flag = False
    total = total_amount

    ws = websocket.WebSocketApp(f"wss://stream.pushbullet.com/websocket/{ACCESS_TOKEN}",
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    wst = threading.Thread(target=ws.run_forever)
    wst.daemon = True
    wst.start()
    i  = 3000
    start_time = time.time()
    while time.time() - start_time < 30000:  # 5 minutos = 300 segundos
        if flag:
            print('pago completo')
            return True
        time.sleep(1)
        print(i)
        i -= 1

    ws.close()
    print('se cerro time out')
    return False
