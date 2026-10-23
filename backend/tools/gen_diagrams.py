"""生成 `V-SMP` 的四版呈现里的**图示版**（3 张）与 `V-OSP` 的**结构图版**（5 张）。

一条硬规则贯穿：**图必须信息完整**。
四版呈现比的是"同一内容用哪种媒介更舒服"，所以图不能只画一半 ——
图画得比文字少，测出来的就是"嫌信息少"而不是媒介偏好。
同理，`V-OSP` 的 A 版（结构图）与 B 版（线性文字）要信息量相近，
否则测出来的是信息量偏好。

尺寸规格见 `_测题图片清单_v0.1.md`：1200×900，PNG，黑白也能读（不靠颜色传信息）。
"""

from __future__ import annotations

from pathlib import Path

from tools.diagram_kit import GRAY, H, INK, LIGHT, W, Sheet, font

ROOT = Path(__file__).resolve().parent.parent.parent
BASE = ROOT / "frontend" / "redesign" / "assets" / "items"


# ── SMP-01 潮汐 ───────────────────────────────────────────────────────
def smp_tide() -> Path:
    sheet = Sheet("为什么一天有两次高潮", "月球引力 + 地球自转，把一个地点一天送过两个隆起")
    # 左：地球与两个隆起
    cx, cy, r = 300, 430, 130
    sheet.draw.ellipse([cx - r - 46, cy - r + 20, cx + r + 46, cy + r - 20], outline=INK, width=2)
    sheet.draw.ellipse([cx - r + 46, cy - r - 20, cx + r - 46, cy + r + 20], outline=INK, width=2)
    sheet.draw.ellipse([cx - r + 100, cy - 20, cx + r - 100, cy + 20], outline=LIGHT, width=2)
    sheet.arrow(cx + r + 60, cy - 150, cx + r + 60, cy + 150)
    sheet.label(cx + r + 76, cy - 6, "自转", 18)
    sheet.draw.ellipse([cx + 300, cy - 34, cx + 368, cy + 34], outline=INK, width=2)
    sheet.label(cx + 382, cy - 10, "月球", 18)
    sheet.label(cx - 120, cy - 128, "背月侧隆起", 18)
    sheet.label(cx + 120, cy - 128, "朝月侧隆起", 18)
    sheet.label(cx - 130, cy + 168, "地球", 18)

    # 右上：24 小时时间轴
    sheet.label(620, 170, "同一个海岸的一天（地球自转一周 = 24h）", 20, bold=True)
    axis_y = 300
    sheet.arrow(620, axis_y, 1140, axis_y, head=10)
    for hour, kind in [(620, "0h 高潮"), (750, "6h 低潮"), (880, "12h 高潮"),
                       (1010, "18h 低潮"), (1120, "24h 高潮")]:
        sheet.draw.line([(hour, axis_y - 10), (hour, axis_y + 10)], fill=INK, width=2)
        sheet.label(hour, axis_y + 26, kind, 16, anchor="ma")
    sheet.label(700, axis_y + 76, "两次高潮之间约 12 小时 25 分（因为月球也在动）", 17, fill=GRAY)

    # 右下：大潮小潮
    sheet.label(620, 470, "太阳的叠加：大潮与小潮", 20, bold=True)
    sheet.table(620, 510, [150, 200, 210], [
        ["月相", "日月位置", "潮差"],
        ["朔 / 望", "同一条线上（叠加）", "大潮（最大）"],
        ["上弦 / 下弦", "互相垂直（抵消）", "小潮（最小）"],
    ])
    sheet.note("限定：不是所有海岸都「两次一样高、间隔 12 小时」——"
               "全日潮（一天一次）与混合潮（两次不等高）都真实存在，取决于当地海域形状与纬度。")
    path = BASE / "SMP-01" / "SMP-01-G.png"
    sheet.save(path)
    return path


