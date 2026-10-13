# 杭电校园网自动登录（Srun Portal）

根据 `login.hdu.edu.cn` 前端源码逆向实现的杭州电子科技大学校园网自动登录脚本。
纯 Python 标准库，无第三方依赖。

## 文件

| 文件 | 说明 |
|------|------|
| `hdu_login.py` | 命令行主程序（探测在线、登录、注销、掉线重连） |
| `srun_crypto.py` | 从网页 JS 移植的加密算法（HMAC-MD5 / SHA1 / xEncode / 自定义 Base64） |

## 用法

```bash
# 登录（密码走命令行）
python3 hdu_login.py -u 学号 -p 密码

# 只写 -p，交互式安全输入密码（不留在 shell 历史里，推荐）
python3 hdu_login.py -u 学号 -p

# 用环境变量，避免密码出现在 history / 进程列表里
export HDU_USER=学号
export HDU_PASS=密码
python3 hdu_login.py

# 只查询在线状态
python3 hdu_login.py -u 学号 --check

# 注销下线
python3 hdu_login.py -u 学号 -p 密码 --logout

# 每 30 秒检测一次，掉线自动重连（适合开机自启常驻）
python3 hdu_login.py -u 学号 -p 密码 --loop 30
```

## 工作原理（对应前端源码）

前端 `Portal.js` 的 `_loginAccount` 流程，脚本 1:1 复刻：

1. `GET /cgi-bin/get_challenge?username=&ip=` → 拿到 `challenge` 作为 token
   （`ip` 传空时服务器返回 `client_ip`，即本机校园网 IP，脚本据此自动定位）
2. `hmd5 = HMAC-MD5(key=token, msg=password)`
   注：JS 里写的 `md5(password, token)` 实际是 **HMAC-MD5**（`all.min.js` 里
   `md5(s,t,r)` 带第二参数时走 HMAC 分支），不是普通 MD5。
3. `info = "{SRBX1}" + 自定义Base64(xEncode(JSON, token))`
   - `xEncode` 是深澜定制的 XXTEA
   - Base64 用自定义字母表 `LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA`
   - **保留 `=` padding**
4. `chksum = SHA1(token+user + token+hmd5 + token+ac_id + token+ip
                  + token+n + token+type + token+info)`，`n=200`, `type=1`
5. `GET /cgi-bin/srun_portal?action=login&...` 完成认证
6. 在线查询：`GET /cgi-bin/rad_user_info`；注销：`action=logout`

## 验证情况

- 三处加密输出（hmd5 / chksum / info）已与网页真实 JS（含 `all.min.js` 的
  md5、sha1、base64 插件）逐字节比对一致。
- 真实接口验证：错误密码提交后服务器返回 `error: ok` +
  `suc_msg: ip_already_online_error`，说明签名与 `info` 解密全部通过
  （仅因当前 IP 已在线而未重复登录）。

## 说明

- `ac_id` 默认从门户 302 重定向自动解析（杭电为 `0`），也可用 `--ac-id` 指定。
- 若接口偶发超时，脚本内置了重试。
- 仅供本人合法登录校园网使用。

## 常见报错

**`error: argument -p/--password: expected one argument`**
说明 `-p` 后面没解析到密码值，常见三种原因：

1. `-p` 写在命令末尾、后面没跟密码 —— 补上密码，或只写 `-p` 回车后交互式输入。
2. 密码以 `-` 开头（如 `-abc`），被 argparse 当成新选项 —— 改用 `--password=-abc`。
3. 密码含空格/`$` 等特殊字符却没加引号 —— 用单引号包起来，如 `-p 'p@ss$word'`。

小技巧：只写 `-p` 会交互式读取密码，既不受上述问题影响，也不进入 shell 历史记录。

## 开机自动启动（Linux / Windows / macOS）

用 `autostart.py` 一键注册为开机（登录）自启动的常驻服务，掉线自动重连：

```bash
# 安装（首次带上账号密码，之后就不用了）
python3 autostart.py install -u 学号 -p 密码

# 查看状态
python3 autostart.py status

# 卸载
python3 autostart.py uninstall
```

可选参数：

```bash
python3 autostart.py install -i 30          # 检测间隔秒数（默认 30）
python3 autostart.py install --config-local # 配置放脚本目录（默认放用户配置目录）
python3 autostart.py install --systemd-linger  # Linux：未登录也运行（需 sudo）
```

各平台实现方式：

| 平台 | 机制 | 说明 |
|------|------|------|
| Linux | systemd 用户服务 | 无 systemd 时自动回退到 crontab `@reboot` |
| Windows | 任务计划程序 | 登录时触发，用 VBS 隐藏窗口后台运行 |
| macOS | launchd LaunchAgent | `~/Library/LaunchAgents` |

配置文件位置：

- Linux/macOS：`~/.config/hdu-autologin/config.json`
- Windows：`%APPDATA%\hdu-autologin\config.json`

```json
{
  "user": "学号",
  "password": "密码",
  "interval": 30
}
```

> 配置里含明文密码，文件权限已设为仅本人可读（0600），且已被 `.gitignore` 排除，请勿提交。

日志：

- Linux：`~/.local/state/hdu-autologin/hdu_login.log`（或 `journalctl --user -u hdu-autologin -f`）
- Windows/macOS：同上工作目录下的 `hdu_login.log`

> Windows 上若提示权限不足，请用「管理员身份」打开终端后重试。

## 直接手动常驻（不想装服务时）

```bash
python3 hdu_login.py -u 学号 -p 密码 --loop 30 --log ~/hdu.log
```
