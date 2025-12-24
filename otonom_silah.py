# KÜTÜPHANELER

import cv2
import math
from ultralytics import YOLO
import os
import datetime
import serial
import cvzone
import threading
import time
import logging

# AYARLAR
CAMERA_INDEX = 1                # webcam için bunu 1 yap
WS, HS = 640, 480               # kamera örnekleme boyutu
H_FOV, V_FOV = 75.7, 50         # kamera görüş açıları
SERVO_INIT = (90, 110)          # servoların başlangıç konumları (X, Y)
SAVE_AS_VIDEO = True            # videoya kaydetmek için True olarak ayarla
ARDUINO_PORT = "COM7"           # arduino seri bağlantı portu
ARDUINO_BAUD = 115200           # arduino baudrate ayarı
ARDUINO_TIMEOUT = 1000          # arduino timeout ayarı
NO_DETECTION_DELAY = 1.0        # saniye hedef bulunamazsa tarama moduna gir
MOVEMENT_DELAY = 0.5            # saniyden önce tekrar hareket etme
IDLE_STEP = 15                  # tarama yaparken atlanacak açı miktarı 

# EK BAŞLANGIÇ AYARLARI
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

# YOLO mesajlarını gizle
logger = logging.getLogger("ultralytics")
logger.setLevel(logging.ERROR)

# GLOBAL DEĞİŞKENLER
servo_x, servo_y = SERVO_INIT
arduino_pan, arduino_tilt = SERVO_INIT
arduino_ready = False
fire_trigger = False
detection_time = 0
movement_time = 0
hareketler = []
idle_status = False
idle_status_prev = idle_status
idle_x_dir = 1
idle_step_time = 0

# YOLO MODELİ
model = YOLO("modeller/yolov8n.pt")

# YARDIMCI FONKSİYONLAR
# değerler Sıkıştırılır

def clamp(n, min_val, max_val):
    if n < min_val:
        return min_val
    elif n > max_val:
        return max_val
    else:
        return n


# OPENCV KAMERA
cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, WS)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HS)

# KAMERA KONTROLÜ
if cap.isOpened():
    print("KAMERA BAŞLATILIYOR...")
else:
    print("KAMERA AYARLARINIZI KONTROL EDİNİZ !!!")
    exit()

# VİDEO DOSYASI OLUŞTURMA
videoWriter = None
if SAVE_AS_VIDEO:
    video_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    video_file_name = f"new_videos/result_{video_timestamp}.mp4"
    cv2_fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    videoWriter = cv2.VideoWriter(video_file_name, cv2_fourcc, 24, (WS, HS))
    print(f"Video {video_file_name} konumuna kaydedilecek.")

# ARDUINO BAĞLANTISI
ser = serial.Serial(ARDUINO_PORT, baudrate=ARDUINO_BAUD)
# seri port hazır olana kadar 2 saniye bekle
while not ser.writable():
    pass

# arduinodan ilk gelen satırı oku
print("< ARDUINO ilk gelen satır:", ser.readline().decode().strip())

# Servoları başlangıçtaki değerlerine ayarla
ser.write(f"{int(servo_x)} {int(servo_y)}\n".encode())
print("Servoları başlangıçtaki değerleri:", ser.readline().decode().strip())
movement_time = time.time()
arduino_ready = True

# OPENCV PENCERESİ
cv2.namedWindow("Object Tracking", cv2.WINDOW_NORMAL)


# Gelen değerler burada arduinoya iletiliyor
def send_data():
    global fire_trigger, arduino_pan, arduino_tilt, arduino_ready, movement_time
    global idle_status, idle_status_prev

    while cap.isOpened() and threading.current_thread().is_alive():
        if not arduino_ready:
            if idle_status != idle_status_prev:
                idle_status_prev = idle_status
                ser.write(f"B{int(not idle_status)}\n".encode())
                print("< ARDUINO", ser.readline().decode().strip())
                arduino_ready = True

            if arduino_pan != servo_x or arduino_tilt != servo_y:
                ser.write(f"{servo_x} {servo_y}\n".encode())
                print("< ARDUINO", ser.readline().decode().strip())
                movement_time = time.time()
                arduino_pan, arduino_tilt = servo_x, servo_y
                arduino_ready = True

            if fire_trigger:
                fire_trigger = False
                ser.write("FIRE\n".encode())
                print("< ARDUINO", ser.readline().decode().strip())
                movement_time = time.time()
                arduino_ready = True
        else:
            time.sleep(0.01)


