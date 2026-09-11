from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal
from deepcat.settings_store import (
    infer_translator_model_type,
    normalize_anthropic_messages_url,
    normalize_openai_chat_base_url,
    normalize_openai_responses_url,
)


class FetchModelsWorker(QThread):
    finished = pyqtSignal(bool, list, str)

    def __init__(self, base_url: str, api_key: str, use_proxy: bool, proxy_url: str) -> None:
        super().__init__()
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.use_proxy = bool(use_proxy)
        self.proxy_url = str(proxy_url or "").strip()

    def run(self) -> None:
        local_cfg = {
            "base_url": self.base_url,
            "model_name": "",
            "api_key": self.api_key,
            "use_proxy": self.use_proxy,
            "proxy_url": self.proxy_url,
            "model_type": infer_translator_model_type("", {"base_url": self.base_url}),
        }
        gemini_active = False
        chatgpt_active = False
        try:
            try:
                from deepcat.local_gemini_web2api_server import ensure_server as ensure_gemini, release_server as release_gemini, identify_spec as identify_gemini
                if identify_gemini(local_cfg) is not None:
                    ensure_gemini(local_cfg)
                    gemini_active = True
            except Exception as e:
                self.finished.emit(False, [], f"启动内置 Gemini 代理服务失败: {e}")
                return
            try:
                from deepcat.local_chatgpt_web2api_server import ensure_server as ensure_chatgpt, release_server as release_chatgpt, identify_spec as identify_chatgpt
                if identify_chatgpt(local_cfg) is not None:
                    ensure_chatgpt(local_cfg)
                    chatgpt_active = True
            except Exception as e:
                self.finished.emit(False, [], f"启动本地 ChatGPT Web2API 服务失败: {e}")
                return

            headers = {
                "Accept": "application/json",
                "User-Agent": "DeepCat/1.0",
            }
            is_local_chatgpt = chatgpt_active
            if self.api_key and not is_local_chatgpt:
                headers["Authorization"] = f"Bearer {self.api_key}"
            if infer_translator_model_type("", {"base_url": self.base_url, "api_key": self.api_key}) == "anthropic":
                headers.pop("Authorization", None)
                if self.api_key:
                    headers["x-api-key"] = self.api_key
                headers["anthropic-version"] = "2023-06-01"

            proxies = {}
            if self.use_proxy and self.proxy_url:
                proxies = {"http": self.proxy_url, "https": self.proxy_url}

            last_status = None
            last_error = ""
            last_non_json = ""
            for url in self._candidate_model_urls():
                try:
                    resp = self._models_response(url, headers, proxies)
                except Exception as e:
                    last_error = str(e)
                    continue
                last_status = resp.status_code
                if resp.status_code != 200:
                    last_error = f"请求失败，HTTP 状态码: {resp.status_code}"
                    if resp.status_code == 401:
                        last_error = f"{last_error}\n请提供正确密钥。"
                    continue

                try:
                    data = resp.json()
                except ValueError:
                    preview = str(resp.text or "").strip().replace("\r", " ").replace("\n", " ")
                    if len(preview) > 120:
                        preview = preview[:120] + "..."
                    last_non_json = f"服务返回的不是 JSON 数据: {preview or '空响应'}"
                    last_error = last_non_json
                    continue
                models = []
                if isinstance(data, dict) and "data" in data:
                    data_items = data.get("data")
                    if isinstance(data_items, dict) and "models" in data_items:
                        data_items = data_items.get("models")
                    if isinstance(data_items, list):
                        for item in data_items:
                            if isinstance(item, dict) and "id" in item:
                                models.append(str(item["id"]))
                            elif isinstance(item, str):
                                models.append(item)
                elif isinstance(data, dict) and "models" in data:
                    for item in data["models"]:
                        if isinstance(item, dict):
                            model_id = str(item.get("id") or item.get("name") or "").strip()
                            if model_id.startswith("models/"):
                                model_id = model_id.split("/", 1)[1]
                            if model_id:
                                models.append(model_id)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            models.append(item)
                        elif isinstance(item, dict) and "id" in item:
                            models.append(str(item["id"]))

                models = [m.strip() for m in models if m.strip()]
                if models:
                    self.finished.emit(True, sorted(list(set(models))), "")
                    return
                else:
                    last_error = "服务器返回的模型列表为空"
                    continue

            if last_status is not None:
                self.finished.emit(False, [], last_error or f"请求失败，HTTP 状态码: {last_status}")
            else:
                self.finished.emit(False, [], last_error or last_non_json or "无法生成模型列表请求地址")
        except Exception as e:
            self.finished.emit(False, [], str(e))
        finally:
            if gemini_active:
                try:
                    release_gemini(local_cfg)  # type: ignore[name-defined]
                except Exception:
                    pass
            if chatgpt_active:
                try:
                    release_chatgpt(local_cfg)  # type: ignore[name-defined]
                except Exception:
                    pass

    def _candidate_model_urls(self) -> list[str]:
        from urllib.parse import urlsplit, urlunsplit

        raw = self.base_url.strip().rstrip("/")
        if not raw:
            return []

        parts = urlsplit(raw)
        path = parts.path.rstrip("/")
        candidates: list[str] = []

        def add(url: str) -> None:
            url = str(url or "").strip().rstrip("/")
            if url and url not in candidates:
                candidates.append(url)

        add(raw)
        if path.endswith("/models"):
            return candidates

        if path.endswith("/chat/completions"):
            base_path = path[: -len("/chat/completions")]
            add(urlunsplit((parts.scheme, parts.netloc, f"{base_path}/models", "", "")))
            return candidates
        if path.endswith("/messages"):
            base_path = path[: -len("/messages")]
            add(urlunsplit((parts.scheme, parts.netloc, f"{base_path}/models", "", "")))
            return candidates
        if path.endswith("/completions"):
            base_path = path[: -len("/completions")]
            add(urlunsplit((parts.scheme, parts.netloc, f"{base_path}/models", "", "")))
            return candidates
        if path.endswith("/responses"):
            base_path = path[: -len("/responses")]
            add(urlunsplit((parts.scheme, parts.netloc, f"{base_path}/models", "", "")))
            return candidates

        if path.endswith(("/v1", "/v4")):
            add(f"{raw}/models")
        else:
            if "generativelanguage.googleapis.com" in parts.netloc.lower():
                key = str(self.api_key or "").strip()
                suffix = f"?key={key}" if key else ""
                add(f"{raw}/v1beta/models{suffix}")
            add(f"{raw}/v1/models")
            add(f"{raw}/models")
        return candidates

    @staticmethod
    def _models_response(url: str, headers: dict, proxies: dict):
        """内置 Gemini 代理走进程内直调，其余地址走真实网络请求。"""
        from deepcat.local_gemini_web2api_server import local_gemini_response

        response = local_gemini_response("GET", url, headers=headers, proxies=proxies, timeout=8)
        if response is not None:
            return response
        import requests

        return requests.get(url, headers=headers, proxies=proxies, timeout=8)


