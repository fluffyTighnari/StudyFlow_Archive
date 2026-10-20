"""把发送键左边那一组控件写进去（上下文环 / 模型 / 用量），并删掉卡片下面那行旧统计。

## 布局

```
[ + ] [ 📎 ] [ 针对第 3 步 ✕ ]        ( ○ 6% ) [ 模型 ▾ ] [ ▥ 1.2M tok ] [ ↑ ]
```

## 上下文为什么是"环"

用户点名要圆圈。它比一条进度线更适合这个位置：**直径不随数字变化**，
所以左下角那排控件不会因为"上下文从 3% 涨到 41%"而左右跳动。
环里不写字（那个尺寸写不下、写了也读不出来），数量在悬停提示与点击展开里。

## 模型为什么从 `<select>` 换成自绘菜单

`<select>` 在这里有两个问题：① 它的样子由系统决定，改不动（和这一页的
圆角/字号不搭）；② 它**没法显示"哪一个是当前的"**——只能靠 `value` 匹配，
而一旦配置里的那一份不在档案列表里（很常见：配置文件直接给 provider+model），
下拉框的第一项就变成"当前"，用户会以为自己在选一个不存在的档案。
自绘菜单把"当前生效的那一份"当成一行**事实**显示出来。
"""

from __future__ import annotations

import pathlib

PATH = pathlib.Path(
    r"D:\Workspace\Code\phy\personal_learning_flow\frontend\app\src\components\Composer.tsx"
)
text = PATH.read_text(encoding="utf-8")


def swap(old: str, new: str, *, label: str, count: int = 1) -> None:
    global text
    found = text.count(old)
    assert found == count, f"{label}：锚点出现 {found} 次（期望 {count}）"
    text = text.replace(old, new, count)
    print(f"✅ {label}")


TRAILING_OLD = """          <div className="dsh-composerTrailing">
            {busy ? (
              <button
                type="button"
                className="dsh-sendButton dsh-focusable"
                data-busy="true"
                onClick={onStop}
                title="停止生成"
                aria-label="停止生成"
              >
                <StopIcon />
              </button>
            ) : (
              <button
                type="button"
                className="dsh-sendButton dsh-focusable"
                onClick={submit}
                disabled={!canSend}
                title="发送（Enter）"
                aria-label="发送"
              >
                <ArrowUpIcon />
              </button>
            )}
          </div>"""

