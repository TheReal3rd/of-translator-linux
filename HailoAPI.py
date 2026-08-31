import json
import logging
from typing import List, Tuple

import requests

logger = logging.getLogger(__name__)

# Compatible with Ollama /api/chat and /api/generate, plus Hailo-hosted chat APIs.


def _base_url(server_ip: str, port: int) -> str:
    return f"http://{server_ip}:{port}"


def _parse_error(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except ValueError:
        pass

    text = (response.text or "").strip()
    if text:
        return text
    return f"HTTP {response.status_code}"


def _is_missing_endpoint(response: requests.Response) -> bool:
    if response.status_code != 404:
        return False

    text = (response.text or "").strip().lower()
    if "model" in text and "not found" in text:
        return False

    return text in ("404 page not found", "not found", "") or "page not found" in text


def _collect_stream(response: requests.Response) -> str:
    result = ""

    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue

        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue

        if "message" in chunk:
            content = chunk["message"].get("content", "")
            if content:
                result += content
        elif "response" in chunk:
            result += chunk["response"]

        if chunk.get("done"):
            break

    return result.strip()


def _stream_post(url: str, payload: dict, timeout: int) -> str:
    headers = {"Content-Type": "application/json"}

    with requests.post(
        url, json=payload, headers=headers, stream=True, timeout=timeout
    ) as response:
        if not response.ok:
            error = _parse_error(response)
            response.reason = error
            response.raise_for_status()
        return _collect_stream(response)


def send_chat_stream(
    server_ip: str,
    model: str,
    message: str,
    port: int = 8000,
    timeout: int = 60,
) -> str:
    base = _base_url(server_ip, port)

    attempts = [
        (
            f"{base}/api/chat",
            {
                "model": model,
                "messages": [{"role": "user", "content": message}],
                "stream": True,
            },
        ),
        (
            f"{base}/api/generate",
            {
                "model": model,
                "prompt": message,
                "stream": True,
            },
        ),
    ]

    last_error = "Unknown error"

    for url, payload in attempts:
        try:
            return _stream_post(url, payload, timeout)
        except requests.exceptions.HTTPError as e:
            response = e.response
            last_error = _parse_error(response) if response is not None else str(e)

            if response is not None and _is_missing_endpoint(response):
                logger.debug("Endpoint unavailable (%s), trying fallback", url)
                continue

            logger.error("Translation request failed: %s", last_error)
            return ""
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            logger.error("Translation request failed: %s", last_error)
            return ""

    logger.error("Translation request failed: %s", last_error)
    return ""


def ping_server(server_ip: str, port: int, timeout: int = 3) -> Tuple[bool, str]:
    url = f"{_base_url(server_ip, port)}/api/tags"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        count = len(response.json().get("models", []))
        return True, f"Connected ({count} model{'s' if count != 1 else ''} found)"
    except requests.exceptions.ConnectionError:
        return False, "Connection refused — check IP and port"
    except requests.exceptions.Timeout:
        return False, "Timed out — server not responding"
    except requests.exceptions.HTTPError as e:
        return False, _parse_error(e.response)
    except requests.exceptions.RequestException as e:
        return False, str(e)
    except (ValueError, KeyError):
        return False, "Connected but response was not valid JSON"


def list_models(server_ip: str, port: int, timeout: int = 5) -> List[str]:
    url = f"{_base_url(server_ip, port)}/api/tags"
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        models = []
        for entry in response.json().get("models", []):
            name = entry.get("name") or entry.get("model")
            if name:
                models.append(name)
        return sorted(set(models))
    except requests.exceptions.RequestException:
        return []
