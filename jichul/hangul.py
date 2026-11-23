"""금액 -> 한글 표기. 회계 매뉴얼 규칙: 한글에는 '일'도 적는다 (일십, 일백, 일천 ...).

예) 59,110 -> 금 오만구천일백일십원정   (샘플 260730 지출결의서와 동일)
    327,600 -> 금 삼십이만칠천육백원정
이 변환은 LLM이 아니라 코드로 한다. (샘플 중에는 숫자와 한글이 어긋난 것이 실제로 있다.)
"""

_DIGIT = "영일이삼사오육칠팔구"
_SMALL = ["", "십", "백", "천"]
_BIG = ["", "만", "억", "조"]


def _four(n: int) -> str:
    """0 < n < 10000 을 한글로. 1도 생략하지 않는다 (일십, 일백, 일천)."""
    out = []
    for pos in range(3, -1, -1):
        d = (n // 10**pos) % 10
        if d == 0:
            continue
        out.append(_DIGIT[d] + _SMALL[pos])
    return "".join(out)


def to_hangul(n: int) -> str:
    if n < 0:
        raise ValueError("음수 금액은 지원하지 않습니다")
    if n == 0:
        return "영"
    parts = []
    unit = 0
    while n > 0:
        chunk = n % 10000
        if chunk:
            parts.append(_four(chunk) + _BIG[unit])
        n //= 10000
        unit += 1
    return "".join(reversed(parts))


def amount_phrase(n: int) -> str:
    """'금 오만이천원 정' 형태 (양식 기본 문구와 동일한 띄어쓰기)."""
    return f"금 {to_hangul(n)}원 정"


def amount_numeric(n: int) -> str:
    return f"(\\ {n:,})"