class ProxyConnectionTestWorker(QThread):
    tested = pyqtSignal(bool, str, float)

    def __init__(self, proxy_url: str) -> None:
        super().__init__()
        self._proxy_url = str(proxy_url or "").strip()

    def run(self) -> None:
        from deepcat.ui.network_probe import (
            CLOUDFLARE_204_TARGET,
            GOOGLE_204_TARGET,
            NetworkProbeEngine,
            run_proxy_network_probe,
        )

        proxy_url = self._proxy_url
        if not proxy_url:
            self.tested.emit(False, "代理地址为空", 0.0)
            return

        engine = NetworkProbeEngine()
        try:
            google_result = run_proxy_network_probe(GOOGLE_204_TARGET, 2.5, proxy_url, engine=engine)
            if google_result.ok:
                self.tested.emit(True, "代理连通成功 (可访问外网)", float(google_result.route_elapsed_ms))
                return

            cloudflare_result = run_proxy_network_probe(CLOUDFLARE_204_TARGET, 2.5, proxy_url, engine=engine)
            if cloudflare_result.ok:
                self.tested.emit(
                    True,
                    "代理连通成功 (Google 异常，其他外网可访问)",
                    float(cloudflare_result.route_elapsed_ms),
                )
                return
        finally:
            engine.close()

        message = (
            f"测试 Google 204 失败: {google_result.error or '未知错误'}\n"
            f"测试 Cloudflare 204 失败: {cloudflare_result.error or '未知错误'}"
        )
        self.tested.emit(False, message, float(google_result.elapsed_ms + cloudflare_result.elapsed_ms))


