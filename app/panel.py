from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from playwright.async_api import Locator
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import Settings

LOGGER = logging.getLogger(__name__)


class LazyCatPanelClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def restart_instance(self, instance_name: str | None) -> dict[str, object]:
        if not self._settings.lazycat_email or not self._settings.lazycat_password:
            raise RuntimeError("未配置 LAZYCAT_EMAIL / LAZYCAT_PASSWORD，无法自动登录懒猫云。")

        self._settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._settings.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=self._settings.playwright_headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            context = await browser.new_context(
                storage_state=str(self._settings.storage_state_path)
                if self._settings.storage_state_path.exists()
                else None,
            )
            context.set_default_timeout(self._settings.browser_timeout_ms)
            page = await context.new_page()
            self._register_dialog_handler(page)

            try:
                await self._ensure_logged_in(page)
                target_page, resolved_instance = await self._open_target_panel(
                    page,
                    instance_name or self._settings.lazycat_target_hostname or None,
                )
                await self._power_cycle(target_page)
                await context.storage_state(path=str(self._settings.storage_state_path))
                screenshot = await self._capture_screenshot(target_page, f"{timestamp}-after-restart")
                return {
                    "base_url": self._settings.lazycat_base_url,
                    "resolved_instance_name": resolved_instance,
                    "final_url": target_page.url,
                    "storage_state_path": str(self._settings.storage_state_path),
                    "artifact_path": screenshot,
                }
            except Exception:
                await self._capture_screenshot(page, f"{timestamp}-failure")
                raise
            finally:
                await context.close()
                await browser.close()

    async def _ensure_logged_in(self, page: Page) -> None:
        await page.goto(self._abs_url(self._settings.lazycat_clientarea_path), wait_until="domcontentloaded")
        if await self._is_login_page(page):
            LOGGER.info("检测到登录态失效，开始重新登录。")
            await self._login(page)
            await page.goto(self._abs_url(self._settings.lazycat_clientarea_path), wait_until="domcontentloaded")
            if await self._is_login_page(page):
                raise RuntimeError("登录后仍停留在登录页，请检查账号密码或站点是否要求额外验证。")

    async def _login(self, page: Page) -> None:
        await page.goto(self._abs_url(self._settings.lazycat_login_path), wait_until="domcontentloaded")
        await page.locator("#emailInp").fill(self._settings.lazycat_email)
        await page.locator("#emailPwdInp").fill(self._settings.lazycat_password)
        await page.locator("#loginButton").click()
        await page.wait_for_load_state("networkidle")

    async def _open_target_panel(self, page: Page, instance_name: str | None) -> tuple[Page, str | None]:
        await page.goto(self._abs_url(self._settings.lazycat_clientarea_path), wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")

        panel_page = await self._click_panel_entry_if_present(page)
        if panel_page is not None:
            return panel_page, instance_name

        resolved_instance = await self._open_instance_detail(page, instance_name)
        panel_page = await self._click_panel_entry_if_present(page)
        if panel_page is None:
            raise RuntimeError("已进入实例详情页，但没有找到“进入面板”按钮，请补充选择器配置。")
        return panel_page, resolved_instance

    async def _open_instance_detail(self, page: Page, instance_name: str | None) -> str | None:
        for selector in self._settings.service_link_selectors:
            locator = await self._find_first_visible_by_selector(page, selector)
            if locator is not None:
                text = await self._safe_text(locator)
                await locator.click()
                await page.wait_for_load_state("networkidle")
                return text or instance_name

        if instance_name:
            locator = await self._find_clickable_by_text(page, (instance_name,))
            if locator is None:
                raise RuntimeError(f"资源列表中未找到实例名: {instance_name}")
            resolved_name = await self._safe_text(locator) or instance_name
            await locator.click()
            await page.wait_for_load_state("networkidle")
            return resolved_name

        candidates = await self._collect_service_link_candidates(page)
        if len(candidates) != 1:
            raise RuntimeError(
                "当前未配置 LAZYCAT_TARGET_HOSTNAME，且未能唯一定位实例。候选项: "
                + ", ".join(candidate["text"] for candidate in candidates[:10]),
            )

        await candidates[0]["locator"].click()
        await page.wait_for_load_state("networkidle")
        return candidates[0]["text"]

    async def _click_panel_entry_if_present(self, page: Page) -> Page | None:
        locator = await self._find_clickable_by_rules(
            page,
            selectors=self._settings.enter_panel_selectors,
            texts=self._settings.enter_panel_texts,
        )
        if locator is None:
            return None

        before_pages = list(page.context.pages)
        await locator.click()
        await asyncio.sleep(1)
        after_pages = [item for item in page.context.pages if item not in before_pages]
        if after_pages:
            panel_page = after_pages[-1]
            self._register_dialog_handler(panel_page)
            await panel_page.wait_for_load_state("networkidle")
            return panel_page

        await page.wait_for_load_state("networkidle")
        return page

    async def _power_cycle(self, page: Page) -> None:
        stop_locator = await self._find_clickable_by_rules(
            page,
            selectors=self._settings.stop_button_selectors,
            texts=self._settings.stop_button_texts,
        )
        if stop_locator is None:
            raise RuntimeError("实例面板中未找到停止按钮，请补充停止按钮选择器。")
        await stop_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await asyncio.sleep(self._settings.stop_wait_seconds)

        start_locator = await self._find_clickable_by_rules(
            page,
            selectors=self._settings.start_button_selectors,
            texts=self._settings.start_button_texts,
        )
        if start_locator is None:
            raise RuntimeError("实例面板中未找到启动按钮，请补充启动按钮选择器。")
        await start_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await page.wait_for_load_state("networkidle")

    async def _click_confirm_if_present(self, page: Page) -> None:
        locator = await self._find_clickable_by_rules(
            page,
            selectors=self._settings.confirm_button_selectors,
            texts=self._settings.confirm_button_texts,
        )
        if locator is not None:
            await locator.click()

    async def _find_clickable_by_rules(
        self,
        page: Page,
        *,
        selectors: Iterable[str],
        texts: Iterable[str],
    ) -> Locator | None:
        for selector in selectors:
            locator = await self._find_first_visible_by_selector(page, selector)
            if locator is not None:
                return locator
        return await self._find_clickable_by_text(page, texts)

    async def _find_first_visible_by_selector(self, page: Page, selector: str) -> Locator | None:
        for frame in page.frames:
            locator = frame.locator(selector)
            count = await locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible():
                    return candidate
        return None

    async def _find_clickable_by_text(
        self,
        page: Page,
        texts: Iterable[str],
    ) -> Locator | None:
        for text in texts:
            regex = re.compile(re.escape(text), re.IGNORECASE)
            for frame in page.frames:
                candidates = (
                    frame.get_by_role("button", name=regex),
                    frame.get_by_role("link", name=regex),
                    frame.get_by_role("tab", name=regex),
                    frame.get_by_text(regex),
                )
                for locator in candidates:
                    count = await locator.count()
                    for index in range(count):
                        candidate = locator.nth(index)
                        if await candidate.is_visible():
                            return candidate
        return None

    async def _collect_service_link_candidates(self, page: Page) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for frame in page.frames:
            links = frame.locator("a[href]")
            count = min(await links.count(), 200)
            for index in range(count):
                locator = links.nth(index)
                if not await locator.is_visible():
                    continue
                text = await self._safe_text(locator)
                href = (await locator.get_attribute("href")) or ""
                if not text or not href:
                    continue
                normalized = (text, href)
                if normalized in seen:
                    continue
                seen.add(normalized)
                lower_text = text.casefold()
                if any(skip.casefold() in lower_text for skip in self._settings.service_link_skip_texts):
                    continue
                if href.startswith("javascript:"):
                    continue
                if href.startswith("http") and not href.startswith(self._settings.lazycat_base_url):
                    continue
                if href.rstrip("/") in {
                    self._settings.lazycat_base_url,
                    self._abs_url(self._settings.lazycat_login_path),
                    self._abs_url(self._settings.lazycat_clientarea_path),
                }:
                    continue
                candidates.append({"text": text, "href": href, "locator": locator})
        return candidates

    async def _capture_screenshot(self, page: Page, name: str) -> str:
        output = self._settings.artifact_dir / f"{name}.png"
        try:
            await page.screenshot(path=str(output), full_page=True)
            return str(output)
        except Exception:  # noqa: BLE001
            LOGGER.exception("截图失败: %s", output)
            return str(output)

    async def _is_login_page(self, page: Page) -> bool:
        if self._settings.lazycat_login_path in page.url:
            return True
        try:
            return await page.locator("#loginButton").is_visible()
        except PlaywrightTimeoutError:
            return False

    async def _safe_text(self, locator: Locator) -> str:
        try:
            text = await locator.inner_text()
        except Exception:  # noqa: BLE001
            return ""
        return " ".join(text.split()).strip()

    def _register_dialog_handler(self, page: Page) -> None:
        async def accept_dialog(dialog) -> None:
            await dialog.accept()

        page.on("dialog", lambda dialog: asyncio.create_task(accept_dialog(dialog)))

    def _abs_url(self, path: str) -> str:
        return f"{self._settings.lazycat_base_url}{path if path.startswith('/') else '/' + path}"
