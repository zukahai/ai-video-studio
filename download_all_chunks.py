import asyncio
import re
from pathlib import Path
from muse_ai.auth import MuseAuth

async def main():
    all_scripts = set()
    for fn in ['page_dump.html', 'settings.html', 'subscriptions.html']:
        if Path(fn).exists():
            t = Path(fn).read_text(encoding='utf-8')
            all_scripts.update(re.findall(r'src="(/_next/static/chunks/[^"?]+)', t))
            all_scripts.update(re.findall(r'"(/_next/static/chunks/[^"?]+)', t))

    print(f"Total script chunks to check: {len(all_scripts)}")

    auth = MuseAuth()
    await auth.load_cookies()

    Path("chunks").mkdir(exist_ok=True)
    for s in all_scripts:
        fname = s.split("/")[-1]
        local_path = Path("chunks") / fname
        if not local_path.exists():
            try:
                resp = await auth.browser.get(f"https://muse.ai{s}")
                if resp.status_code == 200:
                    local_path.write_text(resp.text, encoding="utf-8")
            except Exception as e:
                pass

    print("Finished downloading chunks. Searching...")
    keywords = [
        "settings.subscription.usage",
        "settings_subscription_usage",
        "Weekly limit resets on",
        "Additional tokens",
        "Never expires",
        "Giới hạn hàng tuần sẽ đặt lại vào",
        "Token bổ sung",
        "Không bao giờ hết hạn",
        "Đã dùng"
    ]
    for p in Path("chunks").glob("*.js"):
        c = p.read_text(encoding="utf-8", errors="ignore")
        found = [k for k in keywords if k in c]
        if found:
            print(f"{p.name} -> {found}")

    await auth.close()

if __name__ == "__main__":
    asyncio.run(main())
