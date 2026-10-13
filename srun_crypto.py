"""
深澜（Srun）Portal 认证加密算法 —— 移植自杭州电子科技大学校园网登录页 JS。

对应源码：
  static/themes/pro/js/Portal.js   -> _encodeUserInfo / _getToken / _loginAccount
  static/themes/pro/lib/all.min.js -> md5(password, token) 实为 HMAC-MD5；sha1
"""

import base64
import hashlib
import hmac
import json

# 自定义 Base64 字母表（JS: base64.setAlpha('LVoJPi...QA')）
BASE64_ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
STD_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_MASK = 0xFFFFFFFF


def _s(a, b):
    """字符串 -> 32 位字列表（小端）；b=True 时在末尾追加原串长度。"""
    c = len(a)
    v = []
    for i in range(0, c, 4):
        v.append(
            (ord(a[i]) if i < c else 0)
            | ((ord(a[i + 1]) if i + 1 < c else 0) << 8)
            | ((ord(a[i + 2]) if i + 2 < c else 0) << 16)
            | ((ord(a[i + 3]) if i + 3 < c else 0) << 24)
        )
    if b:
        v.append(c)
    return v


def _l(a, b):
    """32 位字列表 -> 字符串（小端）。"""
    d = len(a)
    c = (d - 1) << 2
    if b:
        m = a[d - 1]
        if m < c - 3 or m > c:
            return None
        c = m
    out = []
    for i in range(d):
        w = a[i] & _MASK
        out.append(chr(w & 0xFF))
        out.append(chr((w >> 8) & 0xFF))
        out.append(chr((w >> 16) & 0xFF))
        out.append(chr((w >> 24) & 0xFF))
    s = "".join(out)
    return s[:c] if b else s


def xencode(msg, key):
    """Srun 的 XXTEA 变体。"""
    if msg == "":
        return ""
    v = _s(msg, True)
    k = _s(key, False)
    if len(k) < 4:
        k = k + [0] * (4 - len(k))
    n = len(v) - 1
    z = v[n]
    y = v[0]
    c = 0x86014019 | 0x183639A0
    q = int(6 + 52 / (n + 1))
    d = 0
    p = 0
    while q > 0:
        q -= 1
        d = (d + c) & (0x8CE0D9BF | 0x731F2640)
        e = (d >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            m = (z >> 5) ^ ((y << 2) & _MASK)
            m = (m + (((y >> 3) ^ ((z << 4) & _MASK)) ^ (d ^ y))) & _MASK
            m = (m + ((k[(p & 3) ^ e] ^ z) & _MASK)) & _MASK
            z = v[p] = (v[p] + m) & (0xEFB8D130 | 0x10472ECF)
        p = n  # JS 的 for 循环结束后 p == n，最后一个字用它索引密钥
        y = v[0]
        m = (z >> 5) ^ ((y << 2) & _MASK)
        m = (m + (((y >> 3) ^ ((z << 4) & _MASK)) ^ (d ^ y))) & _MASK
        m = (m + ((k[(p & 3) ^ e] ^ z) & _MASK)) & _MASK
        z = v[n] = (v[n] + m) & (0xBB390742 | 0x44C6F8BD)
    return _l(v, False)


def base64_custom(raw: bytes) -> str:
    """按自定义字母表做 Base64（保留 '=' padding，与前端插件一致）。"""
    std = base64.b64encode(raw).decode()
    return std.translate(str.maketrans(STD_ALPHA, BASE64_ALPHA))


def encode_info(info: dict, token: str) -> str:
    """用户信息加密：{SRBX1} + Base64(xEncode(JSON, token))。"""
    js = json.dumps(info, separators=(",", ":"), ensure_ascii=False)
    enc = xencode(js, token)
    return "{SRBX1}" + base64_custom(enc.encode("latin-1"))


def hmac_md5(password: str, token: str) -> str:
    """对应 JS 的 md5(password, token)（token 作密钥的 HMAC-MD5）。"""
    return hmac.new(token.encode(), password.encode(), hashlib.md5).hexdigest()


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()
