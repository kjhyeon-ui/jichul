from jichul.hangul import to_hangul

# 실제 샘플 지출결의서에 적힌 값
CASES = {
    310_000: "삼십일만",
    264_000: "이십육만사천",
    59_110: "오만구천일백일십",
    327_600: "삼십이만칠천육백",
    858_900: "팔십오만팔천구백",
    52_000: "오만이천",  # 양식 기본값
}


def test_samples():
    for n, want in CASES.items():
        assert to_hangul(n) == want, (n, to_hangul(n), want)


def test_il_rule():
    assert to_hangul(11_000) == "일만일천"
    assert to_hangul(100_000) == "일십만"
    assert to_hangul(1_000) == "일천"
    assert to_hangul(10) == "일십"


def test_big():
    assert to_hangul(6_353_312) == "육백삼십오만삼천삼백일십이"


def test_sample_230310_is_inconsistent():
    # 샘플 _230310 의 한글 '육백삼십육만칠백일십이' 는 6,360,712 이고 숫자는 6,353,312 이다.
    assert to_hangul(6_360_712) == "육백삼십육만칠백일십이"
    assert to_hangul(6_353_312) != "육백삼십육만칠백일십이"
