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
            context = await browser.new_context(**self._build_context_options())
            context.set_default_timeout(self._settings.browser_timeout_ms)
            page = await context.new_page()
            self._register_dialog_handler(page)

            try:
                await self._ensure_logged_in(page)
                target_page, resolved_instance = await self._open_target_panel(
                    page,
                    instance_name or self._settings.lazycat_target_hostname or None,
                )
                await self._power_cycle(target_page, fallback_page=page)
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

    def _build_context_options(self) -> dict[str, object]:
        options: dict[str, object] = {"ignore_https_errors": True}
        if self._settings.storage_state_path.exists():
            options["storage_state"] = str(self._settings.storage_state_path)
        return options

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
        await page.locator("#emailInp").wait_for(state="visible")
        await page.locator("#emailInp").fill(self._settings.lazycat_email)
        await page.locator("#emailPwdInp").fill(self._settings.lazycat_password)
        await page.locator("#loginButton").click()
        await self._wait_for_login_completion(page)

    async def _open_target_panel(self, page: Page, instance_name: str | None) -> tuple[Page, str | None]:
        await page.goto(self._abs_url(self._settings.lazycat_clientarea_path), wait_until="domcontentloaded")
        await self._wait_for_page_settle(page)

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
                await self._wait_for_page_settle(page)
                return text or instance_name

        if instance_name:
            locator = await self._wait_for_clickable_by_rules(
                page,
                selectors=tuple(),
                texts=(instance_name,),
                timeout_ms=10000,
            )
            if locator is None:
                raise RuntimeError(f"资源列表中未找到实例名: {instance_name}")
            resolved_name = await self._safe_text(locator) or instance_name
            await locator.click()
            await self._wait_for_page_settle(page)
            return resolved_name

        candidates = await self._collect_service_link_candidates(page)
        if len(candidates) != 1:
            raise RuntimeError(
                "当前未配置 LAZYCAT_TARGET_HOSTNAME，且未能唯一定位实例。候选项: "
                + ", ".join(candidate["text"] for candidate in candidates[:10]),
            )

        await candidates[0]["locator"].click()
        await self._wait_for_page_settle(page)
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
            await self._wait_for_page_settle(panel_page)
            return panel_page

        await self._wait_for_page_settle(page)
        return page

    async def _power_cycle(self, page: Page, fallback_page: Page | None = None) -> None:
        stop_locator = await self._wait_for_clickable_by_rules(
            page,
            selectors=self._settings.stop_button_selectors,
            texts=self._settings.stop_button_texts,
        )
        if stop_locator is None:
            if fallback_page is not None and fallback_page is not page:
                LOGGER.warning(
                    "面板页未找到停止按钮，回退到产品详情页控制菜单。panel_url=%s detail_url=%s",
                    page.url,
                    fallback_page.url,
                )
                if await self._try_power_cycle_from_service_detail(fallback_page):
                    return
            raise RuntimeError(
                "实例面板中未找到停止按钮，请补充停止按钮选择器。"
                f" 当前页面: {await self._describe_page(page)}",
            )
        await stop_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await asyncio.sleep(self._settings.stop_wait_seconds)

        start_locator = await self._wait_for_clickable_by_rules(
            page,
            selectors=self._settings.start_button_selectors,
            texts=self._settings.start_button_texts,
        )
        if start_locator is None:
            raise RuntimeError(
                "实例面板中未找到启动按钮，请补充启动按钮选择器。"
                f" 当前页面: {await self._describe_page(page)}",
            )
        await start_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await self._wait_for_page_settle(page)

    async def _click_confirm_if_present(self, page: Page) -> None:
        locator = await self._wait_for_clickable_by_rules(
            page,
            selectors=self._settings.confirm_button_selectors,
            texts=self._settings.confirm_button_texts,
            timeout_ms=2500,
        )
        if locator is not None:
            await locator.click()

    async def _try_power_cycle_from_service_detail(self, page: Page) -> bool:
        control_button = await self._wait_for_clickable_by_rules(
            page,
            selectors=tuple(),
            texts=("控制",),
            timeout_ms=5000,
        )
        if control_button is None:
            return False

        await control_button.click()
        await asyncio.sleep(0.5)

        stop_locator = await self._wait_for_clickable_by_rules(
            page,
            selectors=self._settings.stop_button_selectors,
            texts=self._settings.stop_button_texts,
            timeout_ms=5000,
        )
        if stop_locator is None:
            return False

        await stop_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await asyncio.sleep(self._settings.stop_wait_seconds)

        await control_button.click()
        await asyncio.sleep(0.5)

        start_locator = await self._wait_for_clickable_by_rules(
            page,
            selectors=self._settings.start_button_selectors,
            texts=self._settings.start_button_texts,
            timeout_ms=5000,
        )
        if start_locator is None:
            return False

        await start_locator.click()
        await asyncio.sleep(1)
        await self._click_confirm_if_present(page)
        await self._wait_for_page_settle(page)
        return True

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

    async def _wait_for_clickable_by_rules(
        self,
        page: Page,
        *,
        selectors: Iterable[str],
        texts: Iterable[str],
        timeout_ms: int | None = None,
        poll_interval_ms: int = 500,
    ) -> Locator | None:
        deadline = asyncio.get_running_loop().time() + (
            (timeout_ms or self._settings.browser_timeout_ms) / 1000
        )
        while True:
            locator = await self._find_clickable_by_rules(
                page,
                selectors=selectors,
                texts=texts,
            )
            if locator is not None:
                return locator
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(poll_interval_ms / 1000)

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

    async def _has_logged_in_marker(self, page: Page) -> bool:
        if self._settings.lazycat_login_path not in page.url and self._settings.lazycat_base_url in page.url:
            try:
                if await page.locator("#page-header-user-dropdown").is_visible():
                    return True
            except Exception:  # noqa: BLE001
                pass

            try:
                if await page.locator("a[href*='servicedetail?id=']").count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass

            try:
                if await page.locator("a[href*='logout']").count() > 0:
                    return True
            except Exception:  # noqa: BLE001
                pass

        return False

    async def _wait_for_login_completion(self, page: Page) -> None:
        deadline = asyncio.get_running_loop().time() + (self._settings.browser_timeout_ms / 1000)
        while True:
            if await self._has_logged_in_marker(page):
                return

            if not await self._is_login_page(page):
                await self._wait_for_page_settle(page)
                if await self._has_logged_in_marker(page):
                    return

            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(
                    "登录后等待页面完成超时。"
                    f" 当前页面: {await self._describe_page(page)}"
                )

            await asyncio.sleep(0.5)

    async def _wait_for_page_settle(self, page: Page, timeout_ms: int = 10000) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass
        await asyncio.sleep(1)

    async def _safe_text(self, locator: Locator) -> str:
        try:
            text = await locator.inner_text()
        except Exception:  # noqa: BLE001
            return ""
        return " ".join(text.split()).strip()

    async def _describe_page(self, page: Page) -> str:
        title = ""
        try:
            title = await page.title()
        except Exception:  # noqa: BLE001
            title = ""

        text = ""
        try:
            text = await page.locator("body").inner_text(timeout=2000)
        except Exception:  # noqa: BLE001
            text = ""

        summary = " ".join(text.split())[:200]
        return f"url={page.url}, title={title}, text={summary}"

    def _register_dialog_handler(self, page: Page) -> None:
        async def accept_dialog(dialog) -> None:
            await dialog.accept()

        page.on("dialog", lambda dialog: asyncio.create_task(accept_dialog(dialog)))

    def _abs_url(self, path: str) -> str:
        return f"{self._settings.lazycat_base_url}{path if path.startswith('/') else '/' + path}"
