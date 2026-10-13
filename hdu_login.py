#!/usr/bin/env python3
"""
杭州电子科技大学校园网（深澜 Srun Portal）自动登录脚本。

原理（来自 login.hdu.edu.cn 前端源码）：
  1. GET /cgi-bin/get_challenge?username=&ip=      -> 取 token(challenge)
  2. hmd5   = HMAC-MD5(password, token)            -> 提交为 {MD5}<hmd5>
  3. info   = {SRBX1} + 自定义Base64(xEncode(JSON, token))
  4. chksum = SHA1(token+user + token+hmd5 + token+ac_id + token+ip
                   + token+n + token+type + token+info)
  5. GET /cgi-bin/srun_portal?action=login&...     -> 完成认证

用法：
    python3 hdu_login.py -u 学号 -p 密码
    python3 hdu_login.py -u 学号 -p 密码 --check          # 只看是否在线
    python3 hdu_login.py -u 学号 -p 密码 --logout         # 注销
    python3 hdu_login.py -u 学号 -p 密码 --loop 30        # 每 30s 检测，掉线自动重连
    python3 hdu_login.py -u 学号 -p 密码 --ac-id 0 --ip 10.252.18.95

    # 也可用环境变量，避免密码进历史记录：
    export HDU_USER=学号 HDU_PASS=密码
    python3 hdu_login.py
"""

import argparse
import getpass
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srun_crypto import encode_info, hmac_md5, sha1  # noqa: E402

PORTAL = os.environ.get("HDU_PORTAL", "http://login.hdu.edu.cn")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TYPE = 1          # 认证类型（账号密码）
N = 200           # 深澜固定参数
ENC = "srun_bx1"

# 用于识别 "-p" / "-u" 后面没有跟值的情况（此时改为交互式输入）
PROMPT = "\0PROMPT\0"


# --------------------------------------------------------------------------- #
# 配置文件 / 日志
# --------------------------------------------------------------------------- #
def config_paths():
    """候选配置文件路径（按优先级从高到低）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    paths = [os.path.join(here, "hdu_config.json")]
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        paths.append(os.path.join(base, "hdu-autologin", "config.json"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        paths.append(os.path.join(base, "hdu-autologin", "config.json"))
    return paths


def default_config_path():
    """安装时使用的默认写入位置（用户配置目录）。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "hdu-autologin", "config.json")


def load_config(path=None):
    """读取 JSON 配置；path 为空时按 config_paths() 顺序找第一个存在的。"""
    for p in ([path] if path else config_paths()):
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data["_path"] = p
                    return data
            except Exception as e:
                print(f"[!] 读取配置失败 {p}: {e}")
    return {}


