"""
Web Scraper - 爬取公開網頁內容
使用 httpx（非同步 HTTP）+ BeautifulSoup（HTML 解析）
"""
import logging
from typing import Literal

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TIMEOUT = 30.0  # 秒


async def fetch_url(url: str, timeout: float = TIMEOUT) -> str:
    """
    發送 HTTP GET 請求，回傳 HTML 純文字內容。
    會自動跟隨最多 5 次跳轉。
    """
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        max_redirects=5,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def extract_text(html: str, selector: str | None = None) -> str:
    """
    用 BeautifulSoup 取出文字。
    - selector 不給：回傳 <body> 純文字
    - selector 給 CSS 選擇器：只取符合的元素
    """
    soup = BeautifulSoup(html, "lxml")

    # 移除 script / style / nav / footer 噪音標籤
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    if selector:
        elements = soup.select(selector)
        return "\n\n".join(el.get_text(separator="\n", strip=True) for el in elements)
    else:
        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)
        return soup.get_text(separator="\n", strip=True)


def extract_links(html: str, base_url: str | None = None) -> list[dict]:
    """
    取出所有連結，回傳格式：[{"text": "...", "href": "..."}]
    base_url 會做 href 絕對路徑拼接。
    """
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if href.startswith("/") and base_url:
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        if href.startswith("http"):
            links.append({"text": text or href, "href": href})
    return links


async def scrape(
    url: str,
    selector: str | None = None,
    extract_links_flag: bool = False,
    timeout: float = TIMEOUT,
) -> dict:
    """
    主爬蟲函式。

    Args:
        url: 目標網址
        selector: CSS 選擇器，只取符合區塊（選填）
        extract_links_flag: 是否一併回傳連結清單（預設 False）
        timeout: 請求逾時（秒）

    Returns:
        {
            "url": "...",
            "status_code": 200,
            "title": "...",
            "text": "...",
            "links": [...]  (if extract_links_flag)
        }
    """
    try:
        html = await fetch_url(url, timeout)

        # 取 title
        soup = BeautifulSoup(html, "lxml")
        title = soup.find("title")
        title_text = title.get_text(strip=True) if title else ""

        # 主文字
        text = extract_text(html, selector)

        result: dict = {
            "url": url,
            "status_code": 200,
            "title": title_text,
            "text": text,
        }

        if extract_links_flag:
            result["links"] = extract_links(html, url)

        return result

    except httpx.HTTPStatusError as e:
        logger.warning(f"HTTP 錯誤 {e.response.status_code}：{url}")
        return {
            "url": url,
            "error": f"HTTP {e.response.status_code}",
            "text": "",
        }
    except httpx.RequestError as e:
        logger.warning(f"連線錯誤：{url} — {e}")
        return {
            "url": url,
            "error": f"連線失敗：{e}",
            "text": "",
        }
    except Exception as e:
        logger.exception(f"爬蟲未知錯誤：{url}")
        return {
            "url": url,
            "error": str(e),
            "text": "",
        }
