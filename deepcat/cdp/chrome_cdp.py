from __future__ import annotations

import asyncio
import base64
import json
import logging
import urllib.request
import urllib.error
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

from deepcat.core.detector import FixedRegions


@dataclass(frozen=True)
class CDPResult:
    paths: list[str]
    width: int
    height: int


# 单次 CDP 截图 clip 高度上限：超高页面的 base64 响应可能超过 ws max_size 导致连接断开
_MAX_CLIP_HEIGHT = 30000

# 单次等待 CDP 响应的超时秒数：Chrome 卡死时避免 recv 永久阻塞导致上层取消失效
_CDP_RECV_TIMEOUT = 45.0


def _http_get_json(url: str, timeout: float = 2.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "deepcat"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as e:
        raise RuntimeError(f"无法连接到 {url}（{type(e).__name__}: {e}）") from e
    return json.loads(data.decode("utf-8"))


def _pick_target_ws_url(port: int, url_contains: Optional[str]) -> str:
    url = f"http://127.0.0.1:{int(port)}/json"
    try:
        pages = _http_get_json(url, timeout=5.0)
    except Exception as e:
        raise RuntimeError(
            "CDP 连接失败：无法访问 Chrome 调试端口。\n"
            f"请确保 Chrome 以调试模式启动并保持运行：\n"
            f'  chrome --remote-debugging-port={int(port)}\n'
            f"并确认本机可访问：{url}"
        ) from e
    if not isinstance(pages, list):
        raise RuntimeError("CDP 返回数据异常")
    candidates = [p for p in pages if isinstance(p, dict) and p.get("type") == "page" and p.get("webSocketDebuggerUrl")]
    if not candidates:
        raise RuntimeError("未找到可用页面，请确保 Chrome 已开启调试端口且至少打开一个网页标签页")
    if url_contains:
        for p in candidates:
            if url_contains in str(p.get("url") or ""):
                return str(p["webSocketDebuggerUrl"])
    for p in candidates:
        u = str(p.get("url") or "")
        if u and not u.startswith("chrome://"):
            return str(p["webSocketDebuggerUrl"])
    return str(candidates[0]["webSocketDebuggerUrl"])


async def _cdp_call(ws, call_id: int, method: str, params: Optional[dict[str, Any]] = None) -> Any:
    payload: dict[str, Any] = {"id": int(call_id), "method": str(method)}
    if params is not None:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    while True:
        # 带超时等待响应：Chrome 卡死时避免 recv 永久阻塞，超时抛出 RuntimeError 供上层取消流程识别
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=_CDP_RECV_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"等待 CDP 响应超时（{method}，{_CDP_RECV_TIMEOUT:.0f} 秒）：Chrome 可能已卡死或断开连接"
            ) from e
        msg = json.loads(raw)
        if msg.get("id") == int(call_id):
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result")