class _Tee:
    """把输出同时写到控制台和日志文件。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for st in self.streams:
            try:
                st.write(data)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# HTTP 辅助
# --------------------------------------------------------------------------- #
def _get_json(url, params, timeout=10, retries=3):
    """带 JSONP 回调的 GET，返回解析后的 dict。失败自动重试。"""
    params = dict(params)
    params["callback"] = "jQuery_cb"
    full = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            m = re.match(r"^\s*jQuery_cb\((.*)\)\s*;?\s*$", body, re.S)
            raw = m.group(1) if m else body
            return json.loads(raw)
        except Exception as e:      # 网络波动 / 超时 / 解析失败
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise last_err


def detect_ac_id(portal=PORTAL, timeout=8, retries=3):
    """访问门户根路径，从 302 Location 里解析 ac_id；失败默认 0。"""
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(portal + "/", headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    final = r.geturl()
            except urllib.error.HTTPError as e:
                final = e.headers.get("Location", "") or ""
            m = (re.search(r"ac_id=(\d+)", final)
                 or re.search(r"index_(\d+)\.html", final))
            if m:
                return m.group(1)
        except Exception:
            pass
        if attempt < retries:
            time.sleep(1.5 * attempt)
    return "0"


# --------------------------------------------------------------------------- #
# 业务接口
# --------------------------------------------------------------------------- #
def query_online(ip=None, portal=PORTAL):
    """查询在线信息；返回 dict（含 user_name 表示已在线）或 None。"""
    try:
        res = _get_json(portal + "/cgi-bin/rad_user_info",
                        {"ip": ip} if ip else {})
    except Exception as e:
        print(f"[!] 查询在线状态失败: {e}")
        return None
    return res if res.get("error") == "ok" else None


def get_challenge(username, ip="", portal=PORTAL):
    """获取 token。ip 传空时，服务器会返回 client_ip（本机校园网 IP）。

    返回 (token, client_ip)。
    """
    res = _get_json(portal + "/cgi-bin/get_challenge",
                    {"username": username, "ip": ip or ""})
    if not res.get("challenge"):
        raise RuntimeError(f"获取 token 失败: {res.get('error_msg') or res}")
    return res["challenge"], (res.get("client_ip") or ip or "").strip()


def resolve_ip(username, ip=None, portal=PORTAL):
    """确定认证 IP：优先用参数，否则由门户 get_challenge 返回的 client_ip 推断。"""
    if ip:
        return ip
    _, client_ip = get_challenge(username, "", portal)
    return client_ip or None


def do_login(username, password, ip, ac_id, portal=PORTAL):
    """执行一次账号密码认证，返回 (成功?, 响应)。"""
    token, client_ip = get_challenge(username, ip, portal)
    ip = ip or client_ip
    if not ip:
        raise RuntimeError("无法确定认证 IP，请用 --ip 手动指定")

    hmd5 = hmac_md5(password, token)
    info = encode_info(
        {"username": username, "password": password, "ip": ip,
         "acid": ac_id, "enc_ver": ENC},
        token,
    )
    chk = (f"{token}{username}{token}{hmd5}{token}{ac_id}{token}{ip}"
           f"{token}{N}{token}{TYPE}{token}{info}")

    params = {
        "action": "login",
        "username": username,
        "password": "{MD5}" + hmd5,
        "os": "Linux",
        "name": "Linux",
        "double_stack": 0,
        "chksum": sha1(chk),
        "info": info,
        "ac_id": ac_id,
        "ip": ip,
        "n": N,
        "type": TYPE,
    }
    res = _get_json(portal + "/cgi-bin/srun_portal", params)
    ok = res.get("error") == "ok" or res.get("suc_msg") == "ip_already_online_error"
    return ok, res


def do_logout(username, ip, ac_id, portal=PORTAL):
    res = _get_json(portal + "/cgi-bin/srun_portal",
                    {"action": "logout", "username": username,
                     "ip": ip, "ac_id": ac_id})
    return res.get("error") == "ok", res


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def _login_once(args):
    ip = args.ip or resolve_ip(args.user, None, args.portal)
    if not ip:
        print("[!] 无法确定认证 IP，请用 --ip 手动指定")
        return False
    ac_id = args.ac_id or detect_ac_id(args.portal)
    print(f"[*] 认证 IP={ip}  ac_id={ac_id}  账号={args.user}")

    info = query_online(ip, args.portal)
    if info and info.get("user_name"):
        print(f"[=] 已在线：{info['user_name']}  计费组={info.get('billing_name','')}")
        return True

    ok, res = do_login(args.user, args.password, ip, ac_id, args.portal)
    if ok:
        print(f"[+] 登录成功：{res.get('suc_msg') or res.get('error_msg') or 'ok'}")
    else:
        print(f"[-] 登录失败：{res.get('error_msg') or res.get('error') or res}")
    return ok


def _logout_once(args):
    ip = args.ip or resolve_ip(args.user, None, args.portal)
    ac_id = args.ac_id or detect_ac_id(args.portal)
    ok, res = do_logout(args.user, ip, ac_id, args.portal)
    print(("[+] 已注销" if ok else f"[-] 注销失败：{res}"))
    return ok


def main():
    ap = argparse.ArgumentParser(
        description="杭电校园网自动登录 (Srun Portal)",
        epilog=("提示：密码以 '-' 开头时请写成 --password=-xxx，"
                "或直接用 -p 交互式输入；含空格/特殊字符时记得加引号。"),
    )
    ap.add_argument("-u", "--user", nargs="?", const=PROMPT,
                    default=os.environ.get("HDU_USER"),
                    help="学号/账号（只写 -u 则交互式输入）")
    ap.add_argument("-p", "--password", nargs="?", const=PROMPT,
                    default=os.environ.get("HDU_PASS"),
                    help="密码（只写 -p 则安全地交互式输入，不留在历史记录里）")
    ap.add_argument("-c", "--config", help="JSON 配置文件路径（默认自动查找）")
    ap.add_argument("--ip", help="认证 IP，默认由门户返回的 client_ip 自动确定")
    ap.add_argument("--ac-id", help="AC ID，默认自动从门户 302 解析")
    ap.add_argument("--portal", help=f"门户地址，默认 {PORTAL}")
    ap.add_argument("--log", help="把输出追加写入该文件（常驻/自启动时用）")
    ap.add_argument("--check", action="store_true", help="只查询在线状态")
    ap.add_argument("--logout", action="store_true", help="注销下线")
    ap.add_argument("--loop", type=int, metavar="SEC", help="循环检测并自动重连")
    args = ap.parse_args()

    # 日志重定向（同时保留控制台输出）
    if args.log:
        try:
            fh = open(args.log, "a", encoding="utf-8", buffering=1)
            sys.stdout = _Tee(sys.stdout, fh)
            sys.stderr = _Tee(sys.stderr, fh)
        except Exception as e:
            print(f"[!] 无法打开日志文件 {args.log}: {e}")

    # 优先级：命令行 > 环境变量 > 配置文件
    cfg = load_config(args.config)
    if cfg.get("_path"):
        print(f"[*] 使用配置：{cfg['_path']}")

    if args.user == PROMPT:
        args.user = input("账号: ").strip()
    elif not args.user:
        args.user = cfg.get("user") or None

    if args.password == PROMPT:
        args.password = getpass.getpass("密码: ")
    elif not args.password:
        args.password = cfg.get("password") or None

    args.ac_id = args.ac_id or cfg.get("ac_id")
    args.ip = args.ip or cfg.get("ip")
    args.portal = args.portal or cfg.get("portal") or PORTAL
    if args.loop is None and cfg.get("interval"):
        try:
            args.loop = int(cfg["interval"])
        except (TypeError, ValueError):
            pass

    if args.check:
        ip = args.ip or (resolve_ip(args.user, None, args.portal) if args.user else None)
        info = query_online(ip, args.portal)
        if info and info.get("user_name"):
            print(f"[=] 在线：{info['user_name']}  IP={info.get('online_ip')}  "
                  f"组={info.get('billing_name','')}  在线秒数={info.get('sum_seconds')}")
        else:
            print("[=] 离线")
        return 0

    if not args.user:
        ap.error("缺少账号：用 -u/--user 指定，或设置 HDU_USER，或写入配置文件")
    if not args.password:
        ap.error("缺少密码：用 -p/--password 指定，或设置 HDU_PASS，"
                 "或写入配置文件，也可只写 -p 交互式输入")

    if args.logout:
        return 0 if _logout_once(args) else 1

    if args.loop:
        print(f"[*] 每 {args.loop}s 检测一次，Ctrl-C 退出")
        while True:
            try:
                _login_once(args)
            except KeyboardInterrupt:
                print("\n[*] 已退出")
                return 0
            except Exception as e:
                print(f"[!] 出错: {e}")
            try:
                time.sleep(args.loop)
            except KeyboardInterrupt:
                print("\n[*] 已退出")
                return 0

    try:
        return 0 if _login_once(args) else 1
    except Exception as e:
        print(f"[!] 出错: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
