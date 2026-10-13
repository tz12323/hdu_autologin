#!/usr/bin/env python3
"""
杭电校园网自动登录 —— 跨平台自启动安装器（Linux / Windows / macOS）。

它会在系统里注册一个"开机/登录后自动运行"的任务，运行：
    python hdu_login.py --loop <interval> --log <logfile>
即常驻后台，检测到掉线自动重连。

用法：
    python3 autostart.py install            # 安装并立即启用
    python3 autostart.py uninstall          # 卸载
    python3 autostart.py status             # 查看状态
    python3 autostart.py install --interval 30 --user 学号 --password 密码
    python3 autostart.py install --config /path/to/config.json
    python3 autostart.py install --systemd-linger   # Linux: 未登录也运行(需 sudo)

平台实现：
    Linux   -> systemd 用户服务（无 systemd 时回退 crontab @reboot）
    Windows -> 任务计划程序（Task Scheduler，登录时触发，后台无窗口）
    macOS   -> launchd LaunchAgent
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOGIN_PY = os.path.join(HERE, "hdu_login.py")

sys.path.insert(0, HERE)
from hdu_login import default_config_path  # noqa: E402
SERVICE_NAME = "hdu-autologin"
TASK_NAME = "HDU-AutoLogin"

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------- #
# 公共
# --------------------------------------------------------------------------- #
def find_python():
    """返回当前解释器路径。"""
    return os.path.abspath(sys.executable)


def find_pythonw():
    """Windows: 优先用 pythonw.exe（无控制台窗口）。"""
    exe = find_python()
    cand = exe.replace("python.exe", "pythonw.exe")
    return cand if os.path.isfile(cand) else exe


def work_dir():
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or HERE, "hdu-autologin")
    return os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "hdu-autologin",
    )


def log_path():
    return os.path.join(work_dir(), "hdu_login.log")


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path


def write_config(cfg_path, user, password, interval, extra=None):
    """写配置文件（仅含显式传入的字段）。"""
    cfg = {}
    if user:
        cfg["user"] = user
    if password:
        cfg["password"] = password
    if interval:
        cfg["interval"] = int(interval)
    if extra:
        cfg.update(extra)
    ensure_dir(os.path.dirname(cfg_path))
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(cfg_path, 0o600)
    except Exception:
        pass
    return cfg_path


def run(cmd, check=True, capture=False):
    """执行命令并打印。"""
    print("  $ " + " ".join(str(c) for c in cmd))
    try:
        sys.stdout.flush()
    except Exception:
        pass
    return subprocess.run(
        cmd, check=check, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


# --------------------------------------------------------------------------- #
# Linux: systemd 用户服务
# --------------------------------------------------------------------------- #
def systemd_available():
    if not IS_LINUX or not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0 or "running" in (r.stdout or "")
    except Exception:
        return False


def systemd_unit_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "systemd", "user", f"{SERVICE_NAME}.service")


def systemd_unit_text(cfg_path, interval, log):
    py = find_python()
    return f"""[Unit]
Description=HDU Campus Network Auto Login
Documentation=file://{LOGIN_PY}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={py} {LOGIN_PY} --config {cfg_path} --loop {interval} --log {log}
Restart=always
RestartSec=30
# 日志由 --log 统一写入，这里交给 journald，避免重复
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def linux_install(args, cfg_path):
    log = log_path()
    ensure_dir(os.path.dirname(log))

    if systemd_available():
        unit = systemd_unit_path()
        ensure_dir(os.path.dirname(unit))
        with open(unit, "w", encoding="utf-8") as f:
            f.write(systemd_unit_text(cfg_path, args.interval, log))
        print(f"[+] 已写入 systemd 用户服务：{unit}")
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"])
        if args.systemd_linger:
            print("[*] 开启 linger（未登录也运行，需要 sudo）...")
            run(["sudo", "loginctl", "enable-linger", os.environ.get("USER", "")])
        print(f"[+] 完成。查看状态：systemctl --user status {SERVICE_NAME}")
        print(f"[*] 日志：journalctl --user -u {SERVICE_NAME} -f  或  {log}")
        return 0

    # 回退：crontab @reboot
    if not shutil.which("crontab"):
        print("[!] 系统既没有 systemd 也没有 crontab，无法安装自启动。")
        print("    可手动把下面这行加入你的启动项：")
        print(f"    {find_python()} {LOGIN_PY} --config {cfg_path} --loop {args.interval} --log {log}")
        return 1

    line = (f"@reboot {find_python()} {LOGIN_PY} --config {cfg_path} "
            f"--loop {args.interval} --log {log}")
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = cur.stdout if cur.returncode == 0 else ""
    lines = [l for l in existing.splitlines() if SERVICE_NAME not in l]
    lines.append(f"# {SERVICE_NAME}")
    lines.append(line)
    new = "\n".join(lines).strip() + "\n"
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cron") as tf:
        tf.write(new)
        tmp = tf.name
    try:
        run(["crontab", tmp])
        print(f"[+] 已写入 crontab @reboot：{line}")
        print("[*] 注意：crontab 方式在开机时运行，掉线重连由 --loop 负责。")
    finally:
        os.unlink(tmp)
    return 0


