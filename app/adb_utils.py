import subprocess
from app import config as C

def adb(*args, capture_output=True):
    return subprocess.run(["adb", "-s", C.DEVICE, *args], capture_output=capture_output)

def tap(x:int, y:int):
    print(f"[ADB] input tap {int(x)} {int(y)}")
    subprocess.run(["adb","-s",C.DEVICE,"shell","input","tap",str(int(x)),str(int(y))])


def swipe(x1:int, y1:int, x2:int, y2:int, ms:int):
    subprocess.run(["adb","-s",C.DEVICE,"shell","input","swipe",
                    str(int(x1)),str(int(y1)),str(int(x2)),str(int(y2)),str(int(ms))])

def screencap_png_bytes():
    p = subprocess.Popen(["adb","-s",C.DEVICE,"exec-out","screencap","-p"], stdout=subprocess.PIPE)
    return p.stdout.read()