async def capture_full_page_screenshot(
    *,
    port: int = 9888,
    output_dir: str,
    url_contains: Optional[str] = None,
    max_part_height: int = 14000,
    content_only: bool = True,
    should_stop: Optional[Callable[[], bool]] = None,
) -> CDPResult:
    try:
        import websockets
    except Exception as e:
        raise RuntimeError("缺少依赖 websockets，请先安装：pip install websockets") from e

    ws_url = _pick_target_ws_url(int(port), url_contains)
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # open_timeout/close_timeout 避免握手与关闭阶段无限挂起；max_size 允许超大 base64 截图响应
    async with websockets.connect(
        ws_url, open_timeout=30, close_timeout=10, max_size=128 * 1024 * 1024
    ) as ws:
        await _cdp_call(ws, 1, "Page.enable")
        await _cdp_call(ws, 4, "Runtime.enable")
        try:
            await _cdp_call(
                ws,
                11,
                "Runtime.evaluate",
                {
                    "expression": r"""
(() => {
  const id = '__ls_cdp_style';
  let s = document.getElementById(id);
  if (!s) {
    s = document.createElement('style');
    s.id = id;
    s.textContent = '*{scroll-behavior:auto !important;transition:none !important;animation:none !important;}';
    document.documentElement.appendChild(s);
  }
  try { document.documentElement.style.scrollBehavior = 'auto'; } catch (e) {}
  return true;
})()
""",
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            )
        except Exception:
            pass
        metrics = await _cdp_call(ws, 2, "Page.getLayoutMetrics")
        content = metrics["contentSize"]
        width = int(content["width"])
        height = int(content["height"])

        paths: list[str] = []

        visual = metrics.get("visualViewport") or {}
        viewport_w = int(visual.get("clientWidth") or width or 0)
        viewport_h = int(visual.get("clientHeight") or 0)
        if viewport_h <= 0:
            viewport_h = int(min(1200, height)) if height > 0 else 900

        needs_scroll_fallback = False
        if height <= int(viewport_h * 1.2):
            try:
                probe = await _cdp_call(
                    ws,
                    5,
                    "Runtime.evaluate",
                    {
                        "expression": r"""
(() => {
  const cand = [];
  const all = Array.from(document.querySelectorAll('*'));
  for (const el of all) {
    if (!el || !el.getBoundingClientRect) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 200) continue;
    const sh = el.scrollHeight || 0;
    const ch = el.clientHeight || 0;
    if (sh <= ch + 50) continue;
    const style = window.getComputedStyle(el);
    const oy = style ? style.overflowY : '';
    if (oy !== 'auto' && oy !== 'scroll') continue;
    cand.push({sh, ch, area: r.width * r.height});
  }
  cand.sort((a,b)=> (b.sh-b.ch) - (a.sh-a.ch) || b.area-a.area);
  const best = cand[0];
  return best ? {scrollHeight: best.sh, clientHeight: best.ch} : null;
})()
""",
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
                val = (probe or {}).get("result", {}).get("value")
                if isinstance(val, dict) and int(val.get("scrollHeight") or 0) > int(val.get("clientHeight") or 0) + 200:
                    needs_scroll_fallback = True
            except Exception:
                needs_scroll_fallback = False

        if needs_scroll_fallback or content_only:
            tmp_dir = Path(tempfile.mkdtemp(prefix="deepcat_cdp_frames_"))
            frame_paths: list[Path] = []

            async def capture_clip_png(x: float, y: float, w: float, h: float) -> bytes:
                res = await _cdp_call(
                    ws,
                    2000 + len(frame_paths),
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "captureBeyondViewport": True,
                        "clip": {"x": float(x), "y": float(y), "width": float(w), "height": float(h), "scale": 1},
                    },
                )
                return base64.b64decode(res["data"])

            try:
                init = await _cdp_call(
                    ws,
                    6,
                    "Runtime.evaluate",
                    {
                        "expression": r"""
(() => {
  const sidebarRight = (() => {
    try {
      const vw = window.innerWidth || 0;
      const vh = window.innerHeight || 0;
      let best = 0;
      const all = Array.from(document.querySelectorAll('*'));
      for (const el of all) {
        if (!el || !el.getBoundingClientRect) continue;
        const r = el.getBoundingClientRect();
        if (r.x > 6) continue;
        if (r.width < 140 || r.width > Math.min(520, vw * 0.6)) continue;
        if (r.height < vh * 0.7) continue;
        const st = window.getComputedStyle(el);
        const pos = st ? st.position : '';
        if (pos !== 'fixed' && pos !== 'sticky') continue;
        best = Math.max(best, r.x + r.width);
      }
      return best;
    } catch (e) {
      return 0;
    }
  })();
  const all = Array.from(document.querySelectorAll('*'));
  let best = null;
  for (const el of all) {
    if (!el || !el.getBoundingClientRect) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 200 || r.height < 200) continue;
    const sh = el.scrollHeight || 0;
    const ch = el.clientHeight || 0;
    if (sh <= ch + 50) continue;
    const style = window.getComputedStyle(el);
    const oy = style ? style.overflowY : '';
    if (oy !== 'auto' && oy !== 'scroll') continue;
    const delta = sh - ch;
    const score = delta * 10 + (r.width * r.height) + (r.x * 4000);
    if (!best || score > best.score) best = {score, el};
  }
  if (!best) return null;
  window.__ls_scroll_el = best.el;
  const r = best.el.getBoundingClientRect();
  return {
    rect: {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height},
    sidebarRight,
    scrollHeight: best.el.scrollHeight,
    clientHeight: best.el.clientHeight,
    scrollTop: best.el.scrollTop || 0
  };
})()
""",
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                )
                info = (init or {}).get("result", {}).get("value")
                if not isinstance(info, dict) or "rect" not in info:
                    if not content_only:
                        raise RuntimeError("未找到可滚动内容区域（该网页可能使用特殊渲染/跨域 iframe）")
                    pick = await _cdp_call(
                        ws,
                        7,
                        "Runtime.evaluate",
                        {
                            "expression": r"""
(() => {
  const prefer = Array.from(document.querySelectorAll('main, article, [role="main"]'));
  const cand = [];
  const push = (el) => {
    if (!el || !el.getBoundingClientRect) return;
    const r = el.getBoundingClientRect();
    if (r.width < 300 || r.height < 300) return;
    const st = window.getComputedStyle(el);
    const vis = st && st.visibility !== 'hidden' && st.display !== 'none';
    if (!vis) return;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'nav' || tag === 'aside') return;
    const area = r.width * r.height;
    const score = area + (r.x * 2000);
    cand.push({score, r});
  };
  for (const el of prefer) push(el);
  if (cand.length === 0) {
    for (const el of Array.from(document.body.querySelectorAll('*')).slice(0, 4000)) push(el);
  }
  cand.sort((a,b)=> b.score-a.score);
  const best = cand[0];
  if (!best) return null;
  const r = best.r;
  return {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height};
})()
""",
                            "returnByValue": True,
                            "awaitPromise": True,
                        },
                    )
                    rect = (pick or {}).get("result", {}).get("value")
                    if not isinstance(rect, dict):
                        raise RuntimeError("未找到内容区域（该网页可能使用跨域 iframe）")
                    try:
                        await _cdp_call(
                            ws,
                            8,
                            "Runtime.evaluate",
                            {
                                "expression": r"""
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const h0 = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
  const steps = 18;
  for (let i = 0; i <= steps; i++) {
    const y = Math.round((h0 * i) / steps);
    window.scrollTo(0, y);
    await sleep(120);
  }
  await sleep(200);
  window.scrollTo(0, 0);
  await sleep(200);
  return true;
})()
""",
                                "returnByValue": True,
                                "awaitPromise": True,
                            },
                        )
                    except Exception:
                        pass
                    metrics2 = await _cdp_call(ws, 9, "Page.getLayoutMetrics")
                    content2 = metrics2["contentSize"]
                    width = int(content2["width"])
                    height = int(content2["height"])
                    best_rect = rect
                    for _ in range(14):
                        pick2 = await _cdp_call(
                            ws,
                            12,
                            "Runtime.evaluate",
                            {
                                "expression": r"""
(() => {
  const prefer = Array.from(document.querySelectorAll('main, article, [role="main"]'));
  const cand = [];
  const push = (el) => {
    if (!el || !el.getBoundingClientRect) return;
    const r = el.getBoundingClientRect();
    if (r.width < 300 || r.height < 300) return;
    const st = window.getComputedStyle(el);
    const vis = st && st.visibility !== 'hidden' && st.display !== 'none';
    if (!vis) return;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'nav' || tag === 'aside') return;
    const area = r.width * r.height;
    const score = area + (r.x * 4000);
    cand.push({score, r});
  };
  for (const el of prefer) push(el);
  if (cand.length === 0) {
    for (const el of Array.from(document.body.querySelectorAll('*')).slice(0, 4000)) push(el);
  }
  cand.sort((a,b)=> b.score-a.score);
  const best = cand[0];
  if (!best) return null;
  const r = best.r;
  return {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height};
})()
""",
                                "returnByValue": True,
                                "awaitPromise": True,
                            },
                        )
                        r2 = (pick2 or {}).get("result", {}).get("value")
                        if isinstance(r2, dict) and isinstance(best_rect, dict):
                            if float(r2.get("x") or 0) > float(best_rect.get("x") or 0):
                                best_rect = r2
                        await asyncio.sleep(0.10)
                    rect = best_rect
                    clip_x2 = float(rect["x"])
                    clip_w2 = float(rect["width"])
                    clip_y2 = 0.0
                    clip_h2 = float(height) if height > 0 else float(rect["height"])
                    # 超高页面一次性截图的 base64 响应可能超过 ws max_size 导致断连，这里截断保护
                    if clip_h2 > _MAX_CLIP_HEIGHT:
                        logger.warning(
                            "整页高度 %.0f 超过单次截图上限 %d，仅截取顶部部分",
                            clip_h2,
                            _MAX_CLIP_HEIGHT,
                        )
                        clip_h2 = float(_MAX_CLIP_HEIGHT)
                    png = await capture_clip_png(clip_x2, clip_y2, clip_w2, clip_h2)
                    out_path = str(out_dir / f"screenshot_{ts}_cdp.png")
                    Path(out_path).write_bytes(png)
                    paths.append(out_path)
                    return CDPResult(paths=paths, width=int(clip_w2), height=int(clip_h2))
                if "scrollHeight" not in info:
                    raise RuntimeError("未找到可滚动内容区域（该网页可能使用特殊渲染/跨域 iframe）")

                rect = info["rect"]
                clip_x = float(rect["x"])
                clip_y = float(rect["y"])
                clip_w = float(rect["width"])
                clip_h = float(rect["height"])

                total_scroll_h = int(info.get("scrollHeight") or 0)
                client_h = int(info.get("clientHeight") or 0)
                if total_scroll_h <= client_h + 50:
                    raise RuntimeError("页面滚动高度不足，无法执行滚动拼接")

                try:
                    import cv2
                except Exception as e:
                    raise RuntimeError("CDP 滚动拼接需要 opencv-python") from e

                from deepcat.core.stitcher import find_overlap

                step_base = int(max(200, int(client_h * 0.75)))
                step = int(step_base)
                max_scroll_top = int(max(0, total_scroll_h - client_h))

                async def set_scroll_top(v: int) -> None:
                    await _cdp_call(
                        ws,
                        3000 + v % 1000,
                        "Runtime.evaluate",
                        {
                            "expression": f"window.__ls_scroll_el && (window.__ls_scroll_el.scrollTop = {int(v)});",
                            "awaitPromise": False,
                            "returnByValue": False,
                        },
                    )

                async def get_scroll_info_and_rect() -> Optional[dict]:
                    stat = await _cdp_call(
                        ws,
                        4500 + len(frame_paths),
                        "Runtime.evaluate",
                        {
                            "expression": r"""
(() => {
  const el = window.__ls_scroll_el;
  if (!el) return null;
  const sidebarRight = (() => {
    try {
      const vw = window.innerWidth || 0;
      const vh = window.innerHeight || 0;
      let best = 0;
      const all = Array.from(document.querySelectorAll('*'));
      for (const n of all) {
        if (!n || !n.getBoundingClientRect) continue;
        const rr = n.getBoundingClientRect();
        if (rr.x > 6) continue;
        if (rr.width < 140 || rr.width > Math.min(520, vw * 0.6)) continue;
        if (rr.height < vh * 0.7) continue;
        const st = window.getComputedStyle(n);
        const pos = st ? st.position : '';
        if (pos !== 'fixed' && pos !== 'sticky') continue;
        best = Math.max(best, rr.x + rr.width);
      }
      return best;
    } catch (e) {
      return 0;
    }
  })();
  const r = el.getBoundingClientRect();
  return {
    scrollTop: el.scrollTop || 0,
    scrollHeight: el.scrollHeight || 0,
    clientHeight: el.clientHeight || 0,
    sidebarRight,
    rect: {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height}
  };
})()
""",
                            "returnByValue": True,
                            "awaitPromise": False,
                        },
                    )
                    return (stat or {}).get("result", {}).get("value")

                async def wait_right_layout_and_lock_clip() -> None:
                    nonlocal clip_x, clip_y, clip_w, clip_h
                    try:
                        await _cdp_call(
                            ws,
                            4600,
                            "Runtime.evaluate",
                            {"expression": "window.scrollTo(0,0); true;", "returnByValue": True, "awaitPromise": False},
                        )
                    except Exception:
                        pass

                    best = None
                    stable = 0
                    for _ in range(70):
                        info2 = await get_scroll_info_and_rect()
                        if isinstance(info2, dict) and isinstance(info2.get("rect"), dict):
                            r = info2["rect"]
                            sb = float(info2.get("sidebarRight") or 0)
                            x = float(r.get("x") or 0)
                            ok = (sb <= 0) or (x >= sb + 20)
                            if ok:
                                stable += 1
                            else:
                                stable = 0
                            if best is None or x > float(best["rect"].get("x") or 0):
                                best = info2
                            if stable >= 6:
                                best = info2
                                break
                        await asyncio.sleep(0.10)
                    if isinstance(best, dict) and isinstance(best.get("rect"), dict):
                        rr = best["rect"]
                        clip_x = float(rr.get("x", clip_x))
                        clip_y = float(rr.get("y", clip_y))
                        clip_w = float(rr.get("width", clip_w))
                        clip_h = float(rr.get("height", clip_h))

                await wait_right_layout_and_lock_clip()

                scroll_top = 0
                last_real_top = -1
                stable_bottom = 0
                prev_bgr = None
                expected_new_px = None
                while True:
                    if should_stop is not None and should_stop():
                        break
                    await set_scroll_top(scroll_top)
                    await asyncio.sleep(0.30)
                    png = await capture_clip_png(clip_x, clip_y, clip_w, clip_h)
                    fp = tmp_dir / f"f_{len(frame_paths):05d}.png"
                    fp.write_bytes(png)
                    curr_bgr = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if curr_bgr is None:
                        raise RuntimeError("PNG 解码失败")

                    ssv = await get_scroll_info_and_rect()
                    if isinstance(ssv, dict):
                        real_top = int(ssv.get("scrollTop") or 0)
                        total_scroll_h = int(ssv.get("scrollHeight") or total_scroll_h)
                        client_h = int(ssv.get("clientHeight") or client_h)
                        max_scroll_top = int(max(0, total_scroll_h - client_h))
                        if prev_bgr is not None:
                            o = find_overlap(
                                prev_bgr,
                                curr_bgr,
                                strip_height=120,
                                min_confidence=0.0,
                                fixed_regions=FixedRegions(0, 0),
                                expected_new_pixels=expected_new_px,
                            )
                            new_px = int(curr_bgr.shape[0]) - int(o.overlap_end_y)
                            if int(o.overlap_end_y) <= 0 or new_px < 40:
                                try:
                                    fp.unlink(missing_ok=True)  # type: ignore[attr-defined]
                                except Exception:
                                    pass
                                step = int(max(120, int(step * 0.6)))
                                scroll_top = min(max_scroll_top, real_top + step)
                                await asyncio.sleep(0.35)
                                continue
                            expected_new_px = float(new_px)
                            if step < step_base:
                                step = int(min(step_base, max(step + 20, int(step * 1.1))))
                        else:
                            expected_new_px = float(int(curr_bgr.shape[0] * 0.7))

                        frame_paths.append(fp)
                        prev_bgr = curr_bgr

                        if real_top >= max_scroll_top - 2:
                            stable_bottom += 1
                        else:
                            stable_bottom = 0
                        if real_top == last_real_top:
                            stable_bottom += 1
                        last_real_top = real_top
                        if stable_bottom >= 3:
                            if real_top < max_scroll_top - 2:
                                scroll_top = max_scroll_top
                                stable_bottom = 0
                                continue
                            await asyncio.sleep(0.35)
                            break
                        scroll_top = min(max_scroll_top, real_top + step)
                    else:
                        frame_paths.append(fp)
                        prev_bgr = curr_bgr
                        if scroll_top >= max_scroll_top:
                            break
                        scroll_top = min(max_scroll_top, scroll_top + step)

                frames_bgr = []
                for p in frame_paths:
                    data = p.read_bytes()
                    arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if arr is None:
                        raise RuntimeError("PNG 解码失败")
                    frames_bgr.append(arr)

                from deepcat.core.stitcher import stitch_images

                merged = stitch_images(frames_bgr, strip_height=120, min_confidence=0.0, fixed_regions=FixedRegions(0, 0))
                out_path = str(out_dir / f"screenshot_{ts}_cdp.png")
                # imencode + tofile 支持 Windows 非 ASCII 路径（cv2.imwrite 不支持）
                ok, buf = cv2.imencode(".png", merged, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
                if not ok:
                    raise RuntimeError("PNG 编码失败")
                buf.tofile(out_path)
                paths.append(out_path)

                return CDPResult(paths=paths, width=int(clip_w), height=int(merged.shape[0]))
            finally:
                try:
                    for p in frame_paths:
                        try:
                            p.unlink(missing_ok=True)  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    try:
                        tmp_dir.rmdir()
                    except Exception:
                        pass
                except Exception:
                    pass

        if should_stop is not None and should_stop():
            raise RuntimeError("用户已停止")

        if height <= int(max_part_height):
            res = await _cdp_call(
                ws,
                3,
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                    "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
                },
            )
            data = base64.b64decode(res["data"])
            path = str(out_dir / f"screenshot_{ts}_cdp.png")
            Path(path).write_bytes(data)
            paths.append(path)
        else:
            part = 1
            y = 0
            while y < height:
                h = int(min(int(max_part_height), int(height - y)))
                res = await _cdp_call(
                    ws,
                    10 + part,
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "captureBeyondViewport": True,
                        "clip": {"x": 0, "y": int(y), "width": width, "height": h, "scale": 1},
                    },
                )
                data = base64.b64decode(res["data"])
                path = str(out_dir / f"screenshot_{ts}_cdp_part{part:02d}.png")
                Path(path).write_bytes(data)
                paths.append(path)
                y += h
                part += 1

    return CDPResult(paths=paths, width=width, height=height)


def capture_full_page_screenshot_sync(
    *,
    port: int = 9888,
    output_dir: str,
    url_contains: Optional[str] = None,
    max_part_height: int = 14000,
    content_only: bool = True,
    should_stop: Optional[Callable[[], bool]] = None,
) -> CDPResult:
    return asyncio.run(
        capture_full_page_screenshot(
            port=int(port),
            output_dir=str(output_dir),
            url_contains=url_contains,
            max_part_height=int(max_part_height),
            content_only=bool(content_only),
            should_stop=should_stop,
        )
    )