class TranslatorConnectionTestWorker(QThread):
    tested = pyqtSignal(bool, str, float)

    def __init__(self, cfg: dict[str, object], use_proxy: bool, proxy_url: str) -> None:
        super().__init__()
        self._cfg = dict(cfg)
        self._use_proxy = bool(use_proxy)
        self._proxy_url = str(proxy_url or "")

    def _required(self, key: str) -> str:
        value = str(self._cfg.get(key, "") or "").strip()
        if not value:
            raise ValueError(f"缺少 {key}")
        return value

    def _proxies(self) -> Optional[dict[str, str]]:
        if not self._use_proxy:
            return None
        proxy_url = str(self._proxy_url or "").strip()
        if not proxy_url:
            return None
        if "://" not in proxy_url:
            proxy_url = f"socks5h://{proxy_url}"
        elif proxy_url.startswith("socks5://"):
            proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
        return {
            "http": proxy_url,
            "https": proxy_url,
            "socks5": proxy_url,
        }

    @staticmethod
    def _chatgpt_web2api_error_message(detail: object) -> str:
        raw = str(detail or "").strip()
        if not raw:
            return ""
        payload = None
        try:
            payload = json.loads(raw)
        except Exception:
            payload = None
        message = ""
        code = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                code = str(error.get("code") or "").strip()
            elif isinstance(error, str):
                message = error.strip()
        signal = f"{code}\n{message}\n{raw}".lower()
        network_markers = (
            "upstream_network_error",
            "curl: (28)",
            "curl: (35)",
            "connection timed out",
            "connect timed out",
            "read timed out",
            "failed to connect",
            "tls connect error",
            "proxy",
            "name resolution",
            "could not resolve",
        )
        if any(marker in signal for marker in network_markers):
            return "上游网络连接失败，请检查代理/网络后重试。"
        return message

    @staticmethod
    def _compact_test_error_detail(message: object) -> str:
        detail = str(message or "").strip()
        if not detail:
            return ""
        detail = detail.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in detail.split("\n") if line.strip()]
        compact = "；".join(lines[:3]) if lines else detail
        return compact[:420] + "..." if len(compact) > 420 else compact

    @staticmethod
    def _classify_test_failure(message: object) -> tuple[str, str]:
        detail = TranslatorConnectionTestWorker._compact_test_error_detail(message)
        lower = detail.lower()
        auth_markers = (
            "http 状态码: 401",
            "http 状态码: 403",
            "http 401",
            "http 403",
            "status code: 401",
            "status code: 403",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "invalid_api_key",
            "incorrect api key",
            "api key",
            "apikey",
            "permission denied",
            "无权限",
            "鉴权",
            "认证",
            "密钥无效",
        )
        model_markers = (
            "http 状态码: 404",
            "http 404",
            "status code: 404",
            "model not found",
            "model_not_found",
            "does not exist",
            "unknown model",
            "invalid model",
            "未返回目标模型",
            "模型不存在",
            "模型未找到",
            "not found",
        )
        timeout_markers = (
            "timeout",
            "timed out",
            "read timed out",
            "connect timed out",
            "connection timed out",
            "timeoutexpired",
            "10060",
            "超时",
        )
        if any(marker in lower for marker in timeout_markers):
            return "网络超时", "请求在限定时间内未返回，请检查网络、代理或上游服务状态。"
        if any(marker in lower for marker in auth_markers):
            return "鉴权失败", "API 密钥无效、已过期，或当前账号没有访问权限。"
        if any(marker in lower for marker in model_markers):
            return "模型不存在", "当前模型 ID 不存在，或该 API 地址/账号无权访问此模型。"
        if any(marker in lower for marker in ("connection refused", "failed to connect", "network", "proxy", "连接失败")):
            return "网络连接失败", "无法连接到 API 地址，请检查网络、代理和服务地址。"
        return "测试失败", "模型服务返回异常，请查看原始错误信息。"

    @staticmethod
    def _format_test_result(success: bool, message: object) -> str:
        if success:
            return "连通成功：模型可正常响应。"
        category, advice = TranslatorConnectionTestWorker._classify_test_failure(message)
        detail = TranslatorConnectionTestWorker._compact_test_error_detail(message)
        return f"{category}：{advice}" + (f"\n原始错误：{detail}" if detail else "")

    def _request(self, method: str, url: str, **kwargs):
        from deepcat.local_gemini_web2api_server import local_gemini_response

        inprocess = local_gemini_response(str(method).upper(), url, **kwargs)
        if inprocess is not None:
            return inprocess

        import requests

        # Determine if we should bypass proxy for local/loopback URLs
        bypass_proxy = False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            hostname = str(parsed.hostname or "").lower()
            if hostname in {"127.0.0.1", "localhost"}:
                bypass_proxy = True
        except Exception:
            pass

        explicit_proxies = None if bypass_proxy else self._proxies()
        kwargs.pop("proxies", None)
        if explicit_proxies is not None:
            return requests.request(str(method).upper(), url, proxies=explicit_proxies, **kwargs)
        try:
            # Set direct request timeout to 30 seconds
            direct_kwargs = kwargs.copy()
            direct_kwargs.setdefault("timeout", 30)
            return requests.request(str(method).upper(), url, proxies=None, **direct_kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            if bypass_proxy:
                raise
            proxy_url = str(self._proxy_url or "").strip()
            if not proxy_url:
                raise
            if "://" not in proxy_url:
                proxy_url = f"socks5h://{proxy_url}"
            elif proxy_url.startswith("socks5://"):
                proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
            return requests.request(
                str(method).upper(),
                url,
                proxies={
                    "http": proxy_url,
                    "https": proxy_url,
                    "socks5": proxy_url,
                },
                **kwargs,
            )

    def run(self) -> None:
        start = time.time()

        # 智能匹配并拉起对应的本地服务器进程 (腾讯本地模型或内置本地 Gemini 思考模型)
        from deepcat.local_hunyuan_server import ensure_server as ensure_hy, release_server as release_hy, identify_spec as identify_hy
        from deepcat.local_gemini_web2api_server import ensure_server as ensure_gemini, release_server as release_gemini, identify_spec as identify_gemini
        from deepcat.local_chatgpt_web2api_server import ensure_server as ensure_chatgpt, release_server as release_chatgpt, identify_spec as identify_chatgpt

        hy_active = False
        gemini_active = False
        chatgpt_active = False
        is_local_gemini = identify_gemini(self._cfg) is not None
        is_local_chatgpt = identify_chatgpt(self._cfg) is not None

        if identify_hy(self._cfg) is not None:
            try:
                ensure_hy(self._cfg)
                hy_active = True
            except Exception as e:
                self.tested.emit(False, f"启动腾讯本地模型服务失败: {e}", max(0.0, time.time() - start))
                return

        if is_local_gemini:
            try:
                ensure_gemini(self._cfg)
                gemini_active = True
            except Exception as e:
                self.tested.emit(False, f"启动内置 Gemini 代理服务失败: {e}", max(0.0, time.time() - start))
                return

        if is_local_chatgpt:
            try:
                ensure_chatgpt(self._cfg)
                chatgpt_active = True
            except Exception as e:
                self.tested.emit(False, f"启动本地 ChatGPT Web2API 服务失败: {e}", max(0.0, time.time() - start))
                return

        try:
            model_type = str(self._cfg.get("model_type", "") or "").lower()
            if is_local_gemini:
                self._test_local_gemini_web2api()
            elif is_local_chatgpt:
                self._test_local_chatgpt_web2api()
            elif model_type == "gemini":
                self._test_gemini()
            elif model_type == "microsoft_free":
                self._test_microsoft_free()
            elif model_type == "google_free":
                self._test_google_free()
            elif model_type == "deeplx":
                self._test_deeplx()
            elif model_type == "anthropic":
                self._test_anthropic()
            elif model_type == "openai_responses":
                self._test_openai_responses()
            elif model_type == "openai_images":
                self._test_openai_images()
            else:
                self._test_glm()
            self.tested.emit(True, self._format_test_result(True, ""), max(0.0, time.time() - start))
        except Exception as exc:
            self.tested.emit(False, self._format_test_result(False, str(exc)), max(0.0, time.time() - start))
        finally:
            if hy_active:
                try:
                    release_hy(self._cfg)
                except Exception:
                    pass
            if gemini_active:
                try:
                    release_gemini(self._cfg)
                except Exception:
                    pass
            if chatgpt_active:
                try:
                    release_chatgpt(self._cfg)
                except Exception:
                    pass

    def _test_gemini(self) -> None:
        import requests

        base_url = self._required("base_url").rstrip("/")
        model_name = self._required("model_name")
        api_key = self._required("api_key")
        response = self._request(
            "POST",
            f"{base_url}/v1beta/models/{model_name}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": "请只回复 OK，用于模型连通测试。"}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
            },
            timeout=15,
        )
        response.raise_for_status()

    def _base_url(self, default: str) -> str:
        return str(self._cfg.get("base_url", "") or default).strip().rstrip("/")

    def _local_gemini_web2api_root_url(self) -> str:
        from urllib.parse import urlparse

        raw = self._base_url("http://127.0.0.1:8081")
        parse_target = raw if "://" in raw else f"http://{raw}"
        parsed = urlparse(parse_target)
        host = str(parsed.hostname or "127.0.0.1")
        try:
            port = int(parsed.port or 8081)
        except ValueError:
            port = 8081
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{display_host}:{port}/"

    def _test_local_gemini_web2api(self) -> None:
        model_name = self._required("model_name")
        response = self._request("GET", self._local_gemini_web2api_root_url(), timeout=5)
        response.raise_for_status()
        try:
            result = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"本地 Gemini 代理已响应，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if not isinstance(result, dict) or str(result.get("status", "")).lower() not in {"ok", "ready"}:
            raise ValueError("本地 Gemini 代理未返回健康状态")
        models = result.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("本地 Gemini 代理未返回模型列表")
        model_ids = {str(item) for item in models}
        if model_name not in model_ids:
            raise ValueError(f"本地 Gemini 代理未返回目标模型: {model_name}")

    def _local_chatgpt_web2api_root_url(self) -> str:
        from urllib.parse import urlparse

        raw = self._base_url("http://127.0.0.1:8082")
        parse_target = raw if "://" in raw else f"http://{raw}"
        parsed = urlparse(parse_target)
        host = str(parsed.hostname or "127.0.0.1")
        try:
            port = int(parsed.port or 8082)
        except ValueError:
            port = 8082
        display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{display_host}:{port}/"

    def _test_local_chatgpt_web2api(self) -> None:
        import requests

        model_name = self._required("model_name")
        api_key = self._required("api_key")
        root_url = self._local_chatgpt_web2api_root_url()
        response = self._request("GET", root_url, timeout=5)
        response.raise_for_status()
        try:
            result = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"本地 ChatGPT Web2API 已响应，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if not isinstance(result, dict) or str(result.get("service", "")).lower() != "chatgpt_web2api":
            raise ValueError("本地 ChatGPT Web2API 未返回健康状态")
        models = result.get("models")
        if not isinstance(models, list) or not models:
            raise ValueError("本地 ChatGPT Web2API 未返回模型列表")
        try:
            from deepcat.chatgpt_web2api import normalize_model

            expected_model = normalize_model(model_name)
        except Exception:
            expected_model = model_name
        model_ids = {str(item) for item in models}
        if expected_model not in model_ids:
            raise ValueError(f"本地 ChatGPT Web2API 未返回目标模型: {model_name}")

        base_url = normalize_openai_chat_base_url(self._base_url("http://127.0.0.1:8082"))
        try:
            chat_response = self._request(
                "POST",
                f"{base_url}/chat/completions",
                headers={"Content-Type": "application/json"},
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "请只回复 OK，用于模型连通测试。"}],
                    "temperature": 0,
                    "max_tokens": 16,
                    "enable_conversation_append": False,
                    "upstream_auth_timeout_sec": 6,
                    "upstream_bootstrap_timeout_sec": 2,
                    "upstream_warmup_timeout_sec": 2,
                    "upstream_connect_timeout_sec": 6,
                    "upstream_timeout_sec": 12,
                },
                timeout=30,
            )
        except requests.exceptions.Timeout as exc:
            raise ValueError(
                "上游网络连接失败，请检查代理/网络后重试。本地 ChatGPT Web2API 服务未在限定时间内返回，"
                "通常是它仍在等待 chatgpt.com 上游连接。"
            ) from exc
        try:
            chat_response.raise_for_status()
        except Exception as exc:
            detail = str(getattr(chat_response, "text", "") or "").strip()
            friendly = self._chatgpt_web2api_error_message(detail)
            if friendly:
                raise ValueError(friendly) from exc
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            if detail:
                raise ValueError(f"{exc}: {detail}") from exc
            raise
        try:
            payload = chat_response.json()
        except Exception as exc:
            preview = str(getattr(chat_response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"模型连接成功，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if isinstance(payload, dict) and "error" in payload:
            friendly = self._chatgpt_web2api_error_message(json.dumps(payload, ensure_ascii=False))
            if friendly:
                raise ValueError(friendly)
            raise ValueError(f"模型服务返回错误: {payload.get('error')}")
        try:
            content = str(payload["choices"][0]["message"].get("content") or "").strip()
        except Exception as exc:
            raise ValueError("模型连接成功，但未返回 OpenAI 兼容内容") from exc
        if not content:
            raise ValueError("模型连接成功，但未返回有效内容")

    def _test_microsoft_free(self) -> None:
        import requests

        token_response = self._request(
            "GET",
            "https://edge.microsoft.com/translate/auth",
            timeout=15,
        )
        token_response.raise_for_status()
        token = token_response.text.strip()
        if not token:
            raise ValueError("微软翻译未返回授权令牌")
        response = self._request(
            "POST",
            f"{self._base_url('https://api-edge.cognitive.microsofttranslator.com')}/translate",
            params={"api-version": "3.0", "to": "zh-Hans"},
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json=[{"Text": "OK"}],
            timeout=15,
        )
        response.raise_for_status()

    def _test_google_free(self) -> None:
        import requests

        response = self._request(
            "GET",
            f"{self._base_url('https://translate.googleapis.com')}/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": "OK"},
            timeout=15,
        )
        response.raise_for_status()

    def _test_deeplx(self) -> None:
        import requests

        base_url = self._base_url("http://127.0.0.1:1188")
        if "linux.do/t/topic/111737" in base_url or not base_url.lower().startswith(("http://", "https://")):
            raise ValueError("请先根据 L站说明填写 DeepLX API 接口地址")
        url = base_url if base_url.endswith("/translate") else f"{base_url}/translate"
        response = self._request(
            "POST",
            url,
            headers={"Content-Type": "application/json"},
            json={"text": "OK", "source_lang": "AUTO", "target_lang": "ZH"},
            timeout=15,
        )
        response.raise_for_status()

    def _test_anthropic(self) -> None:
        import requests

        url = normalize_anthropic_messages_url(self._required("base_url"))
        model_name = self._required("model_name")
        api_key = self._required("api_key")
        response = self._request(
            "POST",
            url,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": model_name,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "请只回复 OK，用于模型连通测试。"}],
            },
            timeout=15,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            detail = str(getattr(response, "text", "") or "").strip()
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            if detail:
                raise ValueError(f"{exc}: {detail}") from exc
            raise
        try:
            result = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"模型连接成功，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if isinstance(result, dict) and "error" in result:
            raise ValueError(f"模型服务返回错误: {result.get('error')}")
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and str(part.get("text") or "").strip():
                    return
        raise ValueError("模型连接成功，但未返回有效内容")

    def _extract_openai_responses_text(self, result: object) -> str:
        if not isinstance(result, dict):
            return ""
        output_text = result.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        chunks: list[str] = []
        output = result.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "input_text", "text"}:
                        text = str(part.get("text") or "").strip()
                        if text:
                            chunks.append(text)
        return "".join(chunks).strip()

    def _test_openai_responses(self) -> None:
        import requests

        url = normalize_openai_responses_url(self._required("base_url"))
        model_name = self._required("model_name")
        api_key = self._required("api_key")
        response = self._request(
            "POST",
            url,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={
                "model": model_name,
                "input": "请只回复 OK，用于模型连通测试。",
                "temperature": 0,
                "max_output_tokens": 16,
            },
            timeout=15,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            detail = str(getattr(response, "text", "") or "").strip()
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            if detail:
                raise ValueError(f"{exc}: {detail}") from exc
            raise
        try:
            result = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"模型连接成功，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if isinstance(result, dict) and "error" in result:
            raise ValueError(f"模型服务返回错误: {result.get('error')}")
        if self._extract_openai_responses_text(result):
            return
        raise ValueError("模型连接成功，但未返回有效内容")

    def _test_openai_images(self) -> None:
        # 生图按次计费，连通测试不真正生图：用同源的 /models 端点验证地址与密钥
        from deepcat.settings_store import normalize_openai_images_url

        images_url = normalize_openai_images_url(self._required("base_url"))
        self._required("model_name")
        api_key = self._required("api_key")
        base_url = normalize_openai_chat_base_url(images_url)
        response = self._request(
            "GET",
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status in {401, 403}:
            detail = str(getattr(response, "text", "") or "").strip()
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            raise ValueError(f"API 密钥无效或无权限（HTTP {status}）：{detail}")
        if status in {404, 405}:
            # 部分中转不提供 /models，端点可达且未报鉴权错误即视为连通
            return
        response.raise_for_status()

    def _test_glm(self) -> None:
        import requests

        base_url = normalize_openai_chat_base_url(self._required("base_url"))
        model_name = self._required("model_name")
        api_key = self._required("api_key")
        response = self._request(
            "POST",
            f"{base_url}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "请只回复 OK，用于模型连通测试。"}],
                "temperature": 0,
                "max_tokens": 16,
            },
            timeout=15,
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            detail = str(getattr(response, "text", "") or "").strip()
            if len(detail) > 300:
                detail = f"{detail[:300]}..."
            if detail:
                raise ValueError(f"{exc}: {detail}") from exc
            raise
        try:
            result = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", "") or "").strip()
            if len(preview) > 300:
                preview = f"{preview[:300]}..."
            raise ValueError(f"模型连接成功，但返回的不是 JSON 数据: {preview or '空响应'}") from exc
        if isinstance(result, dict) and "error" in result:
            raise ValueError(f"模型服务返回错误: {result.get('error')}")
        try:
            message = result["choices"][0]["message"]
            content = str(message.get("content") or "").strip()
            if not content:
                # 推理模型在 max_tokens 较小时可能把全部 token 用于思维链，
                # content 为空但 reasoning_content 有内容，同样视为连通成功
                content = str(message.get("reasoning_content") or "").strip()
        except Exception:
            content = ""
        if not content:
            raise ValueError("模型连接成功，但未返回有效内容")


class UpdateCheckWorker(QThread):
    checked = pyqtSignal(object, str)

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._current_version = str(current_version or "")

    def run(self) -> None:
        try:
            from deepcat.updater import check_latest_release

            self.checked.emit(check_latest_release(self._current_version), "")
        except Exception as exc:
            self.checked.emit(None, str(exc))


class UpdateDownloadWorker(QThread):
    progress = pyqtSignal(int, int)
    downloaded = pyqtSignal(object, str)

    def __init__(self, info: object) -> None:
        super().__init__()
        self._info = info

    def run(self) -> None:
        try:
            from deepcat.updater import download_update

            path = download_update(self._info, lambda done, total: self.progress.emit(int(done), int(total)))  # type: ignore[arg-type]
            self.downloaded.emit(path, "")
        except Exception as exc:
            self.downloaded.emit(None, str(exc))


class ModelDownloadWorker(QThread):
    progress = pyqtSignal(int)
    download_finished = pyqtSignal(bool, str)

    def __init__(self, url: str, dest_path: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.dest_path = dest_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        temp_dest = None
        try:
            dest = Path(self.dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp_dest = dest.with_suffix(".tmp")

            import urllib.request
            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                chunk_size = 512 * 1024  # 512KB chunks

                with open(temp_dest, "wb") as f:
                    while not self._is_cancelled:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int(downloaded * 100 / total_size)
                            percent = max(0, min(100, percent))
                            self.progress.emit(percent)

            if self._is_cancelled:
                if temp_dest and temp_dest.exists():
                    try:
                        temp_dest.unlink()
                    except Exception:
                        pass
                self.download_finished.emit(False, "Cancelled")
            else:
                if dest.exists():
                    try:
                        dest.unlink()
                    except Exception:
                        pass
                temp_dest.rename(dest)
                self.download_finished.emit(True, "")
        except Exception as e:
            if temp_dest and temp_dest.exists():
                try:
                    temp_dest.unlink()
                except Exception:
                    pass
            self.download_finished.emit(False, str(e))
