"""Dev helper: drive a chat in the UI and screenshot the conversation."""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "Compare Chase cards with no annual fee in a table"
    out = sys.argv[2] if len(sys.argv) > 2 else "chat_screenshot.png"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto("http://localhost:5173", wait_until="networkidle")
        region = sys.argv[3] if len(sys.argv) > 3 else None
        if region:
            await page.click(f"button:has-text('{region}')")
            await page.wait_for_timeout(300)
        await page.fill("textarea", query)
        await page.keyboard.press("Enter")
        # Wait until streaming finishes (stop button replaced by send button)
        await page.wait_for_selector("button[title='Send (Enter)']", timeout=120_000)
        await page.wait_for_timeout(800)
        await page.screenshot(path=out, full_page=False)
        await browser.close()
    print(f"saved {out}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