# ── SMP-02 分裂作格 ───────────────────────────────────────────────────
def smp_ergative() -> Path:
    sheet = Sheet("分裂作格：同一语言里为什么会有两套对齐",
                  "S 不及物唯一论元 · A 及物施事 · O 及物受事")
    sheet.table(60, 170, [230, 180, 180, 400], [
        ["", "S（不及物）", "A（施事）", "O（受事）"],
        ["主格-宾格", "主格", "主格", "宾格"],
        ["作格-绝对格", "绝对格", "作格", "绝对格"],
    ], size=19, row_h=52)
    sheet.label(60, 350, "印地语：同一件事，换个体就换一套对齐", 20, bold=True)
    sheet.box(60, 388, 520, 66, "完成体　Ramesh-ne　kitaab　kharidi　（施事 -ne，受事无标记）",
              size=17, wrap_at=40)
    sheet.box(60, 462, 520, 66, "未完成体　Ramesh　kitaab-ko　kharidta hai　（施事主格，受事 -ko）",
              size=17, wrap_at=40)
    sheet.label(60, 540, "→ 决定用哪一套的是「体（aspect）」这个语法范畴", 18, fill=GRAY)

    sheet.label(620, 170, "三类常见的分裂条件", 20, bold=True)
    for index, (tag, text) in enumerate([
        ("① 体 / 时", "完成体用作格，未完成体用主格-宾格（印地语）"),
        ("② 人称-生命度", "一、二人称偏主格-宾格；第三人称/无生倾向作格（只是倾向）"),
        ("③ 小句类型", "主句与从句可以各用一套（澳、高加索多语种有报道）"),
    ]):
        sheet.box(620, 212 + index * 92, 520, 78, f"{tag}：{text}", size=17, wrap_at=34)

    sheet.label(620, 500, "形态作格 ≠ 句法作格", 20, bold=True)
    sheet.box(620, 540, 520, 96,
              "迪尔巴尔语：格后缀层面一目了然，但并列小句删略成分时按"
              "「绝对格（S=O）」认同 —— 删掉的是受事，不是施事。",
              size=17, wrap_at=36)
    sheet.box(620, 652, 520, 74,
              "所以「某语言是作格语言」这句话必须补一句：在哪个层面上作格。",
              size=17, wrap_at=36, fill=None)
    sheet.note("判据先于分类：不用格后缀的语言（如汉语），「是不是作格」取决于你用什么判据；"
               "不同判据给出不同答案，不是研究者的分歧。")
    path = BASE / "SMP-02" / "SMP-02-G.png"
    sheet.save(path)
    return path


# ── SMP-03 哈希表 ─────────────────────────────────────────────────────
def smp_hash() -> Path:
    sheet = Sheet("哈希表为什么平均 O(1)", "键 → 哈希函数 → 桶下标 → 槽位；冲突用链地址法")
    # 上：键 → 哈希 → 桶
    for index, key in enumerate(["\"apple\"", "\"banana\"", "\"cherry\""]):
        sheet.box(80, 180 + index * 70, 150, 52, key, size=19)
        sheet.arrow(238, 206 + index * 70, 330, 206 + index * 70)
    sheet.box(340, 250, 190, 110, "hash(k)\nmod m", size=19, bold=True)

    # 桶数组
    sheet.label(600, 168, "桶数组（m = 8）", 19, bold=True)
    for slot in range(8):
        y = 200 + slot * 62
        sheet.draw.rectangle([600, y, 700, y + 50], outline=INK, width=2)
        sheet.label(650, y + 25, f"[{slot}]", 17, anchor="mm")
        if slot in (2, 5):
            chain_x = 700
            for depth, word in enumerate(["apple", "banana"] if slot == 2 else ["cherry"]):
                chain_x = 700 + depth * 150
                sheet.arrow(chain_x, y + 25, chain_x + 26, y + 25, head=8)
                sheet.box(chain_x + 30, y + 2, 130, 46, word, size=16, wrap_at=10)
    sheet.arrow(534, 305, 594, 305)
    sheet.label(560, 276, "下标", 16, anchor="ma")

    # 下：公式与最坏情形
    sheet.label(60, 560, "平均情况", 20, bold=True)
    sheet.box(60, 596, 520, 92,
              "负载因子 α = n / m　　平均链长 ≈ α\n"
              "查找 ≈ 1 + α ⇒ 均匀哈希、α 有上界时是 O(1)",
              size=18, wrap_at=40)
    sheet.label(620, 560, "最坏情况", 20, bold=True)
    sheet.box(620, 596, 520, 92,
              "所有键落进同一个桶 ⇒ 链表退化成线性表\n查找 O(n)（哈希函数差，或被刻意构造）",
              size=18, wrap_at=40)
    sheet.label(60, 716, "扩容（rehash）：负载因子超阈值就翻倍重建 —— 单次 O(n)，摊还 O(1)",
               18, fill=GRAY)
    sheet.note("所以要同时说清两件事：平均 O(1) 依赖「哈希均匀 + 负载因子有上界」；"
               "去掉这个前提，最坏就是 O(n)。")
    path = BASE / "SMP-03" / "SMP-03-G.png"
    sheet.save(path)
    return path