TRAILING_NEW = """          <div className="dsh-composerTrailing">
            {/*
              上下文环。点击展开同一份读数的详情（与悬停提示同样的内容，
              但键盘用户按不到悬停，所以他们需要这个可点的入口）。
            */}
            <button
              type="button"
              className="dsh-ringButton dsh-focusable"
              onClick={() => {
                setUsageOpen(false);
                setModelOpen(false);
                setRingOpen((open) => !open);
              }}
              aria-label="上下文用量"
              aria-expanded={ringOpen}
              title={
                contextTokens === null
                  ? "上下文：还没测到（这个会话还没有一次带用量的回复）"
                  : `上下文：已用 ${fullNumber(contextTokens)} tok` +
                    (limit === null ? "" : ` / 上限 ${fullNumber(limit)} tok`) +
                    (usedRatio === null
                      ? ""
                      : `（${Math.round(usedRatio * 100)}%）`) +
                    "\\n最近一次回复实测发送给模型的 tokens"
              }
            >
              <svg className="dsh-ring" viewBox="0 0 32 32" aria-hidden="true">
                <circle className="dsh-ringTrack" cx="16" cy="16" r="13" />
                <circle
                  className="dsh-ringFill"
                  cx="16"
                  cy="16"
                  r="13"
                  data-crowded={usedRatio !== null && usedRatio > 0.8 ? undefined : false}
                  /*
                   * 周长 = 2πr。这里写的是"比例"，不是像素 —— 值域 0..1，
                   * 由浏览器乘上半径。写死路径长度会让环在缩放时错位。
                   */
                  strokeDasharray={`${Math.round((usedRatio ?? 0) * 100)} 100`}
                  pathLength={100}
                />
              </svg>
              <span className="dsh-ringText">
                {usedRatio === null ? "—" : `${Math.round(usedRatio * 100)}%`}
              </span>
            </button>

            {ringOpen && (
              <div className="dsh-popover dsh-popoverRight" role="dialog" aria-label="上下文用量">
                <p className="dsh-popoverTitle">上下文</p>
                <dl className="dsh-kv">
                  <dt>最近一次回复</dt>
                  <dd>{contextTokens === null ? "没测到" : `${fullNumber(contextTokens)} tok`}</dd>
                  <dt>窗口上限</dt>
                  <dd>{limit === null ? "没测到" : `${fullNumber(limit)} tok`}</dd>
                  <dt>占用</dt>
                  <dd>{usedRatio === null ? "算不出" : `${(usedRatio * 100).toFixed(1)}%`}</dd>
                </dl>
                <p className="dsh-popoverNote">
                  取的是**最近一次回复实测**的 prompt tokens（后端落库的
                  `prompt_tokens`），不是估算；没有读数时显示「没测到」。
                </p>
              </div>
            )}

            {/*
              模型：一个键 + 自绘菜单。菜单里第一组是"当前生效"（事实），
              第二组是可切换的档案（动作）。
            */}
            <div className="dsh-popoverAnchor">
              <button
                type="button"
                className="dsh-toolPill dsh-focusable"
                onClick={() => {
                  setRingOpen(false);
                  setUsageOpen(false);
                  setModelOpen((open) => !open);
                }}
                aria-label="模型"
                aria-expanded={modelOpen}
                title={`${modelTitle}\\n点击可换一份模型档案`}
                disabled={hasChoice === false}
              >
                <span className="dsh-toolPillText">{modelLabel}</span>
                <ChevronRightIcon />
              </button>

              {modelOpen && (
                <div className="dsh-popover dsh-popoverRight" role="menu" aria-label="选择模型">
                  <p className="dsh-popoverTitle">当前生效</p>
                  <p className="dsh-popoverFact" title={modelTitle}>
                    {modelLabel}
                  </p>
                  <div className="dsh-popoverSep" />
                  <p className="dsh-popoverTitle">
                    {(profiles ?? []).length === 0 ? "没有别的档案" : "换成"}
                  </p>
                  {(profiles ?? []).length === 0 && (
                    <p className="dsh-popoverNote">
                      只有配置文件里这一份。要有多份可选，得在设置里加模型档案
                      （`/api/llm/profiles` 还没有写入端点）。
                    </p>
                  )}
                  {(profiles ?? []).map((profile) => (
                    <button
                      key={profile.profile_id}
                      type="button"
                      role="menuitem"
                      className="dsh-menuItem"
                      data-checked={profile.profile_id === activeProfileId || undefined}
                      disabled={switchingProfile || profile.profile_id === activeProfileId}
                      onClick={() => {
                        setModelOpen(false);
                        onSwitchProfile(profile.profile_id);
                      }}
                    >
                      <span>
                        {profile.label === ""
                          ? `${profile.provider_id} · ${profile.model}`
                          : profile.label}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/*
              完整的 token 用量。按钮上是总量，点开是四格明细。
              每一格**要么是实测值，要么是「没测到」** —— 没有 0 填充。
            */}
            <div className="dsh-popoverAnchor">
              <button
                type="button"
                className="dsh-toolPill dsh-focusable"
                onClick={() => {
                  setRingOpen(false);
                  setModelOpen(false);
                  setUsageOpen((open) => !open);
                }}
                aria-label="token 用量"
                aria-expanded={usageOpen}
                title="本会话的 token 用量（点开看明细）"
              >
                <TokenIcon />
                <span className="dsh-toolPillText">
                  {tokenUsage === null ? "没测到" : `${compactTokens(tokenUsage.total)} tok`}
                </span>
              </button>

              {usageOpen && (
                <div className="dsh-popover dsh-popoverRight" role="dialog" aria-label="token 用量">
                  <p className="dsh-popoverTitle">本会话 token 用量</p>
                  <dl className="dsh-kv">
                    <dt>总量</dt>
                    <dd>{tokenUsage === null ? "没测到" : `${fullNumber(tokenUsage.total)} tok`}</dd>
                    <dt>缓存命中</dt>
                    <dd>
                      {tokenUsage?.hitRate == null
                        ? "没测到"
                        : `${Math.round(tokenUsage.hitRate * 100)}%`}
                    </dd>
                    <dt>未命中输入</dt>
                    <dd>
                      {tokenUsage === null ? "没测到" : `${fullNumber(tokenUsage.uncachedInput)} tok`}
                    </dd>
                    <dt>命中输入</dt>
                    <dd>
                      {tokenUsage === null || tokenUsage.hitRate === null
                        ? "没测到"
                        : `${fullNumber(tokenUsage.cachedInput)} tok`}
                    </dd>
                    <dt>输出</dt>
                    <dd>{tokenUsage === null ? "没测到" : `${fullNumber(tokenUsage.output)} tok`}</dd>
                  </dl>
                  <p className="dsh-popoverNote">
                    总量 = 输入 + 输出（每条回复实测值相加）。缓存读数是提供方报的
                    （DeepSeek 会报）；本地模型与测试替身不报，所以显示「没测到」
                    而不是 0% —— 0% 的意思是"测过了，一次都没命中"。
                  </p>
                </div>
              )}
            </div>

            {busy ? (
              <button
                type="button"
                className="dsh-sendButton dsh-focusable"
                data-busy="true"
                onClick={onStop}
                title="停止生成"
                aria-label="停止生成"
              >
                <StopIcon />
              </button>
            ) : (
              <button
                type="button"
                className="dsh-sendButton dsh-focusable"
                onClick={submit}
                disabled={!canSend}
                title="发送（Enter）"
                aria-label="发送"
              >
                <ArrowUpIcon />
              </button>
            )}
          </div>"""

