#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TOUCH / SIGNAL — 영수증 QR 파이프라인
폴더 감시 → R2 업로드 → 고유 QR 생성 → 영수증 프린터 출력

전시장 컴퓨터에서 실행:  python pipeline.py
설치:  pip install -r requirements.txt
"""

import os, time, uuid, io, sys
from datetime import datetime

import boto3                                   # R2 업로드 (S3 호환)
import qrcode                                  # QR 생성
from watchdog.observers import Observer        # 폴더 감시
from watchdog.events import FileSystemEventHandler
from escpos.printer import Usb                 # 영수증 프린터 (ESC/POS)

# ═══════════════════════════════════════════════════════════════
# ▼▼▼ 여기만 수정하면 됩니다 (CONFIG) ▼▼▼
# ═══════════════════════════════════════════════════════════════
CONFIG = {
    # ── TouchDesigner가 5초 영상을 저장하는 폴더 ──
    "WATCH_FOLDER": r"C:\touch_signal\recordings",
    "EXT": ".mp4",                             # 감시할 확장자

    # ── Cloudflare R2 (대시보드 > R2 > Manage API Tokens 에서 발급) ──
    "R2_ACCOUNT_ID":  "XXXXXXXXXXXXXXXXXXXX",
    "R2_ACCESS_KEY":  "XXXXXXXXXXXXXXXXXXXX",
    "R2_SECRET_KEY":  "XXXXXXXXXXXXXXXXXXXX",
    "R2_BUCKET":      "touch-signal",
    # 버킷 공개 주소 (끝에 / 없이). index.html의 R2_BASE와 반드시 동일!
    "R2_PUBLIC_BASE": "https://pub-832ff24e01f04404a5738f9ee7512f62.r2.dev",

    # ── 다운로드 페이지 주소 (GitHub Pages). QR에는 이 주소가 들어감 ──
    "PAGE_BASE": "https://rivermin-304.github.io/XIST/",

    # ── 영수증 프린터 (USB) : lsusb / 장치관리자에서 확인한 값 ──
    # EPSON TM-T20 예시: 0x04b8 / 0x0e15
    "PRINTER_VENDOR_ID":  0x04b8,
    "PRINTER_PRODUCT_ID": 0x0e15,

    # ── 영수증에 인쇄할 문구 ──
    "RECEIPT_TITLE": "TOUCH / SIGNAL",
    "RECEIPT_SUB":   "Interactive Media Art",
    "RECEIPT_MSG":   "QR을 스캔해 당신의 5초를 저장하세요",

    # 처리 끝난 원본을 옮겨둘 폴더 (재처리 방지). None이면 그냥 둠.
    "DONE_FOLDER": r"C:\touch_signal\_done",

    # ── 순번(카운터) 설정 : XIST_037 형식 ──
    "ID_PREFIX":  "XIST_",             # 파일/URL 접두어 (index.html DOWNLOAD_PREFIX와 동일하게)
    "ID_START":   1,                   # 시작 번호
    "ID_PAD":     3,                   # 최소 자릿수 (037). 넘으면 자동 확장(1000...)
    "COUNTER_FILE": "counter.txt",     # 마지막 번호 저장 파일 (재시작해도 이어짐)
}
# ═══════════════════════════════════════════════════════════════
# ▲▲▲ CONFIG 끝 ▲▲▲
# ═══════════════════════════════════════════════════════════════


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# R2 클라이언트 (S3 호환 엔드포인트)
s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{CONFIG['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
    aws_access_key_id=CONFIG["R2_ACCESS_KEY"],
    aws_secret_access_key=CONFIG["R2_SECRET_KEY"],
    region_name="auto",
)


def wait_until_stable(path, timeout=30):
    """TD가 파일을 다 쓸 때까지 대기 (파일 크기가 멈추면 완료로 판단)."""
    last, stable = -1, 0
    start = time.time()
    while time.time() - start < timeout:
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size == last and size > 0:
            stable += 1
            if stable >= 3:        # 0.9초간 크기 변화 없음 → 완료
                return True
        else:
            stable = 0
        last = size
        time.sleep(0.3)
    return False


def upload_to_r2(local_path, key):
    """R2에 업로드. 브라우저에서 바로 재생되도록 content-type 지정."""
    s3.upload_file(
        local_path, CONFIG["R2_BUCKET"], key,
        ExtraArgs={"ContentType": "video/mp4"},
    )
    return f"{CONFIG['R2_PUBLIC_BASE']}/{key}"


def make_qr_image(url):
    """QR을 ESC/POS 프린터용 흑백 이미지로 생성."""
    qr = qrcode.QRCode(box_size=6, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("1")


def print_receipt(qr_img, vid):
    """영수증 출력."""
    p = Usb(CONFIG["PRINTER_VENDOR_ID"], CONFIG["PRINTER_PRODUCT_ID"])
    p.set(align="center", bold=True, double_height=True, double_width=True)
    p.text(CONFIG["RECEIPT_TITLE"] + "\n")
    p.set(align="center", bold=False, double_height=False, double_width=False)
    p.text(CONFIG["RECEIPT_SUB"] + "\n\n")
    p.image(qr_img)                            # QR
    p.text("\n" + CONFIG["RECEIPT_MSG"] + "\n")
    p.set(align="center")
    p.text(f"NO. {vid}\n")
    p.text(f"{datetime.now():%Y-%m-%d %H:%M}\n")
    p.cut()


def next_id():
    """다음 순번 ID 생성 (예: XIST_037). counter.txt에 저장해 재시작해도 이어짐."""
    cfile = CONFIG["COUNTER_FILE"]
    # 마지막 번호 읽기 (없으면 시작번호-1)
    try:
        with open(cfile, "r") as f:
            last = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        last = CONFIG["ID_START"] - 1
    n = last + 1
    # 즉시 저장 (충돌 방지)
    with open(cfile, "w") as f:
        f.write(str(n))
    # 최소 자릿수로 채우되, 넘으면 그대로 확장 (037 → 999 → 1000 → 1001)
    return f"{CONFIG['ID_PREFIX']}{n:0{CONFIG['ID_PAD']}d}"


def process(path):
    """새 영상 하나 처리: 업로드 → QR → 인쇄."""
    if not wait_until_stable(path):
        log(f"! 파일 안정화 실패, 건너뜀: {path}")
        return

    vid = next_id()                           # 순번 ID (예: XIST_037)
    key = f"{vid}{CONFIG['EXT']}"
    try:
        upload_to_r2(path, key)
        page_url = f"{CONFIG['PAGE_BASE']}?v={vid}"   # QR에 담을 주소
        qr = make_qr_image(page_url)
        print_receipt(qr, vid)
        log(f"✓ 처리 완료 [{vid}]  {page_url}")

        # 원본 정리
        done = CONFIG.get("DONE_FOLDER")
        if done:
            os.makedirs(done, exist_ok=True)
            os.replace(path, os.path.join(done, os.path.basename(path)))
    except Exception as e:
        log(f"✗ 처리 실패 [{vid}]: {e}")


class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.lower().endswith(CONFIG["EXT"]):
            log(f"→ 새 영상 감지: {os.path.basename(event.src_path)}")
            process(event.src_path)


def main():
    folder = CONFIG["WATCH_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    log(f"감시 시작: {folder}")
    log("Ctrl+C 로 종료")

    obs = Observer()
    obs.schedule(Handler(), folder, recursive=False)
    obs.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
        log("종료됨")
    obs.join()


if __name__ == "__main__":
    main()