# ── OSP 结构图（5 张）─────────────────────────────────────────────────
def osp_split_ergative() -> Path:
    sheet = Sheet("分裂作格：全局结构", "对齐 → 分裂条件 → 语言例；形态与句法两个层面分开看")
    sheet.box(60, 180, 300, 96, "格对齐系统", size=22, bold=True)
    sheet.box(60, 300, 300, 80, "主格-宾格：S = A", size=19)
    sheet.box(60, 400, 300, 80, "作格-绝对格：S = O", size=19)
    sheet.arrow(210, 276, 210, 300)
    sheet.arrow(210, 380, 210, 400)

    sheet.box(430, 180, 300, 76, "分裂条件", size=22, bold=True)
    for index, text in enumerate(["① 体 / 时", "② 人称-生命度（倾向）", "③ 小句类型"]):
        sheet.arrow(360, 352, 430, 220 + index * 84, dashed=True)
        sheet.box(430, 276 + index * 84, 300, 62, text, size=18, wrap_at=18)

    sheet.box(800, 180, 340, 76, "层面", size=22, bold=True)
    sheet.box(800, 276, 340, 84, "形态层：格后缀长在谁身上", size=18, wrap_at=16)
    sheet.box(800, 380, 340, 84, "句法层：删略/关系化按谁认同", size=18, wrap_at=16)
    sheet.label(800, 480, "两者不必一致（Dyirbal）", 18, fill=GRAY)

    sheet.box(430, 560, 710, 100,
              "语言例：印地语（体分裂）· 巴斯克（无分裂作格）· 迪尔巴尔（句法作格）",
              size=19, wrap_at=44)
    sheet.note("这张图与线性版（B 版）信息量相同：两套对齐、三类条件、两个层面、三个语言例。")
    path = BASE / "OSP-01" / "OSP-01-G.png"
    sheet.save(path)
    return path


def osp_tonogenesis() -> Path:
    sheet = Sheet("声调是怎么产生的（tonogenesis）", "音段对立的消失，把音高差异提升为区别性特征")
    steps = [
        ("起点", "辅音清浊对立 / 韵尾对立存在"),
        ("音段丢失", "浊塞音清化、韵尾弱化或消失"),
        ("音高被留下", "原来的清高浊低差异还在，但载体没了"),
        ("听觉显著化", "音高成为唯一可用的对立线索"),
        ("调类化", "音高差异被重新分析为声调（调类）"),
        ("语法化（部分语言）", "声调进一步承担时态、人称等语法功能"),
    ]
    for index, (tag, text) in enumerate(steps):
        y = 180 + index * 104
        sheet.box(80, y, 200, 72, tag, size=19, bold=True)
        sheet.box(320, y, 620, 72, text, size=19, wrap_at=30)
        if index:
            sheet.arrow(240, y - 32, 240, y)
    sheet.arrow(240, 152, 240, 180)
    sheet.label(980, 200, "分支条件", 20, bold=True)
    for index, text in enumerate(["清浊消失", "韵尾简化", "发声态差异"]):
        sheet.box(980, 240 + index * 92, 160, 74, text, size=17, wrap_at=8)
    sheet.note("时间尺度：可以在一两代人的时间内完成，有语言在几十年内新增调类；"
               "而「有声调/没声调」也不是二值，存在只有两个调、仅在少数词上对立的语言。")
    path = BASE / "OSP-02" / "OSP-02-G.png"
    sheet.save(path)
    return path


def osp_case_tree() -> Path:
    sheet = Sheet("格标记系统的分类", "先按「谁跟谁同形」分对齐，再按「是否随条件切换」分分裂")
    sheet.box(430, 160, 340, 70, "格系统", size=22, bold=True)
    branches = [
        ("主格-宾格", "S = A，O 另标"),
        ("作格-绝对格", "S = O，A 另标"),
        ("中立", "三者同形"),
        ("三分", "S、A、O 三者都不同"),
    ]
    for index, (name, text) in enumerate(branches):
        x = 60 + index * 285
        sheet.arrow(600, 230, x + 120, 300)
        sheet.box(x, 300, 240, 70, name, size=19, bold=True)
        sheet.box(x, 380, 240, 76, text, size=17, wrap_at=12)
        sheet.arrow(x + 120, 456, x + 120, 500, dashed=True)
        sheet.box(x, 500, 240, 92, ["不分裂", "", "", ""][index] or "可分裂 / 不分裂", size=17)
    sheet.box(60, 630, 1080, 96,
              "分裂维度（叠加在上面任何一支上）：体 / 时 · 人称-生命度 · 小句类型 · 主动-非主动",
              size=19, wrap_at=48)
    sheet.note("这张图对应线性版的四段：对齐四种、分裂维度四种、语言例、以及「形态与句法可不同」的提醒。")
    path = BASE / "OSP-03" / "OSP-03-G.png"
    sheet.save(path)
    return path


