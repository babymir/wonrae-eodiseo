#!/bin/bash
# 사용: find_num.sh <pdf> <정규식> [<정규식2> ...]
# 쪽마다 pdftotext로 글자를 뽑아 정규식이 나오는 쪽과 문장 앞뒤를 보여 준다.
PDF="$1"; shift
N=$(pdfinfo "$PDF" 2>/dev/null | awk '/^Pages:/{print $2}')
if [ -z "$N" ]; then echo "PDF 쪽수를 읽지 못함: $PDF"; exit 1; fi
TOTAL=$(pdftotext -layout "$PDF" - 2>/dev/null | wc -c)
echo "쪽수=$N, 전체 글자 바이트=$TOTAL"
for pat in "$@"; do
  echo "== 패턴: $pat"
  for p in $(seq 1 $N); do
    pdftotext -f $p -l $p "$PDF" - 2>/dev/null | tr '\n' ' ' | grep -oE ".{0,60}${pat}.{0,60}" | sed "s/^/  [p$p] /" | head -3
  done
done
