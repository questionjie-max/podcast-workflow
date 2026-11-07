"""i18n 一致性：中英词典键集合必须一致，引用的键必须存在。

只做「静态一致性」检查，不需要浏览器。动态键（`t("state." + s)`）通过白名单
前缀放行，避免把真问题掩盖成噪声。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "podcast_studio" / "server" / "static"

# 运行时拼接的键：前缀匹配即视为已覆盖
DYNAMIC_PREFIXES = ("state.", "step.", "voices.group.", "provider.")

_KEY_RE = re.compile(r'"([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+)"\s*:')
_REF_RE = re.compile(r'data-i18n(?:-ph)?="([^"]+)"')
# t(...) 内部可能有三元表达式（t(a ? "k1" : "k2")），抓取整段调用里的所有键
_T_CALL_ARGS_RE = re.compile(r'\bt\(([^()]*)\)')
_T_KEY_IN_ARGS_RE = re.compile(r'["\']([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)+)["\']')


def _t_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for args in _T_CALL_ARGS_RE.findall(text):
        keys |= set(_T_KEY_IN_ARGS_RE.findall(args))
    return keys


def _dicts() -> tuple[set[str], set[str]]:
    src = (STATIC / "i18n.js").read_text(encoding="utf-8")
    en = src.split("const I18N_EN = {", 1)[1].split("\n};", 1)[0]
    zh = src.split("const I18N_ZH = {", 1)[1].split("\n};", 1)[0]
    return set(_KEY_RE.findall(en)), set(_KEY_RE.findall(zh))


def test_en_zh_key_parity():
    en, zh = _dicts()
    assert en, "英文词典为空"
    assert not (en - zh), f"中文缺键：{sorted(en - zh)}"
    assert not (zh - en), f"英文缺键：{sorted(zh - en)}"


def test_all_referenced_keys_exist():
    en, _ = _dicts()
    refs: set[str] = set()
    for name in ("index.html", "app.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        refs |= set(_REF_RE.findall(text))
        refs |= _t_keys(text)
    missing = sorted(refs - en)
    assert not missing, f"引用了词典里不存在的键：{missing}"


def test_no_dead_keys():
    """定义了却没人用的键 = 翻译白做 / 该接线没接。"""
    en, _ = _dicts()
    used: set[str] = set()
    for name in ("index.html", "app.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        used |= set(_REF_RE.findall(text))
        used |= _t_keys(text)
    dead = sorted(
        k for k in en
        if k not in used and not any(k.startswith(p) for p in DYNAMIC_PREFIXES)
    )
    assert not dead, f"死键（定义但未接线）：{dead}"


def test_switch_language_covers_both_dicts():
    """切语言时动态内容也要重画（回归：en→zh 曾不还原）。"""
    src = (STATIC / "i18n.js").read_text(encoding="utf-8")
    assert "I18N_ZH" in src and "I18N_EN" in src
    assert "function toggleLang" in src
    # applyI18n 必须遍历两个属性，且缺键时不覆盖(用 !== undefined 判断)
    assert "data-i18n]" in src and "data-i18n-ph]" in src
    assert "!== undefined" in src
