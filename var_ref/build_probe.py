"""重建探针页（`dist` 每次 build 都会被清空），然后跑一遍几何核对。

探针页只是把主界面用到的那些类名摆出来，让浏览器把 `var()` 全部解析成
具体数值 —— 我看不了截图（当前模型不收图像），所以"看起来对不对"
必须换成"算出来是多少"。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(r"D:\Workspace\Code\phy\personal_learning_flow")
DIST = ROOT / "frontend/app/dist"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
UDD = str(ROOT / "var_ref/edge-profile")
PROBE_OUT = ROOT / "var_ref/probe.html"

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>probe</title>
    <link rel="stylesheet" href="/assets/{css}" />
  </head>
  <body>
    <div class="dsh-frame" id="frame">
      <div class="dsh-sidebarColumn" id="sidecol">
        <div class="dsh-sidebar" id="sidebar">
          <div class="dsh-logoRow"><button class="dsh-iconButton"></button></div>
          <button class="dsh-newSession" id="newSession">新建会话</button>
          <button class="dsh-panelRow" aria-current="true" id="panelRow">工作区</button>
        </div>
      </div>
      <div class="dsh-centerColumn">
        <header class="dsh-header" id="header">
          <div class="dsh-tabs"><button class="dsh-tab" data-active="true" id="tab">对话</button></div>
        </header>
        <div class="dsh-messageList">
          <div class="dsh-userRow"><div class="dsh-bubble" id="bubble">你好</div></div>
          <div class="dsh-assistantRow"><div class="dsh-assistantBody" id="body">回答</div></div>
        </div>
        <div class="dsh-composerRoot">
          <div class="dsh-composerCard" id="card">
            <textarea class="dsh-composerInput" id="input"></textarea>
            <div class="dsh-composerRow">
              <button class="dsh-addButton" id="add"></button>
              <button class="dsh-toolButton" id="tool"></button>
              <div class="dsh-composerTrailing">
                <button class="dsh-sendButton" id="send"></button>
              </div>
            </div>
          </div>
          <div class="dsh-composerStats" id="stats">
            <select class="dsh-modelSelect" id="model"><option>qwen3.6:27b</option></select>
            <span class="dsh-stat">上下文 <span class="dsh-statValue">1.2k / 16.0k</span></span>
          </div>
        </div>
      </div>
    </div>
    <pre id="out"></pre>
    <script>
      const read = (id, props) => {
        const el = document.getElementById(id);
        if (el === null) return { id, missing: true };
        const cs = getComputedStyle(el);
        const out = { id };
        for (const p of props) out[p] = cs.getPropertyValue(p);
        return out;
      };
      const root = getComputedStyle(document.documentElement);
      const report = {
        tokens: {
          labelPrimary: root.getPropertyValue("--dsw-alias-label-primary").trim(),
          bgBase: root.getPropertyValue("--dsw-alias-bg-base").trim(),
          sidebarFill: root.getPropertyValue("--dsw-specific-sidebar-fill").trim(),
          bubble: root.getPropertyValue("--dsw-specific-bubble").trim(),
          inputMajor: root.getPropertyValue("--dsw-specific-input-major").trim(),
          selector: root.getPropertyValue("--dsw-specific-selector").trim(),
          infoFill: root.getPropertyValue("--dsw-alias-button-info-fill").trim(),
          deepseek500: root.getPropertyValue("--dsw-static-deepseek-500").trim(),
          bubbleHighlight: root.getPropertyValue("--dsw-specific-bubble-highlight").trim(),
          sidebarWidth: root.getPropertyValue("--dsh-sidebar-width").trim(),
          elevationSoft: root.getPropertyValue("--dsw-elevation-soft").trim(),
          fontFamily: root.getPropertyValue("--dsw-font-family").trim().slice(0, 40),
        },
        elements: [
          read("frame", ["grid-template-columns"]),
          read("sidebar", ["padding", "background-color", "width"]),
          read("newSession", ["height", "border-radius", "padding", "background-color"]),
          read("panelRow", ["min-height", "border-radius", "padding", "background-color"]),
          read("header", ["min-height", "padding-top", "padding-left", "padding-right", "border-bottom-width"]),
          read("tab", ["font-size", "font-weight", "color"]),
          read("bubble", ["border-radius", "padding", "background-color", "font-size"]),
          read("body", ["font-size", "line-height", "color"]),
          read("card", ["border-radius", "box-shadow", "background-color"]),
          read("input", ["min-height", "padding", "font-size"]),
          read("add", ["width", "height", "border-radius", "background-color"]),
          read("send", ["width", "height", "border-radius", "background-color", "color"]),
          read("model", ["height", "font-size", "font-weight", "border-radius", "padding-left"]),
          read("stats", ["font-size", "color", "justify-content"]),
        ],
        bodyBg: getComputedStyle(document.body).backgroundColor,
        bodyColor: getComputedStyle(document.body).color,
      };
      // 深色：DSH 往 body 上挂 data-ds-dark-theme。挂上再读一遍同样的令牌，
      // 用来证「深色那一层别名真的能换掉颜色」，而不是文件里写了一段没人用。
      document.body.setAttribute("data-ds-dark-theme", "");
      const darkRoot = getComputedStyle(document.body);
      report.darkTokens = {
        labelPrimary: darkRoot.getPropertyValue("--dsw-alias-label-primary").trim(),
        bgBase: darkRoot.getPropertyValue("--dsw-alias-bg-base").trim(),
        bubble: darkRoot.getPropertyValue("--dsw-specific-bubble").trim(),
        sidebarFill: darkRoot.getPropertyValue("--dsw-specific-sidebar-fill").trim(),
        inputMajor: darkRoot.getPropertyValue("--dsw-specific-input-major").trim(),
      };
      report.darkBodyBg = getComputedStyle(document.body).backgroundColor;
      document.body.removeAttribute("data-ds-dark-theme");
      document.getElementById("out").textContent = JSON.stringify(report, null, 1);
    </script>
  </body>
</html>
"""


def main() -> int:
    css = sorted(p.name for p in (DIST / "assets").glob("*.css"))
    if len(css) != 1:
        print(f"dist/assets 里应当只有一份 CSS，实际 {css}")
        return 2
    (DIST / "__probe.html").write_text(
        TEMPLATE.replace("{css}", css[0]), encoding="utf-8"
    )
    print(f"探针页写好（CSS = {css[0]}）")

    result = subprocess.run(
        [
            EDGE,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            f"--user-data-dir={UDD}",
            "--virtual-time-budget=4000",
            "--dump-dom",
            "http://127.0.0.1:8131/__probe.html",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    PROBE_OUT.write_text(result.stdout, encoding="utf-8")
    print(f"dump 写出 {PROBE_OUT.name}（{len(result.stdout)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
