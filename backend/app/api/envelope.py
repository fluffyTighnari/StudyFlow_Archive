"""列表响应的**统一信封** —— `{"items": [...]}`。

## 为什么要有这个文件（而不是每个端点各写各的）

系统里 18 个返回列表的端点原先有**两种形状**：16 个是裸数组 `[...]`，
1 个（`/api/sessions/{id}/attachments`）是 `{"items": [...]}`，
还有 `/api/sidebar/*` 用了第三种（对象里带几个列表字段）。

形状不一致不是"风格问题"，它有三个具体代价：

1. **调用方要为每个端点记住一种形状。** 前端写第 17 个列表页时又要看一眼文档，
   而看错的表现是 `data.map is not a function` —— 一个发生在运行时、
   离出错点很远的错误。
2. **裸数组**没有地方放"关于这个列表"的信息：总数、下一页、本次用了什么过滤。
   要加任何一件，都是一次破坏性变更。`attachments` 当初用信封正是为此。
3. **`e2echeck` 会把它记成失败**（`step_response_shape_consistency`）——
   那条检查存在的意义就是让这件事不能悄悄积压。

## 为什么是 `{"items": [...]}` 而不是 `{"data": [...]}`

`items` 与 `attachments` 端点已有的字段名一致 —— **不动已经对的那个**。
`data` 这个名字太空，什么都能往里装，于是读代码的人不知道里面是什么。

## 为什么不是 `{"items": [...], "total": N}`

因为现在没有分页需求，而 `total` 会在 18 个端点上各算一次 COUNT 查询 ——
**为想象的需求付真实的代价**。信封的价值是"以后能加"，不是"现在就都有"。
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ItemsResponse(BaseModel, Generic[T]):
    """所有列表端点的统一形状：`{"items": [...]}`。

    ★ 泛型参数是**逐端点**的（`ItemsResponse[M2PreferenceRead]`），
    所以 OpenAPI 里每个端点仍然有自己精确的条目 schema ——
    统一形状不该拿类型精度去换。
    """

    model_config = ConfigDict(
        # ★ 这个标记是**给检查用的**，不是给前端用的。
        #
        # 为什么需要一个标记：`tests/api/test_api_boundaries.py` 与
        # `tools/e2echeck.py` 要回答「哪些端点是列表端点」。
        #
        # 判据不能是"响应里有个数组字段" —— `GET /api/benchmark/plan`
        # 返回一个**计划对象**，它里面也有一个 `items`（计划里的题目），
        # 但它不是列表端点。第一版判据就是这么写的，于是新加的 `/plan`
        # 被它判成了"忘了用信封"。**这与上一轮那个自打脸的判据是同一类错**：
        # 检查用了一个"看起来能覆盖所有情况"的特征，而那个特征
        # 恰好也对别的东西成立。
        #
        # 判据也不能是类名以 `ItemsResponse_` 开头 —— 那会误伤
        # `AttachmentListRead` 这种带兄弟字段的子类，而"能带兄弟字段"
        # 正是引入信封的理由。
        #
        # 所以标记写在模型上：**继承 `ItemsResponse` 的模型自动带上它**，
        # 而"谁是列表端点"这件事因此只有一个定义处。
        json_schema_extra={"x-mouxue-envelope": "items"}
    )

    items: list[T]


def ok(rows: list[T]) -> dict[str, list[T]]:
    """把一串已校验的记录包成信封。

    直接返回 dict 而不是 `ItemsResponse(items=rows)`：FastAPI 会按
    `response_model` 再校验一次，多构造一次对象只是白跑一趟。
    """
    return {"items": rows}


__all__ = ["ItemsResponse", "ok"]