def linux_uninstall():
    if systemd_available() and os.path.isfile(systemd_unit_path()):
        run(["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"], check=False)
        os.unlink(systemd_unit_path())
        run(["systemctl", "--user", "daemon-reload"], check=False)
        print("[+] 已移除 systemd 用户服务")
        return 0
    if shutil.which("crontab"):
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if cur.returncode == 0:
            lines = [l for l in cur.stdout.splitlines()
                     if SERVICE_NAME not in l and LOGIN_PY not in l]
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cron") as tf:
                tf.write("\n".join(lines).strip() + "\n")
                tmp = tf.name
            try:
                run(["crontab", tmp], check=False)
                print("[+] 已从 crontab 移除")
            finally:
                os.unlink(tmp)
    return 0


# --------------------------------------------------------------------------- #
# Windows: 任务计划程序
# --------------------------------------------------------------------------- #
def win_vbs_path():
    return os.path.join(work_dir(), "run_hidden.vbs")


def win_write_vbs(cfg_path, interval, log):
    """写一个隐藏窗口启动的 VBS（任务计划调用它）。

    用 Chr(34) 拼引号，避免 VBS 嵌套双引号的转义问题。
    """
    ensure_dir(work_dir())
    py = find_pythonw()
    args = [py, LOGIN_PY, "--config", cfg_path,
            "--loop", str(interval), "--log", log]
    # 每个参数用 Chr(34) 包起来，参数之间加空格
    quoted = [f'q & "{a}" & q' for a in args]
    cmd_expr = ' & " " & '.join(quoted)
    vbs = (
        'Set sh = CreateObject("WScript.Shell")\r\n'
        'q = Chr(34)\r\n'
        f'cmd = {cmd_expr}\r\n'
        'sh.Run cmd, 0, False\r\n'
    )
    with open(win_vbs_path(), "w", encoding="utf-8") as f:
        f.write(vbs)
    return win_vbs_path()


def windows_install(args, cfg_path):
    log = log_path()
    ensure_dir(work_dir())
    vbs = win_write_vbs(cfg_path, args.interval, log)
    print(f"[+] 已写入启动脚本：{vbs}")

    tr = f'wscript.exe "{vbs}"'
    # 先删旧的，避免同名冲突
    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                   capture_output=True, text=True)
    run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
         "/SC", "ONLOGON", "/RL", "LIMITED", "/F"])
    print(f"[+] 已注册计划任务：{TASK_NAME}（登录时自动启动，无窗口）")
    print(f"[*] 日志：{log}")
    print(f"[*] 立即运行一次：schtasks /Run /TN {TASK_NAME}")
    print("[!] 若提示权限不足，请用『管理员身份』打开终端后重试。")
    return 0


def windows_uninstall():
    r = run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False, capture=True)
    if r.returncode == 0:
        print("[+] 已删除计划任务")
    else:
        print("[!] 未找到计划任务或删除失败")
    for p in (win_vbs_path(),):
        if os.path.isfile(p):
            os.unlink(p)
            print(f"[+] 已删除 {p}")
    return 0


# --------------------------------------------------------------------------- #
# macOS: launchd
# --------------------------------------------------------------------------- #
def mac_plist_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/com.hdu.{SERVICE_NAME}.plist")


