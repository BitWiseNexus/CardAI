"""Dev helper: screenshot the frontend for visual verification."""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173"
    out = sys.argv[2] if len(sys.argv) > 2 else "ui_screenshot.png"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1200)  # let entrance animations settle
        await page.screenshot(path=out, full_page=False)
        await browser.close()
    print(f"saved {out}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
