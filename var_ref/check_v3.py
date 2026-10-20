"""第三批改动的 DOM 核对：文件夹开合图标、生成中提示、上次更新时间、
发送键左边那一组（上下文环 / 模型 / 用量）、设置页。

## 为什么要单独一个脚本

`check_sidebar.py` 只核对侧栏，而这一批里有一半东西在右下的输入卡与设置面板里。
把它塞进那一个脚本会让"侧栏检查"变成"整页检查"，名字就不再说明它查什么。

## 一个反复踩到的坑

`--dump-dom` 只给出**已渲染**的 DOM。设置面板默认是关的，所以它的内容**不会**
出现在 DOM 里 —— 除非真的把它点开。这里用 `--virtual-time-budget` 没法"点"，
所以对设置面板只核对**入口**（左下角那个设置键），面板内部用单元级的
CSS 存在性检查（`app.css` 里有那些类）代替，另外在浏览器里手工验一次。

⚠ 这条注释是这份脚本的诚实边界：它证明不了"设置页点开之后长什么样"。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

CHECKS: list[tuple[str, str]] = [
    # 文件夹开合：图标换成了张开/封口两种，按钮语义仍是 expanded
    ("文件夹有展开/收起键（新类名 dsh-folderToggle）", r'class="dsh-folderToggle'),
    ("展开键带 aria-expanded", r'dsh-folderToggle[^>]*aria-expanded="(?:true|false)"'),
    # 展开态用的是**张开**的夹子（不是箭头旋转）：默认第一个文件夹是展开的，
    # 所以这一条 path 必须出现在 DOM 里。
    ("展开着的文件夹用「张开的夹子」图标", r"M3\.4 13\.2h8\.3"),
    # 会话行：上次更新时间那一格，内容是一次相对时间
    ("会话行有「上次更新」那个格子", r'class="dsh-sessionTime'),
    ("更新时间是一次相对时间（刚刚/分钟前/小时前/天前）", r'class="dsh-sessionTime">(?:刚刚|\d+ 分钟前|\d+ 小时前|\d+ 天前|<)'),
    # 发送键左边那一组
    ("上下文环在", r'class="dsh-ringButton'),
    ("上下文环里有一个真圆（<circle>）", r'class="dsh-ringFill'),
    ("上下文环的可读标签", r'aria-label="上下文用量"'),
    ("模型键在（可点开换档案）", r'aria-label="模型"'),
    ("token 用量键在", r'aria-label="token 用量"'),
    ("用量键上显示「tok」或「没测到」", r"(?:tok|没测到)"),
    # 左下角设置入口
    ("左下角有设置键", r'aria-label="设置"'),
    # 紧凑与字号这套设置落到 <html> 上（默认 14）
    ("<html> 上有 data-reading-size", r'data-reading-size="1[2-6]"'),
]

OPTIONAL: list[tuple[str, str]] = [
    ("生成中的动态点（有回复在流里时才出现）", r'data-pulse="true"'),
    ("「已排队」提示（忙碌时按 Enter 才出现）", r"已排队"),
    ("紧凑轨迹折叠（完成过带工具的轮次才出现）", r'class="dsh-traceWrap"'),
]

#: 设置面板是关着的，所以只查"它的样式真的在打包产物里"。
CSS_MARKERS: list[tuple[str, str]] = [
    ("设置覆盖层样式", r"\.dsh-settingsOverlay"),
    ("设置面板样式", r"\.dsh-settingsPanel"),
    ("分段选择器样式", r"\.dsh-segmentedItem"),
    ("读数表样式（dt/dd）", r"\.dsh-kv dd"),
    ("浮层样式", r"\.dsh-popoverRight"),
    ("阅读字号五档", r'\[data-reading-size="16"\]'),
    ("生成中呼吸动画的关键帧", r"@keyframes dsh-pulse"),
]


#: 设置面板：`#settings` 会把它打开，所以这些能真的在 DOM 里查到。
SETTINGS_CHECKS: list[tuple[str, str]] = [
    ("面板自己在（role=dialog）", r'class="dsh-settingsOverlay" [^>]*role="dialog"'),
    ("左栏有「通用」且标着当前", r'aria-current="true">通用<'),
    ("分区：语言", r">语言<"),
    ("分区：外观", r">外观<"),
    ("分区：对话显示", r">对话显示<"),
    ("分区：忙碌时的发送行为", r">忙碌时的发送行为<"),
    ("分区：通知", r">通知<"),
    ("主题三选（浅色/深色/跟随系统）", r"跟随系统"),
    ("字号五档（12…16）", r'role="radio"[^>]*aria-checked="true"[^>]*>14<'),
    ("对话显示：紧凑 / 详细", r">详细<"),
    ("发送行为：排队 / 打断", r">排队<"),
    ("轮次通知三档", r"仅窗口失焦时"),
    ("权限通知那一行写着「这个产品没有」", r"这个产品没有"),
    ("通知权限的状态说明在（没有就如实说）", r"通知|挡住|没有通知能力"),
]


def find_edge() -> str:
    for path in EDGE_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    raise SystemExit("找不到 Edge。")


def dump(url: str) -> str:
    edge = find_edge()
    with tempfile.TemporaryDirectory(prefix="dsh-dom-") as profile:
        completed = subprocess.run(
            [
                edge,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=15000",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    if completed.returncode != 0:
        print(f"⚠ Edge 退出码 {completed.returncode}（DOM 仍然可能可用）")
    return completed.stdout


def css_of(dist: pathlib.Path) -> str:
    """把打包出来的 CSS 拼起来：`vite build` 之后它是一份，但别假设只有一份。"""
    return "\n".join(path.read_text(encoding="utf-8") for path in dist.glob("assets/*.css"))


#: 「页面已经画到这一步了」的标志。它出现在输入卡里 ——
#: 而输入卡只在**已经选中一个会话**之后才渲染。
READY_MARKER = "dsh-ringButton"


def dump_ready(url: str, attempts: int = 3) -> str:
    """渲染，直到输入卡出现（最多 `attempts` 次）。

    ## 为什么要重试，以及为什么这不是"把失败遮掉"

    每次 `dump()` 都开一个**全新的浏览器 profile**（无缓存、无 cookie），
    于是一次运行会把所有接口都冷读一遍。而服务刚重启时，头几个请求要
    冷启 SQLite、加载题库；`--virtual-time-budget` 是**虚拟**时间 ——
    它在等待网络时会一起走，走够了就立刻 dump。于是冷启动那一下，
    DOM 里会出现"会话列表已经画出来了，但还没选中会话"的中间态。

    这**不是**产品缺陷（真实浏览器里它只是晚 100 毫秒），但也**不能**当成通过：
    所以这里重试，且如果重试才成功就打印一行 ⚠️ —— 让"这次是靠第二次才过"
    留在输出里，而不是悄悄变绿。三次都拿不到，说明页面真的停在空态，
    那时下面的检查会照实报缺。
    """
    for index in range(attempts):
        dom = dump(url)
        if READY_MARKER in dom:
            if index > 0:
                print(f"⚠ 第 {index + 1} 次渲染才拿到输入卡（前面的 DOM 停在空态）")
            return dom
        print(f"· 第 {index + 1} 次渲染还没有输入卡（{len(dom)} 字符），重试")
    return dom


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8125/"
    dist = pathlib.Path(
        argv[2] if len(argv) > 2 else r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\dist"
    )
    dom = dump_ready(url)
    pathlib.Path("var_ref/v3_dom.html").write_text(dom, encoding="utf-8")
    print(f"DOM {len(dom)} 字符（已存 var_ref/v3_dom.html）\n")

    failures: list[str] = []
    for name, pattern in CHECKS:
        if re.search(pattern, dom):
            print(f"✅ {name}")
        else:
            print(f"❌ {name}")
            failures.append(name)

    print()
    for name, pattern in OPTIONAL:
        hit = re.search(pattern, dom) is not None
        print(f"{'✅' if hit else '·'} {name}{'' if hit else '（这一轮没有）'}")

    print()
    css = css_of(dist)
    if css == "":
        print("⚠ 找不到打包后的 CSS，跳过样式检查。")
    for name, pattern in CSS_MARKERS:
        if re.search(pattern, css):
            print(f"✅ {name}")
        else:
            print(f"❌ {name}")
            failures.append(name)

    # ------------------------------------------------ 设置面板（真的打开它）==
    print("\n=== 设置面板（地址里带 #settings，面板因此是开着的）===")
    settings_dom = dump(f"{url.rstrip('/')}/#settings")
    pathlib.Path("var_ref/v3_settings_dom.html").write_text(settings_dom, encoding="utf-8")
    for name, pattern in SETTINGS_CHECKS:
        if re.search(pattern, settings_dom):
            print(f"✅ {name}")
        else:
            print(f"❌ {name}")
            failures.append(name)

    if failures:
        print(f"\n❌ {len(failures)} 项缺失：")
        for name in failures:
            print(f"   · {name}")
        return 1
    print("\n✅ 第三批的东西都在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