def osp_word_order_matrix() -> Path:
    sheet = Sheet("语序类型 × 形态类型", "两套分类各自独立；矩阵里放的是「典型例子」而不是定义")
    sheet.table(60, 180, [200, 220, 220, 220, 220], [
        ["", "孤立（分析）", "黏着", "屈折", "多式综合"],
        ["SOV", "汉语（SV O 为主）", "日语、土耳其语", "拉丁语、德语", "因纽特语"],
        ["SVO", "英语、法语", "斯瓦希里语", "俄语（较自由）", "（少见组合）"],
        ["VSO", "（多为分析型）", "他加禄语", "威尔士语", "（少见）"],
        ["VOS / OVS", "（少见）", "斐济语（VOS）", "（少数）", "（罕见）"],
    ], size=17, row_h=52)
    sheet.label(60, 470, "为什么列这两套分类", 20, bold=True)
    sheet.box(60, 508, 1080, 88,
              "语序说的是「成分排在哪」，形态说的是「词怎么变」—— 两者可以任意组合，"
              "所以「SOV 语言都黏着」是错的。", size=18, wrap_at=48)
    sheet.label(60, 620, "三条容易记反的", 20, bold=True)
    for index, text in enumerate([
        "① 汉语是孤立语，但复合词很多；「无形态」不等于「无结构」",
        "② 屈折语言也可以语序固定（英语 SVO），黏着语言也可以语序自由",
        "③ 语言类型是「倾向」，不是天花板 —— 每种语言都有反例",
    ]):
        sheet.box(60, 658 + index * 46, 1080, 42, text, size=17, wrap_at=56)
    sheet.note("线性版会给同样的三张表与三条提醒，只是排成一段一段的文字。")
    path = BASE / "OSP-04" / "OSP-04-G.png"
    sheet.save(path)
    return path


def osp_contact() -> Path:
    sheet = Sheet("语言接触的四种结果", "两个变量：接触强度（横轴）× 人口比例（纵轴）")
    ox, oy = 300, 700
    sheet.arrow(ox, oy, ox + 700, oy, head=10)
    sheet.arrow(ox, oy, ox, oy - 470, head=10)
    sheet.label(ox + 700, oy + 20, "接触强度 →", 18, anchor="ra")
    sheet.label(ox - 12, oy - 470, "人口比例 ↑", 18, anchor="la")
    sheet.draw.line([(ox + 350, oy), (ox + 350, oy - 470)], fill=LIGHT, width=2)
    sheet.draw.line([(ox, oy - 235), (ox + 700, oy - 235)], fill=LIGHT, width=2)
    quadrants = [
        (ox + 20, oy - 455, "替换", "少数人进入多数语社会：\n两三代内转向多数语"),
        (ox + 372, oy - 455, "混合", "人口相当且接触强：\n出现混合语 / 克里奥尔"),
        (ox + 20, oy - 220, "借用", "接触弱、人口占多数：\n只借词与个别结构"),
        (ox + 372, oy - 220, "语言转移", "接触强、人口占多数：\n外来结构渗入本族语"),
    ]
    for x, y, name, text in quadrants:
        sheet.box(x, y, 320, 196, name, size=21, bold=True, wrap_at=8)
        lines = text.split("\n")
        for index, line in enumerate(lines):
            sheet.label(x + 14, y + 116 + index * 30, line, 16, fill=GRAY)
    sheet.note("限定：四个格子是「倾向」，不是判据 —— 同一种接触在不同社区可以落进不同格子，"
               "取决于教育、通婚与政策。")
    path = BASE / "OSP-05" / "OSP-05-G.png"
    sheet.save(path)
    return path


def main() -> None:
    print("V-SMP 图示版：")
    smp_tide()
    smp_ergative()
    smp_hash()
    print("V-OSP 结构图版：")
    osp_split_ergative()
    osp_tonogenesis()
    osp_case_tree()
    osp_word_order_matrix()
    osp_contact()


if __name__ == "__main__":
    main()
