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
# 프린터 연결 클래스는 모드에 따라 print_receipt 안에서 불러옴

# ═══════════════════════════════════════════════════════════════
# ▼▼▼ 여기만 수정하면 됩니다 (CONFIG) ▼▼▼
# ═══════════════════════════════════════════════════════════════
CONFIG = {
    # ── 녹화 프로그램(TouchDesigner)이 영상+json을 저장하는 폴더 ──
    "WATCH_FOLDER": "/Users/hayden/XIST_record/recordings",
    "EXT": ".mp4",                             # TD가 mp4로 뽑음

    # ── Cloudflare R2 ──
    "R2_ACCOUNT_ID":  "04a5a85a97c04e037e10fd036987b174",   # ✅ 확인됨
    # ▼▼ 아래 두 키는 채팅에 노출된 것 폐기하고 "새로 발급"한 값을 직접 입력 ▼▼
    "R2_ACCESS_KEY":  "c906ca7de72f4ab9aeb2caad8e56d580",
    "R2_SECRET_KEY":  "019f74c4de962c9e0b47c32c4611f35aef43e32dcab132649772381391870de6",
    "R2_BUCKET":      "xist",                               # ✅ 버킷명
    # 버킷 공개 주소 (끝에 / 없이). index.html의 R2_BASE와 반드시 동일!
    "R2_PUBLIC_BASE": "https://pub-832ff24e01f04404a5738f9ee7512f62.r2.dev",  # ✅ 확인됨

    # ── 다운로드 페이지 주소 (GitHub Pages). QR에 들어감 ──
    # ⚠️ repo명이 대문자 XIST라 URL도 대문자! (대소문자 구분함)
    "PAGE_BASE": "https://rivermin-304.github.io/XIST/",   # ✅ 확인됨

    # ── 영수증 프린터 (AHAPOS CPP-3000) ──
    # CPP-3000은 USB / 유선랜 / RS-232 지원. 연결 방식에 따라 PRINTER_MODE 선택:
    #   "usb"     : USB 케이블 (기본, 대부분 이거)
    #   "network" : 유선랜(RJ-45). 프린터 IP 필요 (안정적)
    #   "serial"  : RS-232 / USB-시리얼로 잡힐 때
    "PRINTER_MODE": "usb",

    # [usb 모드] XP-80T (HSPOS HS-KL80 컨트롤러) — find_id로 확인됨
    "PRINTER_VENDOR_ID":  0x0fe6,
    "PRINTER_PRODUCT_ID": 0x811e,
    "USB_IN_EP":  None,                 # 기본 자동. 안되면 printer_setup.py가 알려주는 값(예: 0x81)
    "USB_OUT_EP": None,                 # 기본 자동. 안되면 예: 0x02
    "PRINTER_PROFILE": None,            # 특수 모델일 때만. 보통 None(제네릭)

    # [network 모드] 유선랜 쓸 때만
    "PRINTER_IP":   "192.168.0.100",
    "PRINTER_PORT": 9100,

    # [serial 모드] USB-시리얼로 잡힐 때만 (예: /dev/cu.usbserial-XXXX)
    "SERIAL_PORT": "/dev/cu.usbserial",
    "SERIAL_BAUD": 9600,

    # QR 인쇄 크기 (CPP-3000 80mm 용지 기준 6~8 적당)
    "QR_SIZE": 7,

    # ── 영수증에 인쇄할 문구/정보 (레퍼런스 레이아웃) ──
    "RECEIPT_TITLE": "XIST",
    "RECEIPT_SUB":   "Interactive Media Art",
    "RECEIPT_MSG":   "QR을 스캔해 당신의 순간을 저장하세요",
    # 상단 전시 정보 (고정)
    "EXH_LINE1": "Sungkyunkwan University Design Society MoD",
    "EXH_LINE2": "11am - 7pm  Tuesday to Sunday",
    # 노브 9개 이름 (고정). 값은 json에서 실시간으로 채워짐
    "KNOB_NAMES": [
        "Filter", "LFO", "Distortion", "Reverb", "Echo",
        "Flanger", "Pitch", "Side Chain", "Synth Volume",
    ],
    # 하단 마무리 문구 (고정)
    "RECEIPT_FOOTER1": "Scan QR code to download",
    "RECEIPT_FOOTER2": "Thank you for your exist!",
    # 노브값 json을 기다리는 최대 시간(초). 이 시간 지나면 00으로 인쇄
    "KNOB_WAIT_SEC": 2.0,

    # 처리 끝난 원본을 옮겨둘 폴더 (재처리 방지). None이면 그냥 둠.
    "DONE_FOLDER": "/Users/hayden/XIST_record/_done",

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


def make_printer():
    """CONFIG의 PRINTER_MODE에 따라 프린터 연결 객체 생성."""
    mode = CONFIG.get("PRINTER_MODE", "usb")
    profile = CONFIG.get("PRINTER_PROFILE")
    if mode == "usb":
        from escpos.printer import Usb
        kwargs = {}
        if CONFIG.get("USB_IN_EP") is not None:  kwargs["in_ep"]  = CONFIG["USB_IN_EP"]
        if CONFIG.get("USB_OUT_EP") is not None: kwargs["out_ep"] = CONFIG["USB_OUT_EP"]
        if profile: kwargs["profile"] = profile
        return Usb(CONFIG["PRINTER_VENDOR_ID"], CONFIG["PRINTER_PRODUCT_ID"], **kwargs)
    elif mode == "network":
        from escpos.printer import Network
        return Network(CONFIG["PRINTER_IP"], port=CONFIG.get("PRINTER_PORT", 9100), profile=profile)
    elif mode == "serial":
        from escpos.printer import Serial
        return Serial(devfile=CONFIG["SERIAL_PORT"], baudrate=CONFIG.get("SERIAL_BAUD", 9600), profile=profile)
    else:
        raise ValueError(f"알 수 없는 PRINTER_MODE: {mode}")


def read_knob_values(video_path):
    """영상과 같은 이름의 .json에서 노브값 9개를 읽는다.
    두 형식 모두 지원:
      1) {"knob1": 0, "knob2": 52, ... "knob9": 0}   ← 김현우 TD 형식
      2) {"knobs": [0, 52, 0, ...]}                    ← 배열 형식
    아직 없으면 잠깐 기다렸다 재확인. 끝내 없으면 None(→ 00으로 인쇄)."""
    import json
    json_path = os.path.splitext(video_path)[0] + ".json"
    deadline = time.time() + CONFIG.get("KNOB_WAIT_SEC", 2.0)
    n = len(CONFIG["KNOB_NAMES"])
    while time.time() < deadline:
        if os.path.exists(json_path):
            try:
                with open(json_path, "r") as f:
                    data = json.load(f)
                # 형식 2: 배열
                if isinstance(data.get("knobs"), list) and data["knobs"]:
                    return data["knobs"]
                # 형식 1: knob1~knob9 개별 키
                if any(f"knob{i}" in data for i in range(1, n + 1)):
                    return [data.get(f"knob{i}", 0) for i in range(1, n + 1)]
            except (ValueError, OSError):
                pass  # 아직 쓰는 중일 수 있음 → 재시도
        time.sleep(0.2)
    return None


def _fmt_knob(v):
    """노브값을 2자리 문자열로. 숫자면 00~99, 아니면 그대로."""
    try:
        return f"{int(v):02d}"
    except (ValueError, TypeError):
        return str(v)[:3]


def print_receipt(qr_img, vid, page_url, knobs=None):
    """레퍼런스 레이아웃 영수증 출력.
    고정: 로고/전시정보/노브이름/QR/마무리문구
    실시간: 날짜·시간, 노브값, 파일명, QR(다운로드 링크)"""
    names = CONFIG["KNOB_NAMES"]
    # 노브값 정렬 (없으면 전부 00)
    if not knobs:
        knobs = [0] * len(names)
    knobs = (list(knobs) + [0] * len(names))[:len(names)]

    p = make_printer()

    # ── 상단: 로고(제목) + 전시정보 (고정) ──
    p.set(align="center", bold=True, double_height=True, double_width=True)
    p.text(CONFIG["RECEIPT_TITLE"] + "\n")
    p.set(align="center", bold=False, double_height=False, double_width=False)
    p.text(CONFIG["EXH_LINE1"] + "\n")
    p.text(CONFIG["EXH_LINE2"] + "\n")

    # ── 날짜/시간 (실시간) ──
    p.set(align="left")
    now = datetime.now()
    # 예: 2026-09-15 TUE, 12:00pm
    stamp = now.strftime("%Y-%m-%d %a, ") + now.strftime("%I:%M%p").lstrip("0").lower()
    p.text(stamp + "\n")
    p.text("-" * 42 + "\n")

    # ── 노브 목록 (이름 고정 + 값 실시간) ──
    for i, name in enumerate(names):
        label = f"Knob {i+1} : {name}"
        val = _fmt_knob(knobs[i])
        # 좌측 라벨 + 우측 값 정렬 (42칸 폭)
        pad = 42 - len(label) - len(val)
        if pad < 1: pad = 1
        p.text(label + " " * pad + val + "\n")

    p.text("-" * 42 + "\n")

    # ── 파일명 (실시간, TOTAL 자리) ──
    total_label = "TOTAL :"
    pad = 42 - len(total_label) - len(vid)
    if pad < 1: pad = 1
    p.set(bold=True)
    p.text(total_label + " " * pad + vid + "\n")
    p.set(bold=False)
    p.text("-" * 42 + "\n\n")

    # ── QR (다운로드 링크) ──
    try:
        p.qr(page_url, size=CONFIG.get("QR_SIZE", 7), center=True)
    except Exception:
        p.set(align="center")
        p.image(qr_img)

    # ── 하단 마무리 (고정) ──
    p.text("\n")
    p.set(align="center", bold=True)
    p.text(CONFIG["RECEIPT_FOOTER1"] + "\n")
    p.set(align="center", bold=False)
    p.text(CONFIG["RECEIPT_FOOTER2"] + "\n")
    p.cut()
    try:
        p.close()
    except Exception:
        pass


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
        knobs = read_knob_values(path)        # 같은 이름의 .json에서 노브값 (없으면 None→00)
        upload_to_r2(path, key)
        page_url = f"{CONFIG['PAGE_BASE']}?v={vid}"   # QR에 담을 주소
        qr = make_qr_image(page_url)
        print_receipt(qr, vid, page_url, knobs=knobs)
        log(f"✓ 처리 완료 [{vid}]  노브값:{'있음' if knobs else '없음(00)'}  {page_url}")

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
