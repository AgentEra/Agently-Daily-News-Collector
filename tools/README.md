# Tools Layer

`tools/` 是项目的可替换工具适配层。

默认实现：

- `tools/builtin.py`
- 直接封装 Agently v4 内置 `Search` / `Browse`

当前入口：

- `tools/__init__.py`

如果你想替换为自己的搜索或网页抓取实现，只需要：

1. 新建一个模块，例如 `tools/custom.py`
2. 实现 `SearchToolProtocol` 和 `BrowseToolProtocol` 对应的方法
3. 在 `tools/__init__.py` 中把 `create_search_tool` / `create_browse_tool` 改为导出你的工厂函数

最小接口约束：

```python
class SearchToolProtocol(Protocol):
    async def search_news(
        self,
        *,
        query: str,
        timelimit: SearchNewsTimeLimit,
        max_results: int,
    ) -> list[dict[str, Any]]:
        ...


class BrowseToolProtocol(Protocol):
    async def browse(self, url: str) -> str:
        ...
```
