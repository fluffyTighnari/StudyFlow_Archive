"""给 `boot()` 加一条无头验收的缝：`?probe=1` 时动态加载 selftest。

为什么要留一条缝：这个界面的正确性最终只有一种验证方式 —— **像人一样点它一遍**。
`--dump-dom` 看不见控制台，也点不动按钮，所以让页面自己在带参数时跑一遍，
把结果写进 DOM。不带参数时这段代码根本不加载（动态 import），
正式使用没有任何多余的东西。
"""

from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "redesign" / "app.js"
text = APP.read_text(encoding="utf-8")

OLD = """    toast(err.message || '连不上后端', 'danger');
  }
}
"""

NEW = """    toast(err.message || '连不上后端', 'danger');
  }

  // 无头验收的缝：带 ?probe=1 时加载探针，跑一轮真实对话并把结果写进 DOM。
  // 不带参数时这段代码不会被加载（动态 import），正式使用没有任何多余的东西。
  if (new URLSearchParams(location.search).get('probe') === '1') {
    import('./src/selftest.js')
      .then((mod) => mod.run())
      .catch((err) => toast(String(err && err.message ? err.message : err), 'danger'));
  }
}
"""

assert text.count(OLD) == 1, f"boot 收尾锚点出现 {text.count(OLD)} 次"
APP.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
print("✅ boot：加入 ?probe=1 的验收缝")