swap(TRAILING_OLD, TRAILING_NEW, label="发送键左边那一组控件")

# 旧的卡片下方统计行：信息已全部搬进环与用量面板
STATS_OLD = """      {/*
        右下角那一行。三个值都来自**真实测量**：
        · 模型：`GET /api/llm/config` 里此刻生效的那一份，可以从档案里换
          （`POST /api/llm/profiles/{id}/activate`）；
        · 上下文：最近一次回复的 `prompt_tokens` / `llm_num_ctx`；
        · token：本会话累计生成量（`completion_tokens` 求和）。
        `null` 一律显示「没测到」，不显示 0 —— 0 的意思是"什么都没发"。
      */}
      <div className="dsh-composerStats">
        <span className="dsh-stat" title={modelTitle}>
          <select
            className="dsh-modelSelect"
            value={activeProfileId}
            onChange={(event) => {
              const next = event.target.value;
              if (next !== "" && next !== activeProfileId) onSwitchProfile(next);
            }}
            /*
             * 只有一份可选时不假装能选：禁用 + 说明。摆一个空下拉框
             * 会让人点开才发现里面只有一个选项，那是一次白费的操作。
             */
            disabled={hasChoice === false || switchingProfile}
            aria-label="选择模型"
          >
            <option value="">{modelLabel}</option>
            {(profiles ?? []).map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.label === "" ? `${profile.provider_id} · ${profile.model}` : profile.label}
              </option>
            ))}
          </select>
        </span>

        <span className="dsh-stat" title="最近一次回复实测发送给模型的上下文 tokens">
          <span>上下文</span>
          <span className="dsh-statValue">
            {contextTokens === null ? "没测到" : compactTokens(contextTokens)}
            {limit !== null && ` / ${compactTokens(limit)}`}
          </span>
          {usedRatio !== null && (
            <span className="dsh-contextBar" aria-hidden="true">
              <span
                className="dsh-contextBarFill"
                data-crowded={usedRatio > 0.8}
                style={{ width: `${Math.round(usedRatio * 100)}%` }}
              />
            </span>
          )}
        </span>

        <span className="dsh-stat" title="本会话累计生成的 tokens">
          <span>生成</span>
          <span className="dsh-statValue">
            {completionTokens === null ? "没测到" : compactTokens(completionTokens)}
          </span>
        </span>
      </div>

"""

swap(STATS_OLD, "", label="删掉卡片下方的旧统计行")

# 环的展开状态 + 用到的两个新状态
swap(
    """  const [modelOpen, setModelOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);""",
    """  const [modelOpen, setModelOpen] = useState(false);
  const [usageOpen, setUsageOpen] = useState(false);
  const [ringOpen, setRingOpen] = useState(false);""",
    label="环的展开状态",
)

PATH.write_text(text, encoding="utf-8")
print(f"\n第二阶段写完（{len(text.splitlines())} 行）")
