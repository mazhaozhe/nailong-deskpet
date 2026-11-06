import ctypes
import sys
import time


def wait_for_release(is_pressed, sleep=time.sleep):
    while is_pressed():
        sleep(0.01)


def main():
    is_pressed = lambda: bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    for line in sys.stdin:
        try:
            gesture_id = int(line.strip())
        except ValueError:
            continue
        wait_for_release(is_pressed)
        print(gesture_id, flush=True)


if __name__ == '__main__':
    main()