def mac_install(args, cfg_path):
    log = log_path()
    ensure_dir(os.path.dirname(log))
    plist = mac_plist_path()
    ensure_dir(os.path.dirname(plist))
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.hdu.{SERVICE_NAME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{find_python()}</string>
    <string>{LOGIN_PY}</string>
    <string>--config</string><string>{cfg_path}</string>
    <string>--loop</string><string>{args.interval}</string>
    <string>--log</string><string>{log}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""
    with open(plist, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[+] 已写入 LaunchAgent：{plist}")
    run(["launchctl", "unload", plist], check=False)
    run(["launchctl", "load", plist], check=False)
    print(f"[+] 完成。日志：{log}")
    return 0


def mac_uninstall():
    plist = mac_plist_path()
    if os.path.isfile(plist):
        run(["launchctl", "unload", plist], check=False)
        os.unlink(plist)
        print("[+] 已移除 LaunchAgent")
    return 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def cmd_install(args):
    # 配置文件位置：优先 --config，其次 --config-local（脚本同目录），否则用户配置目录
    if args.config:
        cfg_path = args.config
    elif args.config_local:
        cfg_path = os.path.join(HERE, "hdu_config.json")
    else:
        cfg_path = default_config_path()
    extra = {}
    if args.portal:
        extra["portal"] = args.portal
    if args.ac_id:
        extra["ac_id"] = args.ac_id

    # 没有显式给账号密码时，尝试沿用已有配置
    user, password = args.user, args.password
    if os.path.isfile(cfg_path):
        try:
            old = json.load(open(cfg_path, encoding="utf-8"))
            user = user or old.get("user")
            password = password or old.get("password")
        except Exception:
            pass
    write_config(cfg_path, user, password, args.interval, extra)
    print(f"[+] 配置已写入：{cfg_path}")

    if not user or not password:
        print("[!] 配置里还没有账号/密码。请编辑上面的文件填入 user 和 password，")
        print("    或重跑：python autostart.py install -u 学号 -p 密码")
        print("    （脚本仍会安装，启动后读到配置即可工作）")

    if IS_WIN:
        return windows_install(args, cfg_path)
    if IS_MAC:
        return mac_install(args, cfg_path)
    if IS_LINUX:
        return linux_install(args, cfg_path)
    print(f"[!] 不支持的系统：{platform.system()}")
    return 1


def cmd_uninstall(args):
    if IS_WIN:
        return windows_uninstall()
    if IS_MAC:
        return mac_uninstall()
    if IS_LINUX:
        return linux_uninstall()
    print(f"[!] 不支持的系统：{platform.system()}")
    return 1


def cmd_status(args):
    print(f"系统：{platform.system()} {platform.release()}")
    print(f"Python：{find_python()}")
    print(f"工作目录：{work_dir()}")
    print(f"日志：{log_path()}")
    if IS_LINUX and systemd_available():
        unit = systemd_unit_path()
        print(f"服务文件：{unit}（{'存在' if os.path.isfile(unit) else '不存在'}）")
        run(["systemctl", "--user", "status", f"{SERVICE_NAME}.service", "--no-pager"],
            check=False)
    elif IS_WIN:
        run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
            check=False, capture=False)
    elif IS_MAC:
        plist = mac_plist_path()
        print(f"LaunchAgent：{plist}（{'存在' if os.path.isfile(plist) else '不存在'}）")
        run(["launchctl", "list"], check=False, capture=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description="杭电校园网自动登录 · 自启动安装器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("install", help="安装并启用自启动")
    pi.add_argument("-u", "--user", help="学号/账号")
    pi.add_argument("-p", "--password", help="密码")
    pi.add_argument("-i", "--interval", type=int, default=30,
                    help="检测间隔秒数，默认 30")
    pi.add_argument("-c", "--config", help="配置文件路径")
    pi.add_argument("--config-local", action="store_true",
                    help="把配置放在脚本同目录（默认放用户配置目录）")
    pi.add_argument("--portal", help="门户地址（一般不用改）")
    pi.add_argument("--ac-id", help="AC ID（一般不用改）")
    pi.add_argument("--systemd-linger", action="store_true",
                    help="Linux：开启 linger，使未登录时也运行（需要 sudo）")
    pi.set_defaults(func=cmd_install)

    pu = sub.add_parser("uninstall", help="卸载自启动")
    pu.set_defaults(func=cmd_uninstall)

    ps = sub.add_parser("status", help="查看状态")
    ps.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
