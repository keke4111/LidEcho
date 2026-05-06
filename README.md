# LidEcho

LidEcho 是一个手动运行的 Ubuntu 合盖行为辅助脚本。它只在 YesPlayMusic 正在播放音频时，临时阻止合盖触发休眠，让音乐继续播放。

它不会让屏幕继续亮着。合盖后屏幕熄灭是正常行为，脚本只处理“合盖是否进入休眠”。

## 安全边界

这个项目不会污染系统：

- 不修改 `/etc/systemd/logind.conf`
- 不创建或安装 systemd service
- 不设置开机自启
- 不写入 `~/.config/systemd/user`
- 不写入 `~/.local/bin`
- 不改变 GNOME 电源设置
- 只在当前目录保存脚本和说明文件

脚本使用 `systemd-logind` 的动态 D-Bus inhibitor。这个 inhibitor 依赖一个临时文件描述符。脚本运行时保持它打开，脚本退出、崩溃、被 `Ctrl+C` 停止或被系统杀掉后，文件描述符会关闭，系统会恢复原本合盖行为。

脚本带有单实例保护。同一个用户重复启动时，新的进程会提示 `LidEcho is already running.` 并退出，避免同时运行多个实例。

## 依赖

Ubuntu 上建议安装：

```bash
sudo apt install python3-dbus pulseaudio-utils pipewire-bin
```

说明：

- `python3-dbus` 用于调用 `systemd-logind`。
- `pulseaudio-utils` 提供 `pactl`，用于检测音频流。
- `pipewire-bin` 提供 PipeWire 相关命令，作为回退检测方式。

## 运行

在项目目录中运行：

```bash
cd /home/keke4/Documents/Codex_Documents/LidEcho
python3 lid_manager.py
```

或者：

```bash
./lid_manager.py
```

保持这个终端开着。播放 YesPlayMusic 时，脚本会临时阻止合盖休眠；停止播放或关闭 YesPlayMusic 后，默认等待 30 秒自动释放。

## 右键运行

如果文件管理器支持运行 Python 脚本，可以直接右键运行：

```text
lid_manager.py
```

`lid_manager.py` 不依赖当前目录中的其他脚本文件。你可以把它移动到任意位置后右键运行。

如果脚本发现自己不是从终端启动，会自动尝试打开一个终端窗口重新运行自己。请保持这个终端窗口打开。要停止脚本，在终端里按 `Ctrl+C`。

如果已经有一个实例正在运行，再次右键运行会直接退出，不会创建第二个 inhibitor。

## 停止

在运行脚本的终端按：

```text
Ctrl+C
```

退出后系统会恢复原本合盖行为。

如果需要从另一个终端停止，可以先找到进程：

```bash
pgrep -af lid_manager.py
```

然后结束它：

```bash
kill <PID>
```

## 验证状态

查看当前 systemd inhibitor：

```bash
systemd-inhibit --list
```

当 YesPlayMusic 正在播放并且脚本生效时，应能看到类似这一行：

```text
LidEcho  1000  keke4  <PID>  python3  handle-lid-switch  YesPlayMusic is playing audio
```

如果停止播放、关闭 YesPlayMusic 或退出脚本，等待最多约 30 秒后，`LidEcho` 这一行应该消失。

## 工作原理

脚本循环执行以下逻辑：

```text
检查音频流
    ↓
发现 YesPlayMusic 的音频流处于 RUNNING
    ↓
通过 D-Bus 调用 systemd-logind 的 Inhibit
    ↓
临时阻止 handle-lid-switch
    ↓
YesPlayMusic 停止播放或脚本退出
    ↓
关闭 inhibitor fd
    ↓
系统恢复原本合盖行为
```

调用的 logind 接口是：

```text
org.freedesktop.login1.Manager.Inhibit
```

使用的 inhibitor 目标是：

```text
handle-lid-switch
```

这表示脚本只阻止“合盖触发休眠”，不阻止手动点击挂起、电量保护或其他系统电源策略。

## 可选参数

显示帮助：

```bash
python3 lid_manager.py --help
```

开启调试日志：

```bash
python3 lid_manager.py --debug
```

调整轮询间隔，默认 5 秒：

```bash
python3 lid_manager.py --interval 10
```

调整停止播放后的释放等待时间，默认 30 秒：

```bash
python3 lid_manager.py --release-grace 15
```

增加匹配的应用名：

```bash
python3 lid_manager.py --target YesPlayMusic --target yesplaymusic
```

## 系统负载

脚本默认每 5 秒检查一次音频状态，其余时间睡眠。CPU 占用通常接近 0，内存占用是一个普通 Python 常驻进程的水平。

合盖播放音乐时，真正的耗电和发热主要来自系统继续运行、音频播放和网络活动，而不是这个脚本本身。