data_thread = threading.Thread(target=send_data)
data_thread.start()

def idle_check():
    global idle_status, servo_x, servo_y, idle_x_dir, arduino_ready
    if time.time() - detection_time > NO_DETECTION_DELAY and arduino_ready:
        idle_status = True
        servo_y = 110

        if servo_x + idle_x_dir * IDLE_STEP > 180:
            idle_x_dir = -1
        elif servo_x + idle_x_dir * IDLE_STEP < 0 :
            idle_x_dir = 1
        
        servo_x += idle_x_dir * IDLE_STEP
        arduino_ready = False

while cap.isOpened():

    ret, frame = cap.read()
    frame = cv2.flip(frame, 1)
    if ret == False:
        print("Kamera Bağlantı Sorunu... yada video bitti...")
        break

    # YOLO V8'den sonuçları al
    results = model(frame)
    if results:

        object_detected = False

        for result in results:
            for box in result.boxes:
                # Boading box kordinatları
                x1, y1, x2, y2 = box.xyxy[0].int().tolist()
                # Nesnenin orta nokta kordinatı
                cx, cy = x1 + (x2 - x1) // 2, y1 + (y2 - y1) // 2
                # Kamera ölçüsü
                iw, ih = frame.shape[:2][::-1]
                # Kameranın orta noktası
                icx, icy = iw // 2, ih // 2                          
                # x ve y için merkez uzaklıklarını hesapla = calculate for x and y distances
                dx, dy = round(cx - icx), round(cy - icy)
                # Nesne ile kameranın orta nokta uzaklıkları
                cdist = int(math.sqrt(dx ** 2 + dy ** 2))
                # Tespit edilen sınıflar
                cls = result.names[int(box.cls[0])]
                # Tespit edilen nesnenin doğrulluk oranı
                conf = round(box.conf[0].item(), 2)

                if cls == "person" and round(box.conf[0].item(), 2) > 0.55:
                    idle_status = False
                    cls = "PERSON"

                    dpan = (dx/iw)*H_FOV
                    dtilt = -(dy/ih)*V_FOV

                    if time.time() - movement_time > MOVEMENT_DELAY and arduino_ready:

                        # açı hesaplama
                        # Bir sayıyı belirli bir aralıkta sınırlar
                        servo_x = round(clamp(arduino_pan + dpan, 0, 180))
                        servo_y = round(clamp(arduino_tilt + dtilt, 0, 180))

                        if arduino_pan != servo_x or arduino_tilt != servo_y:
                            arduino_ready = False
                            hareketler.append([dx, dy, dpan, dtilt])
                            print(
                                f"dx={dx}, dy={dy}, dpan={round(dpan,1)}, dtilt={round(dtilt,1)}, cdist={cdist}")
                            print(
                                f"Hedefe git: {arduino_pan, arduino_tilt} -> {servo_x, servo_y}")

                    object_detected = True
                    detection_time = time.time()

                    # Ekrana hedef işareti çizdirme
                    a = 10
                    cv2.rectangle(frame, (icx-a, icy-a),(icx+a, icy+a), (51, 251, 251), 2)
                    x, y = 320, 240
                    y_start, y_end = 210, 270
                    x_start, x_end = 280, 360
                    colorx = (51, 251, 251)
                    colorc = (255, 0, 0)
                    cv2.line(frame, pt1=(x, y_start), pt2=(x, y_end), color=colorx, thickness=2)
                    cv2.line(frame, pt1=(x_start, y), pt2=(x_end, y), color=colorx, thickness=2)

                    # EKRANA HİPOTENÜS ,DOĞRULUK,SERVOX,SERVOY,HEDEFE KİLİTLENDİ YAZDIRMA
                    cvzone.putTextRect(frame, f'{cls}', (max(0, x1 + 5), max(0, y1 - 15)), scale=2, thickness=2, colorT=(0, 0, 255), colorR=(255, 255, 255), font=cv2.FONT_HERSHEY_PLAIN, offset=8, border=2, colorB=(0, 0, 255))
                    # pisagor ile uzaklık
                    cv2.putText(frame, f'Distance: {int(cdist)}', (380, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'Acurracy: %{int(conf*100)}', (380, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.circle(frame, (cx, cy), 50, (0, 0, 255), 2)
                    cv2.putText(frame, f'ServoX: {int(servo_x)}',(10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'ServoY: {int(servo_y)}',(10, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'cx: {int(cx)}',(10, 250), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'cy: {int(cy)}',(10, 300), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'dx: {int(-dx)}',(10, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.putText(frame, f'dy: {int(dy)}',(10, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, colorc, 2)
                    cv2.line(frame, (0, cy), (WS, cy), (0, 0, 0), 2)  # x line
                    cv2.line(frame, (cx, HS), (cx, 0), (0, 0, 0), 2)  # y line
                    cv2.circle(frame, (cx, cy), 15, (0, 51, 255), cv2.FILLED)
                    cv2.putText(frame, "HEDEFE KiLiTLENDi", (50, 450),cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)

                    # TESPİT EDİLEN HER BİR SINIFA BENZERSİZ ID ATA VE EKRANA ÇİZDİR
                    track_id = box.id.item() if box.id is not None else -1
                    cvzone.putTextRect(frame, f'{cls} ID:{-track_id}', (max(0, x1 + 5), max(0, y1 - 15)), scale=2, thickness=2, colorT=(0, 0, 255), colorR=(255, 255, 255), font=cv2.FONT_HERSHEY_PLAIN, offset=8, border=2, colorB=(0, 0, 255))

                    # ateşleme kontrolü (cdist <= 10)
                    if cls == "PERSON" and round(box.conf[0].item(), 2) > 0.05 and cdist <= 10:
                        fire_trigger = True
                        #cv2.putText(frame,f"ATEŞ EDİLİYOR! Hedef konumu: {cx, cy}", (150,250),cv2.FONT_HERSHEY_SIMPLEX,2, (0,0,255), 3)
                        print(
                            f"ATEŞ EDİLİYOR! Hedef konumu Vertical:{cx}, Hortizol:{cy}")
                        cvzone.putTextRect(frame, f"FIRE", (250, 350), scale=2, thickness=3, colorT=(255, 255, 255), colorR=(0, 0, 255), font=cv2.FONT_HERSHEY_SIMPLEX, offset=10, border=None, colorB=(0, 255, 0))

                else:
                    print("check it")                             
                break
        
        if not object_detected:
            idle_check()
            a = 10
            
            iw, ih = frame.shape[:2][::-1]
            icx, icy = iw // 2, ih // 2
            cv2.rectangle(frame, (icx-a, icy-a),
                          (icx+a, icy+a), (51, 251, 251), 2)
            x = 320
            y_start = 210
            y_end = 270
            y = 240
            x_start = 280
            x_end = 360
            colorx = (51, 251, 251)
            colory = (127, 0, 255)

            # EKRANA PİSAGOR ,DOĞRULUK,SERVOX,SERVOY,HEDEF BULUNMADI YAZDIRMA
            cv2.putText(frame, "HEDEF BULUNMADI", (50, 450),cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 0, 255), 3)
            cv2.line(frame, pt1=(x, y_start), pt2=(x, y_end), color=colorx, thickness=2)
            cv2.line(frame, pt1=(x_start, y), pt2=(x_end, y), color=colorx, thickness=2)
            cv2.putText(frame, 'Distance: 000', (380, 50),cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'Acurracy: %00', (380, 100),cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'ServoX deg: 000', (10, 50),cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'ServoY deg: 000', (10, 100),cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'cx: 000',(10, 250), cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'cy: 000',(10, 300), cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'dx: 000',(10, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            cv2.putText(frame, 'dy: 000',(10, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, colory, 2)
            

    # çerçeveyi dosyasına yaz
    if SAVE_AS_VIDEO:
        videoWriter.write(frame)

    # çerçeveyi ekranda göster
    cv2.imshow("Object Tracking", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        cap.release()
        ser.close()

        with open("hareketler.txt", "w") as log_file:
            for hareket in hareketler:
                log_file.write("\t".join([str(x) for x in hareket]) + "\n")

        break

print("Program sonlandırılıyor...")

if SAVE_AS_VIDEO:
    videoWriter.release()

cap.release()
cv2.destroyAllWindows()
