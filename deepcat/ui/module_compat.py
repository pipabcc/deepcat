"""跨模块机械拆分期间的兼容访问器。

部分测试和扩展会修补原聚合模块中的 Qt 类型或 Worker。方法搬入 Mixin 后，
通过此代理继续从原模块读取最新属性，保持既有修补点和运行时替换能力。
"""

from __future__ import annotations

import importlib
from typing import Any


class DynamicModuleAttribute:
    """把调用和属性访问转发到指定模块的实时属性。"""

    def __init__(self, module_name: str, attribute_name: str) -> None:
        self._module_name = module_name
        self._attribute_name = attribute_name

    def _value(self) -> Any:
        module = importlib.import_module(self._module_name)
        return getattr(module, self._attribute_name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._value()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._value(), name)
