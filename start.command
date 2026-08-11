#!/bin/bash
# ═══════════════════════════════════════════════════════
#  TOUCH / SIGNAL — 파이프라인 실행기 (macOS)
#  이 파일을 더블클릭하면 실행됩니다.
# ═══════════════════════════════════════════════════════

# 스크립트가 있는 폴더로 이동
cd "$(dirname "$0")"

# ── 첫 실행: 가상환경 만들고 패키지 설치 ──
if [ ! -d ".venv" ]; then
  echo "▶ 첫 실행입니다. 환경을 설정합니다 (1~2분 소요)..."
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip --quiet
  ./.venv/bin/pip install -r requirements.txt
  echo "▶ 설정 완료."
  echo ""
fi

# ── 파이프라인 시작 ──
echo "═══════════════════════════════════════════"
echo "  파이프라인 실행 중"
echo "  ⚠️ 이 창을 닫으면 멈춥니다. 전시 내내 켜두세요."
echo "  종료하려면: Ctrl + C"
echo "═══════════════════════════════════════════"
echo ""

./.venv/bin/python pipeline.py

# ── 종료 후 ──
echo ""
echo "▶ 파이프라인이 종료되었습니다."
read -n 1 -s -r -p "아무 키나 누르면 창이 닫힙니다..."
